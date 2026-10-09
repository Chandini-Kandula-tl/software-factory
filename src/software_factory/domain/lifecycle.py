"""Project lifecycle state machine.

The LLM never sets lifecycle states. Agents *propose* (for example
"clarification needed" or "change request"); deterministic workflow code
decides the transition and validates it here.
"""

from __future__ import annotations

from enum import StrEnum

from software_factory.domain.errors import InvalidTransitionError


class LifecycleStatus(StrEnum):
    CREATED = "CREATED"
    REQUIREMENTS_ANALYSIS = "REQUIREMENTS_ANALYSIS"
    WAITING_FOR_REQUIREMENT_CLARIFICATION = "WAITING_FOR_REQUIREMENT_CLARIFICATION"
    WAITING_FOR_REQUIREMENT_APPROVAL = "WAITING_FOR_REQUIREMENT_APPROVAL"
    REPOSITORY_ANALYSIS = "REPOSITORY_ANALYSIS"
    ARCHITECTURE_ANALYSIS = "ARCHITECTURE_ANALYSIS"
    WAITING_FOR_ARCHITECTURE_APPROVAL = "WAITING_FOR_ARCHITECTURE_APPROVAL"
    TECHNOLOGY_SELECTION = "TECHNOLOGY_SELECTION"
    WAITING_FOR_TECHNOLOGY_APPROVAL = "WAITING_FOR_TECHNOLOGY_APPROVAL"
    IMPLEMENTATION_PLANNING = "IMPLEMENTATION_PLANNING"
    WAITING_FOR_PLAN_APPROVAL = "WAITING_FOR_PLAN_APPROVAL"
    READY_FOR_DEVELOPMENT = "READY_FOR_DEVELOPMENT"
    DEVELOPMENT = "DEVELOPMENT"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    BUG_FIXING = "BUG_FIXING"
    WAITING_FOR_CHANGE_REQUEST_APPROVAL = "WAITING_FOR_CHANGE_REQUEST_APPROVAL"
    CODE_REVIEW = "CODE_REVIEW"
    SECURITY_VALIDATION = "SECURITY_VALIDATION"
    FINAL_VALIDATION = "FINAL_VALIDATION"
    WAITING_FOR_RELEASE_APPROVAL = "WAITING_FOR_RELEASE_APPROVAL"
    RELEASE = "RELEASE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


S = LifecycleStatus

# Stages where an agent or tool node works. Any of them may fail; a FAILED
# project can be retried back into the stage that failed.
WORKING_STAGES: frozenset[LifecycleStatus] = frozenset(
    {
        S.REQUIREMENTS_ANALYSIS,
        S.REPOSITORY_ANALYSIS,
        S.ARCHITECTURE_ANALYSIS,
        S.TECHNOLOGY_SELECTION,
        S.IMPLEMENTATION_PLANNING,
        S.READY_FOR_DEVELOPMENT,  # workspace preparation + environment check
        S.DEVELOPMENT,
        S.BUILDING,
        S.TESTING,
        S.BUG_FIXING,
        S.CODE_REVIEW,
        S.SECURITY_VALIDATION,
        S.FINAL_VALIDATION,
        S.RELEASE,
    }
)

WAITING_FOR_HUMAN: frozenset[LifecycleStatus] = frozenset(
    {
        S.WAITING_FOR_REQUIREMENT_CLARIFICATION,
        S.WAITING_FOR_REQUIREMENT_APPROVAL,
        S.WAITING_FOR_ARCHITECTURE_APPROVAL,
        S.WAITING_FOR_TECHNOLOGY_APPROVAL,
        S.WAITING_FOR_PLAN_APPROVAL,
        S.WAITING_FOR_CHANGE_REQUEST_APPROVAL,
        S.WAITING_FOR_RELEASE_APPROVAL,
        S.FAILED,
    }
)

# After an approved change request the workflow returns to the design stage
# that owns the decision being changed.
CHANGE_REQUEST_TARGETS = frozenset(
    {S.ARCHITECTURE_ANALYSIS, S.TECHNOLOGY_SELECTION, S.IMPLEMENTATION_PLANNING}
)

