"""Factory Core implementation of the workflow's ``WorkflowRecorder`` port.

Writes every human-readable project artifact and keeps the project registry's
headline status in sync with the workflow.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from software_factory.domain.architecture import ArchitectureProposal
from software_factory.domain.development import (
    ChangeRequest,
    ImpactAnalysis,
    ReleaseRecord,
    ReviewReport,
    SecurityReport,
    TestFailureAnalysis,
    ValidationReport,
    VerificationResult,
)
from software_factory.domain.execution import ExecutionRecord
from software_factory.domain.lifecycle import LifecycleStatus
from software_factory.domain.planning import ImplementationPlan
from software_factory.domain.project import Project
from software_factory.domain.repository import RepositoryAnalysis
from software_factory.domain.requirements import (
    ClarificationAnswer,
    ClarificationQuestion,
    RequirementAnalysis,
)
from software_factory.domain.technology import TechnologySelection
from software_factory.persistence.artifacts import STATE_FILE, ArtifactStore
from software_factory.persistence.database import ProjectRegistry
from software_factory.persistence.markdown import (
    render_architecture,
    render_change_request,
    render_decisions,
    render_errors,
    render_impact_analysis,
    render_plan,
    render_progress,
    render_project,
    render_release_notes,
    render_repository_analysis,
    render_requirements,
    render_review,
    render_security,
    render_technology,
    render_test_analysis,
    render_validation,
    render_verification,
)

logger = logging.getLogger(__name__)

ARTIFACT_STEMS = {
    "requirements": "requirements",
    "architecture": "architecture",
    "technology": "technology-stack",
    "implementation_plan": "implementation-plan",
    "repository_analysis": "repository-analysis",
    "code_review": "review",
}

# Build-phase documents that keep only their latest version.
LATEST_ONLY_STEMS = {
    "test_results": "test-results",
    "test_analysis": "test-analysis",
    "security": "security",
    "validation": "validation",
    "change_request": "change-request",
    "impact_analysis": "impact-analysis",
    "release": "release-notes",
}

PENDING_ACTIONS = {
    LifecycleStatus.CREATED: "Start the workflow: `factory start <project>`",
    LifecycleStatus.WAITING_FOR_REQUIREMENT_CLARIFICATION: (
        "Answer clarification questions: `factory questions` / `factory answer`, "
        "then `factory continue`"
    ),
    LifecycleStatus.WAITING_FOR_REQUIREMENT_APPROVAL: "Review requirements.md, then approve/reject",
    LifecycleStatus.WAITING_FOR_ARCHITECTURE_APPROVAL: (
        "Review architecture.md, then approve/reject"
    ),
    LifecycleStatus.WAITING_FOR_TECHNOLOGY_APPROVAL: (
        "Review technology-stack.md, then approve/reject"
    ),
    LifecycleStatus.WAITING_FOR_PLAN_APPROVAL: "Review implementation-plan.md, then approve/reject",
    LifecycleStatus.FAILED: "Inspect errors.md, then `factory retry <project>`",
    LifecycleStatus.READY_FOR_DEVELOPMENT: (
        "Design approved — `factory continue` starts development"
    ),
    LifecycleStatus.WAITING_FOR_CHANGE_REQUEST_APPROVAL: (
        "Review change-request.md, then approve (reopens that design stage) or reject"
    ),
    LifecycleStatus.WAITING_FOR_RELEASE_APPROVAL: (
        "Review validation.md, review.md and security.md, then approve the release or "
        "reject with what to fix"
    ),
    LifecycleStatus.COMPLETED: "None — released (see release-notes.md). To change the app: "
    '`factory update <project> --text "what to change"`',
    LifecycleStatus.CANCELLED: "None — project cancelled",
}


def pending_action(status: str) -> str:
    try:
        return PENDING_ACTIONS.get(LifecycleStatus(status), "Workflow is running")
    except ValueError:
        return "Unknown"


class ProjectRecorder:
    def __init__(self, project: Project, store: ArtifactStore, registry: ProjectRegistry) -> None:
        self._project = project
        self._store = store
        self._registry = registry

    def persist_state(self, state: Mapping[str, Any]) -> None:
        status = state.get("lifecycle_status", LifecycleStatus.CREATED.value)
        self._project = self._registry.get(self._project.project_id)  # pick up pause flag
        self._store.write_json(STATE_FILE, dict(state))
        self._store.write_text(
            "progress.md",
            render_progress(dict(state), self._project.paused, pending_action(status)),
        )
        self._store.write_text("decisions.md", render_decisions(state.get("decision_log") or []))
        self._store.write_text("errors.md", render_errors(state.get("errors") or []))
        self._store.write_text("project.md", render_project(self._project, status))
        self._write_questions(state.get("pending_questions") or [])
        self._registry.set_lifecycle_status(self._project.project_id, status)

    def _write_questions(self, questions: list[dict[str, Any]]) -> None:
        if not questions:
            self._store.write_text(
                "clarification-questions.md", "# Clarification questions\n\n_None pending._\n"
            )
            return
        lines = [
            "# Clarification questions",
            "",
            "Answer with `factory answer <project> <id> "
            '"<answer>"`, then run `factory continue <project>`.',
            "",
        ]
        for raw in questions:
            q = ClarificationQuestion.model_validate(raw)
            flag = "required" if q.required else "optional"
            lines.append(f"- **{q.id}** ({flag}, {q.impact_area.value}): {q.question}")
            if q.reason:
                lines.append(f"  - Why: {q.reason}")
        self._store.write_text("clarification-questions.md", "\n".join(lines) + "\n")

    def write_stage_artifact(self, stage: str, output: BaseModel, state: Mapping[str, Any]) -> None:
        revision = (state.get("artifact_revisions") or {}).get(stage, 1)
        if isinstance(output, RequirementAnalysis):
            answers = [
                ClarificationAnswer.model_validate(a) for a in state.get("clarifications") or []
            ]
            markdown = render_requirements(output, answers, revision)
        elif isinstance(output, ArchitectureProposal):
            markdown = render_architecture(output, revision)
        elif isinstance(output, TechnologySelection):
            markdown = render_technology(output, revision)
        elif isinstance(output, ImplementationPlan):
            markdown = render_plan(output, revision, list(state.get("plan_checks") or []))
        elif isinstance(output, RepositoryAnalysis):
            markdown = render_repository_analysis(output)
        elif isinstance(output, ReviewReport):
            markdown = render_review(output, revision)
        elif stage in LATEST_ONLY_STEMS:
            self._store.write_artifact(
                LATEST_ONLY_STEMS[stage], output, self._render_latest(output, state)
            )
            return
        else:
            raise TypeError(f"No renderer for {type(output).__name__}")
        self._store.write_stage_artifact(ARTIFACT_STEMS[stage], output, markdown, revision)

    @staticmethod
    def _render_latest(output: BaseModel, state: Mapping[str, Any]) -> str:
        if isinstance(output, VerificationResult):
            return render_verification(output)
        if isinstance(output, TestFailureAnalysis):
            return render_test_analysis(output)
        if isinstance(output, SecurityReport):
            return render_security(output)
        if isinstance(output, ValidationReport):
            return render_validation(output)
        if isinstance(output, ImpactAnalysis):
            return render_impact_analysis(output)
        if isinstance(output, ChangeRequest):
            return render_change_request(output, dict(state.get("change_request") or {}))
        if isinstance(output, ReleaseRecord):
            return render_release_notes(output, dict(state))
        raise TypeError(f"No renderer for {type(output).__name__}")

    def write_tool_output(self, name: str, content: str) -> str:
        """Save a tool log as artifacts/tool-runs/<NNN>-<name> (numbered, never overwritten)."""
        directory = self._store.path("artifacts/tool-runs")
        number = len(list(directory.glob("*"))) + 1 if directory.exists() else 1
        relative = f"artifacts/tool-runs/{number:03d}-{name}"
        self._store.write_text(relative, content)
        return relative

    def record_execution(self, record: ExecutionRecord) -> None:
        self._store.append_history(record)
        logger.info(
            record.summary or record.kind.value,
            extra={
                "project_id": record.project_id,
                "execution_id": record.execution_id,
                "stage": record.stage,
                "agent": record.agent,
                "provider": record.provider,
                "model": record.model,
                "status": record.status.value,
                "duration_ms": record.duration_ms,
                "error": record.error,
            },
        )
