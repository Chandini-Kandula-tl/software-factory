"""OpenAI-compatible adapter (provider type ``openai_compatible``).

One adapter for every server that speaks the widely used Chat Completions
format: local engines (LM Studio, llama.cpp server, vLLM, Ollama's /v1) and
cloud APIs (DeepSeek, OpenAI, Groq, OpenRouter, ...). The factory's own
``LLMRequest``/``LLMResponse`` stay the domain contract; this module only
translates.

Config::

    providers:
      deepseek:
        type: openai_compatible
        base_url: https://api.deepseek.com/v1     # the part before /chat/completions
        api_key_env: DEEPSEEK_API_KEY             # omit for local servers without auth
        options:
          structured_output: json_object          # json_schema (default) | json_object | prompt

``structured_output`` picks how a JSON answer is requested: ``json_schema``
sends the schema (strict servers), ``json_object`` asks for any JSON object
(servers without schema support), ``prompt`` relies on the instructions only.
The router validates the answer against the schema in every case.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from software_factory.config.settings import ProviderConfig
from software_factory.domain.errors import ConfigurationError
from software_factory.llm.errors import LLMError, LLMErrorKind
from software_factory.llm.models import (
    LLMRequest,
    LLMResponse,
    ModelMetadata,
    ProviderHealth,
    TokenUsage,
)
from software_factory.llm.providers.base import read_api_key

STRUCTURED_MODES = ("json_schema", "json_object", "prompt")


class OpenAICompatibleProvider:
    def __init__(
        self, name: str, config: ProviderConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not config.base_url:
            raise ConfigurationError(
                f"Provider '{name}' needs a base_url (e.g. https://api.openai.com/v1)"
            )
        self.name = name
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._timeout = config.timeout_seconds
        self._transport = transport
        self._structured = str(config.options.get("structured_output", "json_schema"))
        if self._structured not in STRUCTURED_MODES:
            raise ConfigurationError(
                f"providers.{name}.options.structured_output must be one of {STRUCTURED_MODES}"
            )

    def _headers(self) -> dict[str, str]:
        # Read at call time only; never stored, logged or persisted.
        key = read_api_key(self.name, self._config)
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=timeout if timeout is not None else self._timeout,
            transport=self._transport,
            headers=self._headers(),
        )

    # -- generation ---------------------------------------------------------

    def generate(self, request: LLMRequest, model: str, options: dict) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
            **options,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        if request.structured_output_schema is not None:
            if self._structured == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "factory_output",
                        "schema": request.structured_output_schema,
                    },
                }
            elif self._structured == "json_object":
                body["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        data = self._post("/chat/completions", body, model)
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                LLMErrorKind.MALFORMED_RESPONSE,
                "Response has no choices[0].message.content",
                provider=self.name,
                model=model,
            ) from exc
        if not isinstance(content, str):
            raise LLMError(
                LLMErrorKind.MALFORMED_RESPONSE,
                "Message content is not text",
                provider=self.name,
                model=model,
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            model_profile=request.model_profile,
            latency_ms=latency_ms,
            usage=TokenUsage(
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            ),
        )

    def _post(self, path: str, body: dict[str, Any], model: str) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(
                LLMErrorKind.TIMEOUT,
                f"Request timed out after {self._timeout}s",
                provider=self.name,
                model=model,
                hint="Increase providers.<name>.timeout_seconds or use a smaller model.",
            ) from exc
        except httpx.ConnectError as exc:
            raise LLMError(
                LLMErrorKind.PROVIDER_UNAVAILABLE,
                f"Cannot reach {self._base_url}",
                provider=self.name,
                model=model,
                hint="Check base_url and that the server is running / reachable.",
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(LLMErrorKind.NETWORK, str(exc), provider=self.name, model=model) from exc
        if response.status_code >= 400:
            raise self._http_error(response, model)
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(
                LLMErrorKind.MALFORMED_RESPONSE,
                "Response body is not JSON",
                provider=self.name,
                model=model,
            ) from exc
        if not isinstance(data, dict):
            raise LLMError(
                LLMErrorKind.MALFORMED_RESPONSE,
                "Response body is not a JSON object",
                provider=self.name,
                model=model,
            )
        return data

    def _http_error(self, response: httpx.Response, model: str) -> LLMError:
        detail, code = response.text, ""
        try:
            error = response.json().get("error")
            if isinstance(error, dict):
                detail = str(error.get("message", detail))
                code = str(error.get("code") or error.get("type") or "")
            elif error:
                detail = str(error)
        except ValueError:
            pass
        status = response.status_code
        lowered = f"{detail} {code}".lower()
        if status in (401, 403):
            return LLMError(
                LLMErrorKind.AUTHENTICATION,
                f"HTTP {status}: {detail}",
                provider=self.name,
                model=model,
                hint=f"Check the API key in {self._config.api_key_env or 'the environment'}.",
            )
        if status == 404 or "model_not_found" in lowered or "does not exist" in lowered:
            return LLMError(
                LLMErrorKind.MODEL_UNAVAILABLE,
                f"Model '{model}' is not available: {detail}",
                provider=self.name,
                model=model,
                hint="Check the model name in config against the server's model list.",
            )
        if status == 429:
            kind = LLMErrorKind.RATE_LIMIT
        elif "context" in lowered and ("length" in lowered or "window" in lowered):
            kind = LLMErrorKind.CONTEXT_LENGTH_EXCEEDED
        elif status >= 500:
            kind = LLMErrorKind.PROVIDER_UNAVAILABLE
        else:
            kind = LLMErrorKind.MALFORMED_RESPONSE
        return LLMError(kind, f"HTTP {status}: {detail}", provider=self.name, model=model)

    # -- health & metadata --------------------------------------------------

    def _list_models(self) -> list[dict[str, Any]]:
        with self._client(timeout=10.0) as client:
            response = client.get("/models")
            response.raise_for_status()
            return [m for m in response.json().get("data", []) if isinstance(m, dict)]

    def health_check(self) -> ProviderHealth:
        try:
            models = self._list_models()
        except httpx.ConnectError:
            return ProviderHealth(
                provider=self.name, healthy=False, detail=f"Cannot reach {self._base_url}"
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = (
                "authentication failed — check the API key"
                if status in (401, 403)
                else f"HTTP {status} from {self._base_url}/models"
            )
            return ProviderHealth(provider=self.name, healthy=False, detail=detail)
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            detail=f"{len(models)} model(s) available",
            available_models=[str(m.get("id", "")) for m in models],
        )

    def get_model_metadata(self, model: str) -> ModelMetadata:
        try:
            models = {str(m.get("id")): m for m in self._list_models()}
        except (httpx.HTTPError, ValueError):
            return ModelMetadata(model=model, available=False)
        entry = models.get(model)
        if entry is None:
            return ModelMetadata(model=model, available=False)
        context = entry.get("context_length") or entry.get("max_context_length")
        return ModelMetadata(
            model=model,
            available=True,
            context_length=context if isinstance(context, int) else None,
            family=entry.get("owned_by"),
        )
