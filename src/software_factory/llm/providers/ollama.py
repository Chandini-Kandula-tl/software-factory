"""Ollama adapter (local model server).

Uses Ollama's native HTTP API: ``/api/chat`` for generation (with ``format``
set to a JSON schema for structured output), ``/api/tags`` for health and
installed models, and ``/api/show`` for model metadata.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from software_factory.config.settings import ProviderConfig
from software_factory.llm.errors import LLMError, LLMErrorKind
from software_factory.llm.models import (
    LLMRequest,
    LLMResponse,
    ModelMetadata,
    ProviderHealth,
    TokenUsage,
)

DEFAULT_BASE_URL = "http://localhost:11434"

# Model options that are top-level request fields in Ollama rather than
# entries of the ``options`` object.
_TOP_LEVEL_OPTIONS = {"think", "keep_alive"}


class OllamaProvider:
    def __init__(
        self, name: str, config: ProviderConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.name = name
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = config.timeout_seconds
        self._transport = transport

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            timeout=timeout if timeout is not None else self._timeout,
            transport=self._transport,
        )

    # -- generation ---------------------------------------------------------

    def generate(self, request: LLMRequest, model: str, options: dict) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
            "stream": False,
        }
        model_options = {k: v for k, v in options.items() if k not in _TOP_LEVEL_OPTIONS}
        for key in _TOP_LEVEL_OPTIONS & options.keys():
            body[key] = options[key]
        if request.temperature is not None:
            model_options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            model_options["num_predict"] = request.max_output_tokens
        if model_options:
            body["options"] = model_options
        if request.structured_output_schema is not None:
            body["format"] = request.structured_output_schema

        started = time.monotonic()
        data = self._post("/api/chat", body, model)
        latency_ms = int((time.monotonic() - started) * 1000)

        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise LLMError(
                LLMErrorKind.MALFORMED_RESPONSE,
                "Response has no message content",
                provider=self.name,
                model=model,
            )
        return LLMResponse(
            content=message["content"],
            provider=self.name,
            model=model,
            model_profile=request.model_profile,
            latency_ms=latency_ms,
            usage=TokenUsage(
                input_tokens=data.get("prompt_eval_count"),
                output_tokens=data.get("eval_count"),
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
                f"Cannot reach Ollama at {self._base_url}",
                provider=self.name,
                model=model,
                hint="Start the server with `ollama serve` and check base_url.",
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
        try:
            detail = str(response.json().get("error", response.text))
        except ValueError:
            detail = response.text
        status = response.status_code
        lowered = detail.lower()
        if status == 404 or ("model" in lowered and "not found" in lowered):
            return LLMError(
                LLMErrorKind.MODEL_UNAVAILABLE,
                f"Model '{model}' is not available: {detail}",
                provider=self.name,
                model=model,
                hint=f"Run `ollama pull {model}` or change the model in config.",
            )
        if status in (401, 403):
            kind = LLMErrorKind.AUTHENTICATION
        elif status == 429:
            kind = LLMErrorKind.RATE_LIMIT
        elif "context" in lowered and ("length" in lowered or "window" in lowered):
            kind = LLMErrorKind.CONTEXT_LENGTH_EXCEEDED
        elif status >= 500:
            kind = LLMErrorKind.PROVIDER_UNAVAILABLE
        else:
            kind = LLMErrorKind.MALFORMED_RESPONSE
        return LLMError(kind, f"HTTP {status}: {detail}", provider=self.name, model=model)

    # -- health & metadata --------------------------------------------------

    def _installed_models(self) -> list[dict[str, Any]]:
        with self._client(timeout=5.0) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            return list(response.json().get("models", []))

    def health_check(self) -> ProviderHealth:
        try:
            models = self._installed_models()
        except httpx.ConnectError:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                detail=f"Cannot reach Ollama at {self._base_url} — start it with `ollama serve`",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(exc))
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            detail=f"{len(models)} model(s) installed",
            available_models=[m.get("name", "") for m in models],
        )

    def get_model_metadata(self, model: str) -> ModelMetadata:
        try:
            installed = {m.get("name"): m for m in self._installed_models()}
        except (httpx.HTTPError, ValueError):
            return ModelMetadata(model=model, available=False)
        entry = installed.get(model) or installed.get(f"{model}:latest")
        if entry is None:
            return ModelMetadata(model=model, available=False)
        details = entry.get("details") or {}
        metadata = ModelMetadata(
            model=model,
            available=True,
            parameter_size=details.get("parameter_size"),
            quantization=details.get("quantization_level"),
            family=details.get("family"),
            size_bytes=entry.get("size"),
        )
        try:
            with self._client(timeout=10.0) as client:
                response = client.post("/api/show", json={"model": model})
                response.raise_for_status()
                model_info = response.json().get("model_info") or {}
        except (httpx.HTTPError, ValueError):
            return metadata
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                metadata.context_length = value
                break
        return metadata
