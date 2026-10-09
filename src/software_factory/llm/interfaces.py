"""The two LLM boundaries of the factory.

``LLMClient`` is what agents depend on. ``ModelProvider`` is what adapters
implement. Nothing outside ``llm/providers`` knows which provider is in use.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from software_factory.llm.models import (
    Capability,
    LLMRequest,
    LLMResponse,
    ModelMetadata,
    ProviderHealth,
)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@runtime_checkable
class LLMClient(Protocol):
    """Agent-facing interface. Selection, retries and fallback live behind it."""

    def generate(self, request: LLMRequest) -> LLMResponse: ...

    def generate_structured(self, request: LLMRequest, schema: type[SchemaT]) -> LLMResponse:
        """Return a response whose ``parsed`` field is a validated ``schema`` instance."""
        ...

    def supports(self, capability: Capability | str, profile: str | None = None) -> bool: ...


@runtime_checkable
class ModelProvider(Protocol):
    """Adapter contract every provider implements (local or cloud)."""

    name: str

    def generate(self, request: LLMRequest, model: str, options: dict) -> LLMResponse:
        """Execute one request against ``model``. Raise ``LLMError`` on failure."""
        ...

    def health_check(self) -> ProviderHealth: ...

    def get_model_metadata(self, model: str) -> ModelMetadata: ...
