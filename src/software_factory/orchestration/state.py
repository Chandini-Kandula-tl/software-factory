"""Typed LangGraph state for the factory workflow.

Values are JSON-serializable (domain models are stored via ``model_dump``) so
checkpoints and ``state.json`` stay readable and portable. Nodes validate them
back into domain models when they need typed access.
"""

from __future__ import annotations

from typing import Any, TypedDict


class FactoryState(TypedDict, total=False):
    project_id: str
    project_name: str
    project_mode: str
    repository_path: str | None
    factory_version: str
    workflow_version: str

    lifecycle_status: str
    current_stage: str
    completed_stages: list[str]

    requirements_input: str
    clarification_round: int
    question_counter: int
    pending_questions: list[dict[str, Any]]
    clarifications: list[dict[str, Any]]

    requirements: dict[str, Any] | None
    repository: dict[str, Any] | None
    impact_analysis: dict[str, Any] | None
    architecture: dict[str, Any] | None
    technology_selection: dict[str, Any] | None
    implementation_plan: dict[str, Any] | None
    # Deterministic check of the plan's commands against this machine.
    plan_checks: list[str]

    # Latest decision per approval stage, plus the full decision log.
    approvals: dict[str, dict[str, Any]]
    decision_log: list[dict[str, Any]]
    revision_feedback: dict[str, str]
    artifact_revisions: dict[str, int]

    # Build phase (see orchestration/nodes/development_nodes.py).
    development: dict[str, Any]
    last_verification: dict[str, Any] | None
    test_analysis: dict[str, Any] | None
    change_request: dict[str, Any] | None
    review: dict[str, Any] | None
    security: dict[str, Any] | None
    validation: dict[str, Any] | None
    release: dict[str, Any] | None

    # Updates of a released app: the version in progress (1 = first build),
    # what the user asked to change, and the earlier releases.
    version: int
    update_request: dict[str, Any] | None
    versions: list[dict[str, Any]]

    failure: dict[str, Any] | None
    errors: list[dict[str, Any]]
    warnings: list[str]
    last_execution_id: str | None
