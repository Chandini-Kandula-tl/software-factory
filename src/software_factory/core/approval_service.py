"""Validates human approval/rejection input and builds the gate's resume payload."""

from __future__ import annotations

from typing import Any

from software_factory.core.workflow_service import WorkflowSnapshot
from software_factory.domain.approvals import ApprovalStage, ApprovalStatus
from software_factory.domain.errors import WorkflowActionError
from software_factory.observability.clock import utc_now


class ApprovalService:
    def build_decision(
        self,
        snapshot: WorkflowSnapshot,
        status: ApprovalStatus,
        reason: str = "",
        expected_stage: str | None = None,
        decided_by: str = "human",
    ) -> dict[str, Any]:
        if snapshot.waiting_for != "approval":
            raise WorkflowActionError(
                f"Project is not waiting for an approval (status: {snapshot.lifecycle_status})"
            )
        assert snapshot.interrupt is not None
        stage = snapshot.interrupt["stage"]
        if expected_stage:
            try:
                expected = ApprovalStage(expected_stage).value
            except ValueError as exc:
                raise WorkflowActionError(
                    f"Unknown stage '{expected_stage}'",
                    hint=f"Stages: {', '.join(s.value for s in ApprovalStage)}",
                ) from exc
            if expected != stage:
                raise WorkflowActionError(
                    f"Project is waiting for '{stage}' approval, not '{expected_stage}'"
                )
        if status is ApprovalStatus.REJECTED and not reason.strip():
            raise WorkflowActionError(
                "A rejection needs a reason so the agent can revise",
                hint='Use --reason "what should change".',
            )
        return {
            "decision": status.value,
            "reason": reason.strip(),
            "decided_by": decided_by,
            "timestamp": utc_now(),
        }
