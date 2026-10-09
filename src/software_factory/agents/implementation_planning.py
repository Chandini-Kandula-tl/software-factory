"""Implementation Planning Agent — turns approved decisions into ordered tasks."""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.errors import AgentOutputError
from software_factory.domain.planning import ImplementationPlan


class ImplementationPlanningAgent(LLMAgent[ImplementationPlan]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="implementation-planning-agent",
        version="1.3.0",
        purpose="Convert approved requirements, architecture and technology into an ordered, "
        "traceable implementation plan with milestones, tests and a release artifact.",
        llm_profile="reasoning",
        prompt_dir="implementation_planning",
        output_schema=ImplementationPlan,
        required_inputs=("requirements", "architecture", "technology_selection"),
        optional_inputs=(
            "repository_analysis",
            "impact_analysis",
            "environment",
            "previous_implementation_plan",
            "update_request",
        ),
        allowed_tools=(),
        capabilities=("implementation_planning",),
    )

    def validate_output(self, output: ImplementationPlan, context: AgentContext) -> list[str]:
        ids = [t.id for t in output.tasks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise AgentOutputError(f"Duplicate task ids: {duplicates}")
        known = set(ids)
        warnings = [
            f"{t.id} depends on unknown task {dep}"
            for t in output.tasks
            for dep in t.depends_on
            if dep not in known
        ]
        cycle = _find_cycle({t.id: [d for d in t.depends_on if d in known] for t in output.tasks})
        if cycle:
            raise AgentOutputError(f"Task dependencies contain a cycle: {' -> '.join(cycle)}")
        untraced = [t.id for t in output.tasks if not t.requirement_ids]
        if untraced:
            warnings.append(f"Tasks not traced to any requirement: {untraced}")
        return warnings


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visiting: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            return visiting[visiting.index(node) :] + [node]
        if node in done:
            return None
        visiting.append(node)
        for dep in graph.get(node, []):
            found = visit(dep)
            if found:
                return found
        visiting.pop()
        done.add(node)
        return None

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return None
