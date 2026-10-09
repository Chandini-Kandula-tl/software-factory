"""Workflow nodes that need no LLM: project loading and repository analysis."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from software_factory.domain.errors import FactoryError
from software_factory.domain.execution import ExecutionKind, ExecutionStatus
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.lifecycle import transition
from software_factory.domain.project import ProjectMode
from software_factory.domain.quality_gates import require_entry
from software_factory.orchestration.nodes.common import (
    Timer,
    execution_record,
    failure_update,
    invoke_agent,
)
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.orchestration.state import FactoryState


def make_load_project(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def load_project(state: FactoryState) -> dict[str, Any]:
        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.WORKFLOW,
            stage="start",
            status=ExecutionStatus.INFO,
            summary=f"Workflow {deps.workflow_version} started "
            f"({state.get('project_mode', ProjectMode.GREENFIELD)} project)",
        )
        deps.recorder.record_execution(record)
        return {
            "lifecycle_status": transition(
                state.get("lifecycle_status", S.CREATED), S.REQUIREMENTS_ANALYSIS
            ).value,
            "last_execution_id": record.execution_id,
        }

    return load_project


def make_analyze_repository(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def analyze_repository(state: FactoryState) -> dict[str, Any]:
        timer = Timer()
        completed = list(state.get("completed_stages") or [])
        if "repository_analysis" not in completed:
            completed.append("repository_analysis")
        repository_path = state.get("repository_path")
        if state.get("project_mode") != ProjectMode.EXISTING.value:
            repository_path = None
        if state.get("update_request"):  # updating a released app: analyse its code
            repository_path = (state.get("development") or {}).get("source_path")

        try:
            require_entry("repository_analysis", state)
            if not repository_path:
                analysis = None
                summary = "Greenfield project: repository analysis skipped"
            else:
                analysis = deps.analyze_repository(Path(repository_path))
                summary = (
                    f"Analyzed {analysis.total_files_scanned} files; "
                    f"languages: {', '.join(analysis.languages) or 'none'}"
                )
        except (FactoryError, OSError) as error:
            record = execution_record(
                deps,
                state,
                kind=ExecutionKind.TOOL,
                stage="repository_analysis",
                status=ExecutionStatus.FAILED,
                timer=timer,
                error=str(error),
                summary="Repository analysis failed",
            )
            deps.recorder.record_execution(record)
            return failure_update(
                state, stage="repository_analysis", error=error, execution_id=record.execution_id
            )

        update: dict[str, Any] = {
            "repository": analysis.model_dump(mode="json") if analysis else None,
            "completed_stages": completed,
            "lifecycle_status": transition(
                state["lifecycle_status"], S.ARCHITECTURE_ANALYSIS
            ).value,
            "failure": None,
        }
        if analysis:
            deps.recorder.write_stage_artifact("repository_analysis", analysis, {**state, **update})
            # Spec §68.7: new requirement on existing software → impact analysis first.
            result, agent_update = invoke_agent(
                deps,
                state,
                deps.agents.impact_analysis,
                stage="repository_analysis",
                available={
                    "mode": "existing_project",
                    "requirements": state.get("requirements"),
                    "repository_analysis": analysis.model_dump(mode="json"),
                },
            )
            if result is None:
                return agent_update
            update["impact_analysis"] = result.output.model_dump(mode="json")
            update["warnings"] = [
                *state.get("warnings", []),
                *(f"impact-analysis-agent: {w}" for w in result.warnings),
            ]
            deps.recorder.write_stage_artifact(
                "impact_analysis", result.output, {**state, **update}
            )
        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.TOOL,
            stage="repository_analysis",
            status=ExecutionStatus.SUCCESS,
            timer=timer,
            summary=summary,
        )
        deps.recorder.record_execution(record)
        update["last_execution_id"] = record.execution_id
        return update

    return analyze_repository
