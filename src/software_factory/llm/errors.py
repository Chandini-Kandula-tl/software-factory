"""Normalized LLM failure types.

Providers map their own errors onto ``LLMErrorKind`` so retry/fallback policy
is written once, in the router, and never per provider.
"""

from __future__ import annotations

from enum import StrEnum

from software_factory.domain.errors import FactoryError


class LLMErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    TOOL_CALL_FAILURE = "tool_call_failure"
    NETWORK = "network"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CONFIGURATION = "configuration"


# Worth retrying against the same model.
RETRYABLE_KINDS: frozenset[LLMErrorKind] = frozenset(
    {
        LLMErrorKind.TIMEOUT,
        LLMErrorKind.RATE_LIMIT,
        LLMErrorKind.PROVIDER_UNAVAILABLE,
        LLMErrorKind.NETWORK,
        LLMErrorKind.MALFORMED_RESPONSE,
    }
)

# Worth trying a capability-compatible fallback model after retries run out.
FALLBACK_KINDS: frozenset[LLMErrorKind] = RETRYABLE_KINDS | {
    LLMErrorKind.MODEL_UNAVAILABLE,
    LLMErrorKind.AUTHENTICATION,
    LLMErrorKind.CONTEXT_LENGTH_EXCEEDED,
    LLMErrorKind.INVALID_STRUCTURED_OUTPUT,
}


class LLMError(FactoryError):
    def __init__(
        self,
        kind: LLMErrorKind,
        message: str,
        provider: str | None = None,
        model: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind
        self.provider = provider
        self.model = model
        # Every provider attempt made before this error was final (for history).
        self.calls: list = []

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    def __str__(self) -> str:
        where = "/".join(part for part in (self.provider, self.model) if part)
        prefix = f"[{self.kind}]" + (f" {where}:" if where else "")
        return f"{prefix} {self.args[0]}"
