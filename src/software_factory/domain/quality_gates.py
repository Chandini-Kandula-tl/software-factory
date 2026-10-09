"""Stage entry criteria, checked deterministically before a stage runs.

The graph never advances just because an agent returned successfully: a stage
may only start when the artifacts and approvals it depends on exist.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from software_factory.domain.approvals import DESIGN_STAGES, ApprovalStage, ApprovalStatus
from software_factory.domain.errors import QualityGateError


def _approved(state: Mapping[str, Any], stage: ApprovalStage) -> bool:
    approvals: Mapping[str, Any] = state.get("approvals") or {}
    decision: Mapping[str, Any] = approvals.get(stage.value) or {}
    return decision.get("status") == ApprovalStatus.APPROVED.value


@dataclass(frozen=True)
class Criterion:
    description: str
    check: Callable[[Mapping[str, Any]], bool]


def _approval_criterion(stage: ApprovalStage) -> Criterion:
    return Criterion(f"{stage.value} is approved", lambda s: _approved(s, stage))


ENTRY_CRITERIA: dict[str, list[Criterion]] = {
    "requirements": [
        Criterion(
            "user requirements are available",
            lambda s: bool((s.get("requirements_input") or "").strip()),
        ),
    ],
    "repository_analysis": [
        Criterion(
            "requirements are approved",
            lambda s: _approved(s, ApprovalStage.REQUIREMENTS) and bool(s.get("requirements")),
        ),
    ],
    "architecture": [
        Criterion("requirements are approved", lambda s: _approved(s, ApprovalStage.REQUIREMENTS)),
        Criterion("requirements artifact exists", lambda s: bool(s.get("requirements"))),
    ],
    "technology": [
        Criterion("architecture is approved", lambda s: _approved(s, ApprovalStage.ARCHITECTURE)),
        Criterion("architecture artifact exists", lambda s: bool(s.get("architecture"))),
    ],
    "implementation_plan": [
        Criterion("technology is approved", lambda s: _approved(s, ApprovalStage.TECHNOLOGY)),
        Criterion("technology artifact exists", lambda s: bool(s.get("technology_selection"))),
    ],
    "development": [
        *(_approval_criterion(stage) for stage in DESIGN_STAGES),
        Criterion("implementation plan exists", lambda s: bool(s.get("implementation_plan"))),
    ],
    "code_review": [
        Criterion(
            "every planned task is implemented",
            lambda s: not (s.get("development") or {}).get("task_queue"),
        ),
    ],
    "release": [
        Criterion(
            "final validation passed",
            lambda s: bool((s.get("validation") or {}).get("passed")),
        ),
        Criterion(
            "security validation passed",
            lambda s: bool((s.get("security") or {}).get("passed")),
        ),
        _approval_criterion(ApprovalStage.RELEASE),
    ],
}


def unmet_entry_criteria(stage: str, state: Mapping[str, Any]) -> list[str]:
    return [c.description for c in ENTRY_CRITERIA.get(stage, []) if not c.check(state)]


def require_entry(stage: str, state: Mapping[str, Any]) -> None:
    unmet = unmet_entry_criteria(stage, state)
    if unmet:
        raise QualityGateError(
            f"Cannot enter stage '{stage}': " + "; ".join(f"{u} (not met)" for u in unmet)
        )
