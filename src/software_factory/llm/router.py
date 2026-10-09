"""Model router — the factory's ``LLMClient`` implementation.

Resolves a logical profile to configured models, validates capabilities,
retries transient failures, falls back to capability-compatible models, and
validates structured output against a schema (with bounded repair attempts).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from software_factory.config.settings import LimitsConfig
from software_factory.llm.errors import FALLBACK_KINDS, LLMError, LLMErrorKind
from software_factory.llm.interfaces import SchemaT
from software_factory.llm.models import (
    Capability,
    LLMRequest,
    LLMResponse,
    Message,
    MessageRole,
    ProviderCall,
)
from software_factory.llm.registry import ModelRegistry

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _final_error(error: LLMError, calls: list[ProviderCall]) -> LLMError:
    """Copy of ``error`` carrying every provider attempt, for execution history."""
    final = LLMError(error.kind, error.args[0], error.provider, error.model, error.hint)
    final.calls = calls
    return final


class ModelRouter:
    def __init__(
        self,
        registry: ModelRegistry,
        limits: LimitsConfig,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._registry = registry
        self._limits = limits
        self._sleep = sleep

    # -- LLMClient ----------------------------------------------------------

    def supports(self, capability: Capability | str, profile: str | None = None) -> bool:
        profiles = [profile] if profile else self._registry.profile_names
        return any(self._registry.candidates(name, {capability}) for name in profiles)

    def generate(self, request: LLMRequest) -> LLMResponse:
        return self._run(request, required=set(), finish=None)

    def generate_structured(self, request: LLMRequest, schema: type[SchemaT]) -> LLMResponse:
        json_schema = schema.model_json_schema()
        structured = request.model_copy(
            update={
                "structured_output_schema": json_schema,
                "messages": [
                    *request.messages,
                    Message(
                        role=MessageRole.USER,
                        content="Respond with a single JSON object that validates against "
                        "this JSON schema. Output JSON only, no prose.\n" + json.dumps(json_schema),
                    ),
                ],
            }
        )
        return self._run(structured, required={Capability.STRUCTURED_OUTPUT}, finish=schema)

    # -- internals ----------------------------------------------------------

    def _run(
        self,
        request: LLMRequest,
        required: set[Capability | str],
        finish: type[BaseModel] | None,
    ) -> LLMResponse:
        profile = self._registry.profile(request.model_profile)
        candidates = self._registry.candidates(request.model_profile, required)
        if not candidates:
            missing = self._registry.missing_capabilities(request.model_profile, required)
            raise _final_error(
                LLMError(
                    LLMErrorKind.UNSUPPORTED_CAPABILITY,
                    f"No enabled model for profile '{request.model_profile}' supports "
                    f"{sorted(missing) or sorted(str(c) for c in required)}",
                    hint="Declare the capability on a model in config or add a "
                    "capability-compatible fallback model to the profile.",
                ),
                [],
            )
        if request.temperature is None:
            request = request.model_copy(update={"temperature": profile.temperature})

        calls: list[ProviderCall] = []
        last_error: LLMError | None = None
        down_providers: set[str] = set()
        for model_key in candidates:
            provider_name = self._registry.model(model_key).provider
            if provider_name in down_providers:
                continue  # same server is unreachable; another model on it won't help
            try:
                response = self._call_with_retries(model_key, request, calls)
                if finish is not None:
                    response = self._validate_structured(
                        model_key, request, response, finish, calls
                    )
                response.calls = calls
                return response
            except LLMError as error:
                last_error = error
                if error.kind not in FALLBACK_KINDS:
                    break
                if error.kind in (LLMErrorKind.PROVIDER_UNAVAILABLE, LLMErrorKind.NETWORK):
                    down_providers.add(provider_name)
                logger.warning(
                    "model %s failed (%s); trying fallback if any", model_key, error.kind
                )
        assert last_error is not None
        raise _final_error(last_error, calls) from last_error

    def _call_with_retries(
        self, model_key: str, request: LLMRequest, calls: list[ProviderCall]
    ) -> LLMResponse:
        model = self._registry.model(model_key)
        attempts = 1 + self._limits.max_agent_retries
        for attempt in range(1, attempts + 1):
            try:
                provider = self._registry.provider_for(model_key)
                response = provider.generate(request, model.model, dict(model.options))
            except LLMError as error:
                calls.append(
                    ProviderCall(
                        provider=model.provider,
                        model=model.model,
                        attempt=attempt,
                        status="failed",
                        error_kind=str(error.kind),
                        error=str(error.args[0]),
                    )
                )
                if not error.retryable or attempt == attempts:
                    raise
                self._sleep(self._limits.retry_backoff_seconds * attempt)
                continue
            calls.append(
                ProviderCall(
                    provider=response.provider,
                    model=response.model,
                    attempt=attempt,
                    status="success",
                    latency_ms=response.latency_ms,
                    usage=response.usage,
                )
            )
            return response
        raise AssertionError("unreachable")

    def _validate_structured(
        self,
        model_key: str,
        request: LLMRequest,
        response: LLMResponse,
        schema: type[BaseModel],
        calls: list[ProviderCall],
    ) -> LLMResponse:
        messages = list(request.messages)
        for repair in range(self._limits.max_structured_output_repairs + 1):
            try:
                response.parsed = schema.model_validate(_extract_json(response.content))
                return response
            except (ValueError, ValidationError) as exc:
                problem = _describe_validation_error(exc)
                calls[-1].status = "invalid_output"
                calls[-1].error_kind = str(LLMErrorKind.INVALID_STRUCTURED_OUTPUT)
                calls[-1].error = problem
                if repair == self._limits.max_structured_output_repairs:
                    raise LLMError(
                        LLMErrorKind.INVALID_STRUCTURED_OUTPUT,
                        f"Output did not match {schema.__name__}: {problem}",
                        provider=response.provider,
                        model=response.model,
                    ) from exc
                messages += [
                    Message(role=MessageRole.ASSISTANT, content=response.content),
                    Message(
                        role=MessageRole.USER,
                        content="Your previous response failed validation: "
                        f"{problem}\nReturn the corrected JSON object only.",
                    ),
                ]
                response = self._call_with_retries(
                    model_key, request.model_copy(update={"messages": messages}), calls
                )
        raise AssertionError("unreachable")


def _extract_json(content: str) -> object:
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("response contains no JSON object")
        text = text[start : end + 1]
    return json.loads(text)


def _describe_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()[:8]
        ]
        return "; ".join(parts)
    return str(exc)
