"""A deterministic ``ModelProvider`` for tests.

Registered as provider type ``scripted``. Each agent (identified by the
``agent`` request metadata) gets a queue of outputs; a queue item may be a
dict (returned as JSON), a raw string, or an exception to raise.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any

from software_factory.config.settings import ProviderConfig
from software_factory.llm.errors import LLMError, LLMErrorKind
from software_factory.llm.models import LLMRequest, LLMResponse, ModelMetadata, ProviderHealth


class Script:
    def __init__(self) -> None:
        self.queues: dict[str, deque[Any]] = defaultdict(deque)
        self.requests: list[tuple[str, LLMRequest]] = []

    def add(self, agent: str, *items: Any) -> Script:
        self.queues[agent].extend(items)
        return self

    def calls_for(self, agent: str) -> list[LLMRequest]:
        return [req for model, req in self.requests if req.metadata.get("agent") == agent]


SCRIPT = Script()


class ScriptedProvider:
    def __init__(self, name: str, config: ProviderConfig) -> None:
        self.name = name

    def generate(self, request: LLMRequest, model: str, options: dict) -> LLMResponse:
        SCRIPT.requests.append((model, request))
        agent = request.metadata.get("agent", "probe")
        queue = SCRIPT.queues[agent]
        if not queue:
            # Behaves like an unavailable model, so the workflow fails in a controlled way.
            raise LLMError(LLMErrorKind.MODEL_UNAVAILABLE, f"No scripted response for {agent}")
        item = queue.popleft()
        if isinstance(item, BaseException):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            model_profile=request.model_profile,
            latency_ms=1,
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True, detail="scripted")

    def get_model_metadata(self, model: str) -> ModelMetadata:
        return ModelMetadata(model=model, available=True)
