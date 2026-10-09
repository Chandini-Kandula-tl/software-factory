"""Workflow nodes that run AI agents.

Each node: checks the stage's entry criteria, builds a minimal agent context,
runs the agent, persists the artifact, records execution history, and then
*deterministically* decides the next lifecycle status. Failures are caught and
turned into a FAILED status that waits for a human retry.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel

from software_factory.agents.base import AgentResult, LLMAgent, llm_calls_from_error
from software_factory.domain.errors import FactoryError
from software_factory.domain.execution import ExecutionKind, ExecutionStatus
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.lifecycle import transition
from software_factory.domain.planning import ImplementationPlan
from software_factory.domain.quality_gates import require_entry
from software_factory.domain.requirements import (
    ClarificationQuestion,
    ProposedQuestion,
    RequirementAnalysis,
)
from software_factory.orchestration.nodes.common import (
    Timer,
    execution_record,
    failure_update,
)
from software_factory.orchestration.nodes.development_nodes import command_problems
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.orchestration.state import FactoryState


@dataclass(frozen=True)
class AgentStage:
    key: str  # quality-gate / artifact stage key
    state_field: str  # where the output lives in FactoryState
    waiting_status: S  # status after a successful run


REQUIREMENTS = AgentStage("requirements", "requirements", S.WAITING_FOR_REQUIREMENT_APPROVAL)
ARCHITECTURE = AgentStage("architecture", "architecture", S.WAITING_FOR_ARCHITECTURE_APPROVAL)
TECHNOLOGY = AgentStage("technology", "technology_selection", S.WAITING_FOR_TECHNOLOGY_APPROVAL)
PLAN = AgentStage("implementation_plan", "implementation_plan", S.WAITING_FOR_PLAN_APPROVAL)


def _run_agent_stage(
    deps: WorkflowDependencies,
    state: FactoryState,
    stage: AgentStage,
    agent: LLMAgent[Any],
    available: dict[str, Any],
    decide: Callable[[BaseModel], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timer = Timer()
    spec = agent.spec
    try:
        require_entry(stage.key, state)
        context = agent.build_context(
            state["project_id"],
            state["project_name"],
            stage.key,
            available,
            revision_feedback=(state.get("revision_feedback") or {}).get(stage.key),
        )
        result: AgentResult[Any] = agent.run(context)
    except FactoryError as error:
        record = execution_record(
            deps,
            state,
            kind=ExecutionKind.AGENT,
            stage=stage.key,
            status=ExecutionStatus.FAILED,
            timer=timer,
            agent=spec.name,
            agent_version=spec.version,
            model_profile=spec.llm_profile,
            llm_calls=llm_calls_from_error(error, spec.llm_profile),
            error=str(error),
            summary=f"{spec.name} failed",
        )
        deps.recorder.record_execution(record)
        return failure_update(state, stage=stage.key, error=error, execution_id=record.execution_id)

    revisions = dict(state.get("artifact_revisions") or {})
    revisions[stage.key] = revisions.get(stage.key, 0) + 1
    decision = decide(result.output) if decide else {}
    update: dict[str, Any] = {
        stage.state_field: result.output.model_dump(mode="json"),
        "artifact_revisions": revisions,
        "failure": None,
        **decision,
        "warnings": [
            *state.get("warnings", []),
            *(f"{spec.name}: {w}" for w in result.warnings),
            *decision.get("warnings", []),
        ],
    }
    update.setdefault(
        "lifecycle_status", transition(state["lifecycle_status"], stage.waiting_status).value
    )

    artifact = spec.output_schema.model_validate(update[stage.state_field])
    deps.recorder.write_stage_artifact(stage.key, artifact, {**state, **update})
    record = execution_record(
        deps,
        state,
        kind=ExecutionKind.AGENT,
        stage=stage.key,
        status=ExecutionStatus.SUCCESS,
        timer=timer,
        agent=spec.name,
        agent_version=spec.version,
        model_profile=result.model_profile,
        provider=result.provider,
        model=result.model,
        prompt_versions=result.prompt_versions,
        llm_calls=result.llm_calls,
        summary=f"{spec.name} produced {stage.key} revision {revisions[stage.key]}",
        details={"warnings": result.warnings, "next_status": update["lifecycle_status"]},
    )
    deps.recorder.record_execution(record)
    update["last_execution_id"] = record.execution_id
    return update


def _previous(state: FactoryState, field: str) -> Any:
    return state.get(field) or None


def update_brief(state: FactoryState) -> str | None:
    """What the design agents are told when a released app is being updated."""
    request = state.get("update_request")
    if not request:
        return None
    return (
        f"The app is built and was released as version {request['version'] - 1} "
        f"(git tag {request.get('previous_release') or 'unknown'}). "
        f"This is version {request['version']}. Requested change:\n\n{request['change']}"
    )


def make_analyze_requirements(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def analyze_requirements(state: FactoryState) -> dict[str, Any]:
        def decide(output: BaseModel) -> dict[str, Any]:
            assert isinstance(output, RequirementAnalysis)
            return _clarification_decision(deps, state, output)

        return _run_agent_stage(
            deps,
            state,
            REQUIREMENTS,
            deps.agents.requirement,
            {
                "requirements_input": state.get("requirements_input"),
                "project_mode": state.get("project_mode"),
                "clarifications": state.get("clarifications") or None,
                "previous_requirements": _previous(state, "requirements"),
                "update_request": update_brief(state),
            },
            decide,
        )

    return analyze_requirements


def _clarification_decision(
    deps: WorkflowDependencies, state: FactoryState, analysis: RequirementAnalysis
) -> dict[str, Any]:
    """Deterministic rule: wait for the user iff a *required* question was proposed
    and the clarification-round limit has not been reached.

    Questions that repeat one the user already answered are dropped first —
    small models sometimes re-ask despite the prompt, which would otherwise
    loop the user on the same question.
    """
    answered = [c["question"] for c in state.get("clarifications") or []]
    proposed = [q for q in analysis.clarification_questions if not _already_answered(q, answered)]
    repeated = len(analysis.clarification_questions) - len(proposed)
    dedupe_warning = (
        [f"Dropped {repeated} proposed question(s) that repeat already-answered ones"]
        if repeated
        else []
    )
    has_required = any(q.required for q in proposed)
    round_number = state.get("clarification_round", 0)
    current = state["lifecycle_status"]

    if has_required and round_number < deps.limits.max_clarification_rounds:
        counter = state.get("question_counter", 0)
        questions = []
        for proposal in proposed:
            counter += 1
            questions.append(
                ClarificationQuestion(
                    id=f"Q{counter}",
                    question=proposal.question,
                    reason=proposal.reason,
                    impact_area=proposal.impact_area,
                    required=proposal.required,
                    round=round_number + 1,
                ).model_dump(mode="json")
            )
        return {
            "lifecycle_status": transition(current, S.WAITING_FOR_REQUIREMENT_CLARIFICATION).value,
            "pending_questions": questions,
            "clarification_round": round_number + 1,
            "question_counter": counter,
            "warnings": dedupe_warning,
        }

    # No blocking questions (or rounds exhausted): unanswered questions become
    # visible open questions for the human reviewer instead of silent guesses.
    leftover = [q.question for q in proposed]
    warnings = list(dedupe_warning)
    if has_required:
        warnings.append(
            f"Clarification limit ({deps.limits.max_clarification_rounds} rounds) reached; "
            "remaining questions recorded as open questions for review"
        )
    requirements = analysis.model_copy(
        update={"open_questions": [*analysis.open_questions, *leftover]}
    ).model_dump(mode="json")
    return {
        "requirements": requirements,
        "pending_questions": [],
        "lifecycle_status": transition(current, S.WAITING_FOR_REQUIREMENT_APPROVAL).value,
        "warnings": warnings,
    }


_SIMILAR_QUESTION_RATIO = 0.85


def _normalize_question(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _already_answered(question: ProposedQuestion, answered: list[str]) -> bool:
    candidate = _normalize_question(question.question)
    return any(
        SequenceMatcher(None, candidate, _normalize_question(previous)).ratio()
        >= _SIMILAR_QUESTION_RATIO
        for previous in answered
    )


def make_design_architecture(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def design_architecture(state: FactoryState) -> dict[str, Any]:
        return _run_agent_stage(
            deps,
            state,
            ARCHITECTURE,
            deps.agents.architecture,
            {
                "requirements": state.get("requirements"),
                "repository_analysis": state.get("repository"),
                "impact_analysis": state.get("impact_analysis"),
                "previous_architecture": _previous(state, "architecture"),
                "update_request": update_brief(state),
            },
        )

    return design_architecture


def make_select_technology(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def select_technology(state: FactoryState) -> dict[str, Any]:
        return _run_agent_stage(
            deps,
            state,
            TECHNOLOGY,
            deps.agents.technology_selection,
            {
                "requirements": state.get("requirements"),
                "architecture": state.get("architecture"),
                "repository_analysis": state.get("repository"),
                "environment": deps.environment,
                "previous_technology_selection": _previous(state, "technology_selection"),
                "update_request": update_brief(state),
            },
        )

    return select_technology


def make_create_implementation_plan(deps: WorkflowDependencies) -> Callable[[FactoryState], dict]:
    def create_implementation_plan(state: FactoryState) -> dict[str, Any]:
        def check_environment(output: BaseModel) -> dict[str, Any]:
            # Shown on the plan so the human sees problems before approving it.
            assert isinstance(output, ImplementationPlan)
            return {"plan_checks": command_problems(output.commands, deps.security)}

        return _run_agent_stage(
            deps,
            state,
            PLAN,
            deps.agents.implementation_planning,
            {
                "requirements": state.get("requirements"),
                "architecture": state.get("architecture"),
                "technology_selection": state.get("technology_selection"),
                "repository_analysis": state.get("repository"),
                "impact_analysis": state.get("impact_analysis"),
                "environment": deps.environment,
                "previous_implementation_plan": _previous(state, "implementation_plan"),
                "update_request": update_brief(state),
            },
            check_environment,
        )

    return create_implementation_plan
