"""Agent contract shared by every agent.

Each agent declares: input keys (its context), input/output schema, LLM
profile, allowed tools, prompt location, and deterministic output validation.
Agents never touch workflow state, persistence, or providers directly — the
orchestrator hands them an ``AgentContext`` and decides what to do with the
``AgentResult``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from software_factory.agents.prompts import PromptLibrary
from software_factory.domain.errors import AgentOutputError
from software_factory.domain.execution import LLMCallRecord
from software_factory.llm.interfaces import LLMClient
from software_factory.llm.models import LLMRequest, LLMResponse, Message, MessageRole
from software_factory.tools.base import ToolName

OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    version: str
    purpose: str
    llm_profile: str
    prompt_dir: str
    output_schema: type[BaseModel]
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...] = ()
    allowed_tools: tuple[ToolName, ...] = ()
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def input_keys(self) -> tuple[str, ...]:
        return self.required_inputs + self.optional_inputs


class AgentContext(BaseModel):
    """Only the information an agent needs — never the whole project."""

    project_id: str
    project_name: str
    stage: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    revision_feedback: str | None = None


class AgentResult(BaseModel, Generic[OutputT]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    agent: str
    agent_version: str
    output: OutputT
    model_profile: str
    provider: str
    model: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Agent(Protocol):
    spec: ClassVar[AgentSpec]

    def run(self, context: AgentContext) -> AgentResult: ...


class LLMAgent(Generic[OutputT]):
    """Base for agents that reason with an LLM and return a validated schema."""

    spec: ClassVar[AgentSpec]
    # When the deterministic checks in validate_output reject an answer, the
    # model gets this many chances to correct it (with the exact reason).
    validation_repairs: ClassVar[int] = 1

    def __init__(self, llm: LLMClient, prompts: PromptLibrary) -> None:
        self._llm = llm
        self._prompts = prompts

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def capabilities(self) -> list[str]:
        return list(self.spec.capabilities)

    def build_context(
        self,
        project_id: str,
        project_name: str,
        stage: str,
        available: dict[str, Any],
        revision_feedback: str | None = None,
    ) -> AgentContext:
        """Select only this agent's declared inputs from what is available."""
        return AgentContext(
            project_id=project_id,
            project_name=project_name,
            stage=stage,
            inputs={k: available[k] for k in self.spec.input_keys if available.get(k) is not None},
            revision_feedback=revision_feedback,
        )

    def run(self, context: AgentContext) -> AgentResult[OutputT]:
        missing = [
            k for k in self.spec.required_inputs if context.inputs.get(k) in (None, "", [], {})
        ]
        if missing:
            raise AgentOutputError(f"{self.spec.name} is missing required inputs: {missing}")

        system = self._prompts.load(self.spec.prompt_dir, "system.md")
        task = self._prompts.load(self.spec.prompt_dir, "task.md")
        request = LLMRequest(
            model_profile=self.spec.llm_profile,
            messages=[
                Message(role=MessageRole.SYSTEM, content=system.render()),
                Message(role=MessageRole.USER, content=task.render(**self._render_values(context))),
            ],
            metadata={"project_id": context.project_id, "agent": self.spec.name},
        )
        calls: list[LLMCallRecord] = []
        for attempt in range(self.validation_repairs + 1):
            response = self._llm.generate_structured(request, self.spec.output_schema)
            calls += _call_records(response)
            output = response.parsed
            try:
                warnings = self.validate_output(output, context)
                break
            except AgentOutputError as error:
                if attempt == self.validation_repairs:
                    error.calls = calls  # type: ignore[attr-defined]
                    raise
                request = request.model_copy(
                    update={
                        "messages": [
                            *request.messages,
                            Message(role=MessageRole.ASSISTANT, content=response.content),
                            Message(
                                role=MessageRole.USER,
                                content=f"Your answer failed the factory's checks: {error}. "
                                "Return the corrected JSON object only.",
                            ),
                        ]
                    }
                )
        return AgentResult[OutputT](
            agent=self.spec.name,
            agent_version=self.spec.version,
            output=output,
            model_profile=self.spec.llm_profile,
            provider=response.provider,
            model=response.model,
            prompt_versions={system.key: system.version, task.key: task.version},
            llm_calls=calls,
            warnings=warnings,
        )

    def _render_values(self, context: AgentContext) -> dict[str, str]:
        values = {
            "project_name": context.project_name,
            "revision_feedback": context.revision_feedback
            or "None — this is the first proposal for this stage.",
        }
        for key in self.spec.input_keys:
            value = context.inputs.get(key)
            if value is None:
                values[key] = "None."
            elif isinstance(value, str):
                values[key] = value
            else:
                values[key] = json.dumps(value, indent=2, ensure_ascii=False)
        return values

    def validate_output(self, output: OutputT, context: AgentContext) -> list[str]:
        """Deterministic post-checks. Return warnings; raise AgentOutputError to fail."""
        return []


def _call_records(response: LLMResponse) -> list[LLMCallRecord]:
    return [
        LLMCallRecord(
            provider=call.provider,
            model=call.model,
            model_profile=response.model_profile,
            status=call.status,
            attempt=call.attempt,
            latency_ms=call.latency_ms,
            input_tokens=call.usage.input_tokens,
            output_tokens=call.usage.output_tokens,
            error_kind=call.error_kind,
            error=call.error,
        )
        for call in response.calls
    ]


def llm_calls_from_error(error: Exception, profile: str) -> list[LLMCallRecord]:
    """Recover per-attempt records from a failed LLM call, for history."""
    return [
        LLMCallRecord(
            provider=call.provider,
            model=call.model,
            model_profile=profile,
            status=call.status,
            attempt=call.attempt,
            latency_ms=call.latency_ms,
            error_kind=call.error_kind,
            error=call.error,
        )
        for call in getattr(error, "calls", [])
    ]
