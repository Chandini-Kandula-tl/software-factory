"""Helpers shared by workflow nodes: execution records and failure handling."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from software_factory.agents.base import AgentResult, LLMAgent, llm_calls_from_error
from software_factory.domain.errors import FactoryError
from software_factory.domain.execution import (
    ExecutionKind,
    ExecutionRecord,
    ExecutionStatus,
    LLMCallRecord,
)
from software_factory.domain.lifecycle import LifecycleStatus, transition
from software_factory.observability.clock import new_execution_id, utc_now
from software_factory.observability.redaction import redact
from software_factory.orchestration.ports import WorkflowDependencies


class Timer:
    def __init__(self) -> None:
        self.started_at = utc_now()
        self._start = time.monotonic()

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


def execution_record(
    deps: WorkflowDependencies,
    state: Mapping[str, Any],
    *,
    kind: ExecutionKind,
    stage: str,
    status: ExecutionStatus,
    timer: Timer | None = None,
    summary: str = "",
    error: str | None = None,
    llm_calls: list[LLMCallRecord] | None = None,
    **fields: Any,
) -> ExecutionRecord:
    calls = llm_calls or []
    return ExecutionRecord(
        execution_id=new_execution_id(),
        project_id=state["project_id"],
        kind=kind,
        stage=stage,
        status=status,
        started_at=timer.started_at if timer else utc_now(),
        completed_at=utc_now(),
        duration_ms=timer.elapsed_ms if timer else None,
        llm_calls=calls,
        retry_count=max(len(calls) - 1, 0),
        factory_version=deps.factory_version,
        workflow_version=deps.workflow_version,
        config_fingerprint=deps.config_fingerprint,
        summary=summary,
        error=redact(error) if error else None,
        **fields,
    )


def failure_update(
    state: Mapping[str, Any],
    *,
    stage: str,
    error: Exception,
    execution_id: str,
) -> dict[str, Any]:
    """Move a working stage to FAILED and remember where to resume on retry."""
    current = LifecycleStatus(state["lifecycle_status"])
    kind = getattr(error, "kind", None) or type(error).__name__
    hint = error.hint if isinstance(error, FactoryError) else None
    message = redact(str(error.args[0]) if error.args else str(error))
    entry = {
        "timestamp": utc_now(),
        "stage": stage,
        "error_kind": str(kind),
        "message": message,
        "hint": hint,
        "execution_id": execution_id,
    }
    return {
        "lifecycle_status": transition(current, LifecycleStatus.FAILED).value,
        "failure": {**entry, "resume_status": current.value},
        "errors": [*state.get("errors", []), entry],
        "last_execution_id": execution_id,
    }


def fail_stage(
    deps: WorkflowDependencies,
    state: Mapping[str, Any],
    *,
    stage: str,
    error: Exception,
    kind: ExecutionKind = ExecutionKind.WORKFLOW,
    timer: Timer | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Record a failed step in history and move the project to FAILED."""
    record = execution_record(
        deps,
        state,
        kind=kind,
        stage=stage,
        status=ExecutionStatus.FAILED,
        timer=timer,
        error=str(error),
        summary=f"{stage} failed",
        **fields,
    )
    deps.recorder.record_execution(record)
    return failure_update(state, stage=stage, error=error, execution_id=record.execution_id)


def invoke_agent(
    deps: WorkflowDependencies,
    state: Mapping[str, Any],
    agent: LLMAgent[Any],
    *,
    stage: str,
    available: dict[str, Any],
    revision_feedback: str | None = None,
) -> tuple[AgentResult[Any] | None, dict[str, Any]]:
    """Run an agent and record it. Returns (result, {}) or (None, failure update)."""
    timer = Timer()
    spec = agent.spec
    try:
        context = agent.build_context(
            state["project_id"],
            state["project_name"],
            stage,
            available,
            revision_feedback=revision_feedback,
        )
        result = agent.run(context)
    except FactoryError as error:
        return None, fail_stage(
            deps,
            state,
            stage=stage,
            error=error,
            kind=ExecutionKind.AGENT,
            timer=timer,
            agent=spec.name,
            agent_version=spec.version,
            model_profile=spec.llm_profile,
            llm_calls=llm_calls_from_error(error, spec.llm_profile),
        )
    record = execution_record(
        deps,
        state,
        kind=ExecutionKind.AGENT,
        stage=stage,
        status=ExecutionStatus.SUCCESS,
        timer=timer,
        agent=spec.name,
        agent_version=spec.version,
        model_profile=result.model_profile,
        provider=result.provider,
        model=result.model,
        prompt_versions=result.prompt_versions,
        llm_calls=result.llm_calls,
        summary=f"{spec.name} completed ({stage})",
        details={"warnings": result.warnings},
    )
    deps.recorder.record_execution(record)
    return result, {"last_execution_id": record.execution_id}