ALLOWED_TRANSITIONS: dict[LifecycleStatus, frozenset[LifecycleStatus]] = {
    S.CREATED: frozenset({S.REQUIREMENTS_ANALYSIS}),
    S.REQUIREMENTS_ANALYSIS: frozenset(
        {S.WAITING_FOR_REQUIREMENT_CLARIFICATION, S.WAITING_FOR_REQUIREMENT_APPROVAL}
    ),
    S.WAITING_FOR_REQUIREMENT_CLARIFICATION: frozenset({S.REQUIREMENTS_ANALYSIS}),
    S.WAITING_FOR_REQUIREMENT_APPROVAL: frozenset({S.REQUIREMENTS_ANALYSIS, S.REPOSITORY_ANALYSIS}),
    S.REPOSITORY_ANALYSIS: frozenset({S.ARCHITECTURE_ANALYSIS}),
    S.ARCHITECTURE_ANALYSIS: frozenset({S.WAITING_FOR_ARCHITECTURE_APPROVAL}),
    S.WAITING_FOR_ARCHITECTURE_APPROVAL: frozenset(
        {S.ARCHITECTURE_ANALYSIS, S.TECHNOLOGY_SELECTION}
    ),
    S.TECHNOLOGY_SELECTION: frozenset({S.WAITING_FOR_TECHNOLOGY_APPROVAL}),
    S.WAITING_FOR_TECHNOLOGY_APPROVAL: frozenset(
        {S.TECHNOLOGY_SELECTION, S.IMPLEMENTATION_PLANNING}
    ),
    S.IMPLEMENTATION_PLANNING: frozenset({S.WAITING_FOR_PLAN_APPROVAL}),
    S.WAITING_FOR_PLAN_APPROVAL: frozenset({S.IMPLEMENTATION_PLANNING, S.READY_FOR_DEVELOPMENT}),
    # -- build / verify phase --------------------------------------------------
    # IMPLEMENTATION_PLANNING: a plan approved before project commands existed is re-planned.
    S.READY_FOR_DEVELOPMENT: frozenset({S.DEVELOPMENT, S.CODE_REVIEW, S.IMPLEMENTATION_PLANNING}),
    S.DEVELOPMENT: frozenset({S.BUILDING, S.WAITING_FOR_CHANGE_REQUEST_APPROVAL}),
    S.BUILDING: frozenset({S.TESTING, S.BUG_FIXING}),
    S.TESTING: frozenset(
        {
            S.DEVELOPMENT,  # next task
            S.CODE_REVIEW,  # all tasks done, or a review fix passed
            S.SECURITY_VALIDATION,  # a security fix passed
            S.FINAL_VALIDATION,  # a validation fix passed
            S.BUG_FIXING,
        }
    ),
    S.BUG_FIXING: frozenset({S.BUILDING, S.WAITING_FOR_CHANGE_REQUEST_APPROVAL}),
    S.WAITING_FOR_CHANGE_REQUEST_APPROVAL: CHANGE_REQUEST_TARGETS
    | {S.DEVELOPMENT, S.BUG_FIXING},  # rejected: continue where the request came from
    S.CODE_REVIEW: frozenset({S.SECURITY_VALIDATION, S.BUG_FIXING}),
    S.SECURITY_VALIDATION: frozenset({S.FINAL_VALIDATION, S.BUG_FIXING}),
    S.FINAL_VALIDATION: frozenset({S.WAITING_FOR_RELEASE_APPROVAL, S.BUG_FIXING}),
    S.WAITING_FOR_RELEASE_APPROVAL: frozenset({S.RELEASE, S.BUG_FIXING}),
    S.RELEASE: frozenset({S.COMPLETED}),
    S.FAILED: WORKING_STAGES | {S.CANCELLED},
    # A released app can be updated: the next version starts at requirements.
    S.COMPLETED: frozenset({S.REQUIREMENTS_ANALYSIS}),
    S.CANCELLED: frozenset(),
}

# Any working stage may fail; the failure handler is the only way out.
for _stage in WORKING_STAGES:
    ALLOWED_TRANSITIONS[_stage] = ALLOWED_TRANSITIONS[_stage] | {S.FAILED}


def is_allowed(current: LifecycleStatus, target: LifecycleStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def transition(current: LifecycleStatus | str, target: LifecycleStatus) -> LifecycleStatus:
    """Validate ``current -> target`` and return ``target``.

    Raises ``InvalidTransitionError`` for transitions the state machine forbids.
    """
    current_status = LifecycleStatus(current)
    if not is_allowed(current_status, target):
        raise InvalidTransitionError(f"Invalid lifecycle transition {current_status} -> {target}")
    return target
