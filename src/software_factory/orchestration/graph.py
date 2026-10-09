"""LangGraph definition of the factory workflow.

Design phase (four human approval gates):

    START → load_project → analyze_requirements
      analyze_requirements ─┬─ clarification needed → wait_for_user_input → analyze_requirements
                            └─ clear → requirement_approval
      requirement_approval ─┬─ rejected → analyze_requirements (with feedback)
                            └─ approved → analyze_repository → design_architecture
      design_architecture → architecture_approval ─┬─ rejected → design_architecture
                                                    └─ approved → select_technology
      select_technology → technology_approval ─┬─ rejected → select_technology
                                                └─ approved → create_implementation_plan
      create_implementation_plan → plan_approval ─┬─ rejected → create_implementation_plan
                                                   └─ approved → prepare_development

Build phase:

      prepare_development → implement_task → build → test
      test ─┬─ pass → commit → implement_task (next task) … → code_review
            └─ fail → debug → build                       (≤ limits.max_debug_iterations)
      implement_task / debug ── change request → change_request_approval
            ├─ approved → design_architecture / select_technology / create_implementation_plan
            └─ rejected → back to implement_task / debug
      code_review ─┬─ blocking findings → debug → build → test → code_review  (≤ max_review_rounds)
                   └─ ok → security_validation ─┬─ secrets → debug … → security_validation
                                                └─ ok → final_validation
      final_validation ─┬─ fail → debug … → final_validation
                        └─ pass → release_approval ─┬─ rejected → debug … → code_review
                                                     └─ approved → release → END (COMPLETED)

      any working node ── failure → handle_failure ─┬─ retry → failed node
                                                     └─ abort → END (CANCELLED)

Every conditional edge uses ``route_by_lifecycle``: the next node is a pure
function of the lifecycle status that deterministic node code set.
The human-readable version lives in docs/workflows/software_factory_execution_flow.md.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from software_factory.orchestration.nodes.agent_nodes import (
    make_analyze_requirements,
    make_create_implementation_plan,
    make_design_architecture,
    make_select_technology,
)
from software_factory.orchestration.nodes.deterministic_nodes import (
    make_analyze_repository,
    make_load_project,
)
from software_factory.orchestration.nodes.development_nodes import (
    make_build,
    make_debug,
    make_implement_task,
    make_prepare_development,
    make_test,
)
from software_factory.orchestration.nodes.human_nodes import (
    ARCHITECTURE_GATE,
    PLAN_GATE,
    RELEASE_GATE,
    REQUIREMENT_GATE,
    TECHNOLOGY_GATE,
    make_approval_gate,
    make_change_request_gate,
    make_handle_failure,
    make_wait_for_user_input,
)
from software_factory.orchestration.nodes.quality_nodes import (
    make_code_review,
    make_final_validation,
    make_release,
    make_security_validation,
)
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.orchestration.routers.lifecycle_router import (
    ROUTE_TARGETS,
    route_by_lifecycle,
)
from software_factory.orchestration.state import FactoryState

NodeFn = Callable[[FactoryState], dict[str, Any]]


def _persisting(deps: WorkflowDependencies, node: NodeFn, name: str) -> NodeFn:
    """Mirror state to human-readable files after every node completes."""

    def wrapped(state: FactoryState) -> dict[str, Any]:
        update = node(state)
        merged = {**state, **update, "current_stage": name}
        deps.recorder.persist_state(merged)
        return {**update, "current_stage": name}

    wrapped.__name__ = name
    return wrapped


def build_workflow(deps: WorkflowDependencies) -> StateGraph:
    nodes: dict[str, NodeFn] = {
        "load_project": make_load_project(deps),
        "analyze_requirements": make_analyze_requirements(deps),
        "wait_for_user_input": make_wait_for_user_input(deps),
        "requirement_approval": make_approval_gate(deps, REQUIREMENT_GATE),
        "analyze_repository": make_analyze_repository(deps),
        "design_architecture": make_design_architecture(deps),
        "architecture_approval": make_approval_gate(deps, ARCHITECTURE_GATE),
        "select_technology": make_select_technology(deps),
        "technology_approval": make_approval_gate(deps, TECHNOLOGY_GATE),
        "create_implementation_plan": make_create_implementation_plan(deps),
        "plan_approval": make_approval_gate(deps, PLAN_GATE),
        "prepare_development": make_prepare_development(deps),
        "implement_task": make_implement_task(deps),
        "build": make_build(deps),
        "test": make_test(deps),
        "debug": make_debug(deps),
        "change_request_approval": make_change_request_gate(deps),
        "code_review": make_code_review(deps),
        "security_validation": make_security_validation(deps),
        "final_validation": make_final_validation(deps),
        "release_approval": make_approval_gate(deps, RELEASE_GATE),
        "release": make_release(deps),
        "handle_failure": make_handle_failure(deps),
    }
    graph = StateGraph(FactoryState)
    for name, node in nodes.items():
        graph.add_node(name, cast(Any, _persisting(deps, node, name)))
        graph.add_conditional_edges(name, route_by_lifecycle, ROUTE_TARGETS)
    graph.add_edge(START, "load_project")
    return graph


def compile_workflow(
    deps: WorkflowDependencies, checkpointer: BaseCheckpointSaver
) -> CompiledStateGraph:
    return build_workflow(deps).compile(checkpointer=checkpointer)
