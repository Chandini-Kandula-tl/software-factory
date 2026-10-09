"""Deterministic routing: the next node is a pure function of lifecycle status."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END

from software_factory.domain.lifecycle import LifecycleStatus as S

NODE_FOR_STATUS: dict[S, str] = {
    S.REQUIREMENTS_ANALYSIS: "analyze_requirements",
    S.WAITING_FOR_REQUIREMENT_CLARIFICATION: "wait_for_user_input",
    S.WAITING_FOR_REQUIREMENT_APPROVAL: "requirement_approval",
    S.REPOSITORY_ANALYSIS: "analyze_repository",
    S.ARCHITECTURE_ANALYSIS: "design_architecture",
    S.WAITING_FOR_ARCHITECTURE_APPROVAL: "architecture_approval",
    S.TECHNOLOGY_SELECTION: "select_technology",
    S.WAITING_FOR_TECHNOLOGY_APPROVAL: "technology_approval",
    S.IMPLEMENTATION_PLANNING: "create_implementation_plan",
    S.WAITING_FOR_PLAN_APPROVAL: "plan_approval",
    S.READY_FOR_DEVELOPMENT: "prepare_development",
    S.DEVELOPMENT: "implement_task",
    S.BUILDING: "build",
    S.TESTING: "test",
    S.BUG_FIXING: "debug",
    S.WAITING_FOR_CHANGE_REQUEST_APPROVAL: "change_request_approval",
    S.CODE_REVIEW: "code_review",
    S.SECURITY_VALIDATION: "security_validation",
    S.FINAL_VALIDATION: "final_validation",
    S.WAITING_FOR_RELEASE_APPROVAL: "release_approval",
    S.RELEASE: "release",
    S.FAILED: "handle_failure",
}

TERMINAL_STATUSES = frozenset({S.CANCELLED, S.COMPLETED})

ROUTE_TARGETS: list[str] = sorted(set(NODE_FOR_STATUS.values())) + [END]


def route_by_lifecycle(state: dict[str, Any]) -> str:
    status = S(state["lifecycle_status"])
    if status in TERMINAL_STATUSES:
        return END
    try:
        return NODE_FOR_STATUS[status]
    except KeyError as exc:  # a status no workflow node handles
        raise RuntimeError(f"No workflow node handles lifecycle status {status}") from exc
