"""Factory-owned, provider-neutral LLM request/response types.

These are the only LLM types agents and the workflow ever see. Provider
adapters translate them to and from each provider's wire format.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Capability(StrEnum):
    REASONING = "reasoning"
    CODING = "coding"
    FAST = "fast"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    STREAMING = "streaming"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    role: MessageRole
    content: str


class LLMRequest(BaseModel):
    messages: list[Message]
    model_profile: str = "reasoning"
    temperature: float | None = None
    # JSON schema the response must satisfy. Set by generate_structured.
    structured_output_schema: dict[str, Any] | None = None
    max_output_tokens: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderCall(BaseModel):
    """Outcome of a single provider attempt, kept for execution history."""

    provider: str
    model: str
    attempt: int
    status: str
    latency_ms: int | None = None
    error_kind: str | None = None
    error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)


class LLMResponse(BaseModel):
    content: str
    provider: str
    model: str
    model_profile: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int | None = None
    # Populated by generate_structured after schema validation.
    parsed: Any = None
    calls: list[ProviderCall] = Field(default_factory=list)


class ProviderHealth(BaseModel):
    provider: str
    healthy: bool
    detail: str = ""
    available_models: list[str] = Field(default_factory=list)


class ModelMetadata(BaseModel):
    model: str
    available: bool
    parameter_size: str | None = None
    quantization: str | None = None
    context_length: int | None = None
    size_bytes: int | None = None
    family: str | None = None
