"""Human approval decisions — persisted, never inferred from agent text."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ApprovalStage(StrEnum):
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    TECHNOLOGY = "technology"
    IMPLEMENTATION_PLAN = "implementation_plan"
    CHANGE_REQUEST = "change_request"
    RELEASE = "release"


# The four design gates every project passes before development starts.
DESIGN_STAGES: tuple[ApprovalStage, ...] = (
    ApprovalStage.REQUIREMENTS,
    ApprovalStage.ARCHITECTURE,
    ApprovalStage.TECHNOLOGY,
    ApprovalStage.IMPLEMENTATION_PLAN,
)


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(BaseModel):
    stage: ApprovalStage
    status: ApprovalStatus
    approved_by: str = "human"
    reason: str = ""
    timestamp: str
    artifact_revision: int = 1
