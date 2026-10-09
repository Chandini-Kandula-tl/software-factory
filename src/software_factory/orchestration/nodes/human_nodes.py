"""Human-in-the-loop nodes: clarification, approval gates, failure handling.

Each node pauses the graph with ``interrupt()``; the checkpoint is persisted
and the process may exit. Factory Core later resumes the graph with the human's
input. Resume payloads that don't satisfy the gate leave the status unchanged,
so the router sends the workflow straight back to the same gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt

from software_factory.domain.approvals import ApprovalDecision, ApprovalStage, ApprovalStatus
from software_factory.domain.execution import ExecutionKind, ExecutionStatus
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.lifecycle import transition
from software_factory.domain.quality_gates import unmet_entry_criteria
from software_factory.domain.requirements import ClarificationAnswer, ClarificationQuestion
from software_factory.observability.clock import utc_now
from software_factory.orchestration.nodes.common import execution_record
from software_factory.orchestration.nodes.development_nodes import prepare_retry
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.orchestration.state import FactoryState


def make_wait_for_user_input(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def wait_for_user_input(state: FactoryState) -> dict[str, Any]:
        questions = [ClarificationQuestion.model_validate(q) for q in state["pending_questions"]]
        payload = interrupt(
            {
                "kind": "clarification",
                "round": state.get("clarification_round", 1),
                "questions": [q.model_dump(mode="json") for q in questions],
            }
        )
        answers: dict[str, dict[str, str]] = (payload or {}).get("answers", {})
        missing = [q.id for q in questions if q.required and not _answer_text(answers, q.id)]
        if missing:
            return {}

        answered = [
            ClarificationAnswer(
                question_id=q.id,
                question=q.question,
                answer=_answer_text(answers, q.id),
                round=q.round,
                answered_at=answers[q.id].get("answered_at") or utc_now(),
            ).model_dump(mode="json")
            for q in questions
            if _answer_text(answers, q.id)
        ]
        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.CLARIFICATION,
            stage="requirements",
            status=ExecutionStatus.SUCCESS,
            summary=f"User answered {len(answered)} clarification question(s) "
            f"in round {state.get('clarification_round', 1)}",
            details={"question_ids": [a["question_id"] for a in answered]},
        )
        deps.recorder.record_execution(record)
        return {
            "clarifications": [*state.get("clarifications", []), *answered],
            "pending_questions": [],
            "lifecycle_status": transition(
                state["lifecycle_status"], S.REQUIREMENTS_ANALYSIS
            ).value,
            "last_execution_id": record.execution_id,
        }

    return wait_for_user_input


def _answer_text(answers: dict[str, dict[str, str]], question_id: str) -> str:
    return (answers.get(question_id) or {}).get("answer", "").strip()


@dataclass(frozen=True)
class ApprovalGate:
    approval_stage: ApprovalStage
    stage_key: str  # artifact / revision-feedback key
    artifact: str
    on_reject: S
    on_approve: S
    next_stage_key: str  # quality-gate key of the stage that follows approval
    # Extra state changes when rejected (e.g. a release rejection starts a fix).
    on_reject_update: Callable[[FactoryState, str], dict[str, Any]] | None = None


REQUIREMENT_GATE = ApprovalGate(
    ApprovalStage.REQUIREMENTS,
    "requirements",
    "requirements.md",
    S.REQUIREMENTS_ANALYSIS,
    S.REPOSITORY_ANALYSIS,
    "repository_analysis",
)
ARCHITECTURE_GATE = ApprovalGate(
    ApprovalStage.ARCHITECTURE,
    "architecture",
    "architecture.md",
    S.ARCHITECTURE_ANALYSIS,
    S.TECHNOLOGY_SELECTION,
    "technology",
)
TECHNOLOGY_GATE = ApprovalGate(
    ApprovalStage.TECHNOLOGY,
    "technology",
    "technology-stack.md",
    S.TECHNOLOGY_SELECTION,
    S.IMPLEMENTATION_PLANNING,
    "implementation_plan",
)


def _release_rejected(state: FactoryState, reason: str) -> dict[str, Any]:
    """A rejected release becomes a fix cycle: fix → build → test → review again."""
    development = dict(state.get("development") or {})
    development.update(
        {
            "phase": "release_feedback",
            "fix_context": {"source": "release_feedback", "feedback": reason},
            "debug_iterations": 0,
            "review_rounds": 0,
            "security_rounds": 0,
        }
    )
    return {"development": development}


RELEASE_GATE = ApprovalGate(
    ApprovalStage.RELEASE,
    "release",
    "validation.md",
    S.BUG_FIXING,
    S.RELEASE,
    "release",
    on_reject_update=_release_rejected,
)

PLAN_GATE = ApprovalGate(
    ApprovalStage.IMPLEMENTATION_PLAN,
    "implementation_plan",
    "implementation-plan.md",
    S.IMPLEMENTATION_PLANNING,
    S.READY_FOR_DEVELOPMENT,
    "development",
)


def make_approval_gate(
    deps: WorkflowDependencies, gate: ApprovalGate
) -> Callable[[FactoryState], dict]:
    def approval_gate(state: FactoryState) -> dict[str, Any]:
        revision = (state.get("artifact_revisions") or {}).get(gate.stage_key, 1)
        payload = interrupt(
            {
                "kind": "approval",
                "stage": gate.approval_stage.value,
                "artifact": gate.artifact,
                "revision": revision,
            }
        )
        try:
            status = ApprovalStatus(str((payload or {}).get("decision")))
        except ValueError:
            return {}
        decision = ApprovalDecision(
            stage=gate.approval_stage,
            status=status,
            approved_by=payload.get("decided_by") or "human",
            reason=payload.get("reason", ""),
            timestamp=payload.get("timestamp") or utc_now(),
            artifact_revision=revision,
        ).model_dump(mode="json")

        approvals = {**(state.get("approvals") or {}), gate.approval_stage.value: decision}
        feedback = dict(state.get("revision_feedback") or {})
        completed = list(state.get("completed_stages") or [])
        if status is ApprovalStatus.APPROVED:
            feedback.pop(gate.stage_key, None)
            if gate.stage_key not in completed:
                completed.append(gate.stage_key)
            next_status = gate.on_approve
            unmet = unmet_entry_criteria(gate.next_stage_key, {**state, "approvals": approvals})
            if unmet:  # defensive: never advance past an unmet gate
                return {}
        else:
            feedback[gate.stage_key] = decision["reason"] or "Rejected without a reason."
            next_status = gate.on_reject
        extra = (
            gate.on_reject_update(state, feedback[gate.stage_key])
            if status is ApprovalStatus.REJECTED and gate.on_reject_update
            else {}
        )

        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.HUMAN_DECISION,
            stage=gate.stage_key,
            status=ExecutionStatus.INFO,
            summary=f"{gate.approval_stage.value} revision {revision} {status.value}",
            details=decision,
        )
        deps.recorder.record_execution(record)
        return {
            **extra,
            "approvals": approvals,
            "decision_log": [*(state.get("decision_log") or []), decision],
            "revision_feedback": feedback,
            "completed_stages": completed,
            "lifecycle_status": transition(state["lifecycle_status"], next_status).value,
            "last_execution_id": record.execution_id,
        }

    approval_gate.__name__ = f"{gate.stage_key}_approval"
    return approval_gate


# Approving a change request re-opens the target decision and everything after it.
_CHANGE_TARGETS: dict[str, tuple[S, list[ApprovalStage]]] = {
    "architecture": (
        S.ARCHITECTURE_ANALYSIS,
        [ApprovalStage.ARCHITECTURE, ApprovalStage.TECHNOLOGY, ApprovalStage.IMPLEMENTATION_PLAN],
    ),
    "technology": (
        S.TECHNOLOGY_SELECTION,
        [ApprovalStage.TECHNOLOGY, ApprovalStage.IMPLEMENTATION_PLAN],
    ),
    "implementation_plan": (S.IMPLEMENTATION_PLANNING, [ApprovalStage.IMPLEMENTATION_PLAN]),
}


def make_change_request_gate(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def change_request_approval(state: FactoryState) -> dict[str, Any]:
        request = state.get("change_request") or {}
        payload = interrupt(
            {
                "kind": "approval",
                "stage": ApprovalStage.CHANGE_REQUEST.value,
                "artifact": "change-request.md",
                "revision": 1,
                "target": request.get("target"),
            }
        )
        try:
            status = ApprovalStatus(str((payload or {}).get("decision")))
        except ValueError:
            return {}
        reason = (payload or {}).get("reason", "")
        decision = ApprovalDecision(
            stage=ApprovalStage.CHANGE_REQUEST,
            status=status,
            approved_by=payload.get("decided_by") or "human",
            reason=reason,
            timestamp=payload.get("timestamp") or utc_now(),
        ).model_dump(mode="json")
        development = dict(state.get("development") or {})
        update: dict[str, Any] = {
            "decision_log": [*(state.get("decision_log") or []), decision],
            "change_request": None,
        }
        if status is ApprovalStatus.APPROVED:
            target, reopened = _CHANGE_TARGETS[str(request.get("target"))]
            approvals = dict(state.get("approvals") or {})
            for stage in reopened:
                approvals.pop(stage.value, None)
            # Approval stage values double as the design stages' artifact keys; the
            # build-phase stages must be passed again after the redesign too.
            reopened_keys = {s.value for s in reopened} | {
                "development",
                "code_review",
                "security_validation",
                "final_validation",
            }
            feedback = dict(state.get("revision_feedback") or {})
            feedback[str(request.get("target"))] = (
                "Approved change request from development. "
                f"Problem: {request.get('problem')} Proposed change: "
                f"{request.get('proposed_change')}"
                + (f" Reviewer note: {reason}" if reason else "")
            )
            development["phase"] = "task"
            development["fix_context"] = None
            update.update(
                {
                    "approvals": approvals,
                    "revision_feedback": feedback,
                    "completed_stages": [
                        s for s in state.get("completed_stages", []) if s not in reopened_keys
                    ],
                    "lifecycle_status": transition(state["lifecycle_status"], target).value,
                }
            )
        else:
            development["change_request_feedback"] = (
                "Your change request was rejected; keep the approved decisions and complete "
                "the work within them." + (f" Reviewer note: {reason}" if reason else "")
            )
            origin = S(request.get("origin_status", S.DEVELOPMENT))
            update["lifecycle_status"] = transition(state["lifecycle_status"], origin).value
        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.HUMAN_DECISION,
            stage="change_request",
            status=ExecutionStatus.INFO,
            summary=f"Change request ({request.get('target')}) {status.value}",
            details=decision,
        )
        deps.recorder.record_execution(record)
        return {**update, "development": development, "last_execution_id": record.execution_id}

    return change_request_approval


def make_handle_failure(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def handle_failure(state: FactoryState) -> dict[str, Any]:
        failure = state.get("failure") or {}
        payload = interrupt({"kind": "failure", **failure})
        action = (payload or {}).get("action")
        extra: dict[str, Any] = {}
        if action == "retry":
            target = S(failure.get("resume_status", S.REQUIREMENTS_ANALYSIS))
            hint = str((payload or {}).get("hint") or "").strip()
            summary = f"Retry requested for stage {failure.get('stage')}"
            if hint:
                summary += f" with human hint: {hint}"
            if state.get("development"):
                target, extra["development"], note = prepare_retry(deps, state, target, hint)
                summary += f" ({note})" if note else ""
            elif hint:  # design stages: the hint becomes revision feedback for that agent
                feedback = dict(state.get("revision_feedback") or {})
                feedback[str(failure.get("stage"))] = hint
                extra["revision_feedback"] = feedback
        elif action == "abort":
            target = S.CANCELLED
            summary = f"Project cancelled after failure in {failure.get('stage')}"
        else:
            return {}
        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.WORKFLOW,
            stage=str(failure.get("stage", "unknown")),
            status=ExecutionStatus.INFO,
            summary=summary,
        )
        deps.recorder.record_execution(record)
        return {
            **extra,
            "lifecycle_status": transition(state["lifecycle_status"], target).value,
            "failure": None if target is not S.CANCELLED else failure,
            "last_execution_id": record.execution_id,
        }

    return handle_failure
