"""Impact Analysis Agent — what a change affects before anyone approves it.

Used in two places (spec §36, §68.7):
- a change request raised during development, before the human decides;
- a new requirement on an existing repository, before architecture work.

Raw facts (changed files, completed tasks, repository analysis) come from
deterministic tools; the agent interprets them.
"""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.development import ImpactAnalysis


class ImpactAnalysisAgent(LLMAgent[ImpactAnalysis]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="impact-analysis-agent",
        version="1.0.0",
        purpose="Assess which requirements, components, files and tasks a change affects, "
        "what existing behaviour must be regression-tested, and the risks.",
        llm_profile="reasoning",
        prompt_dir="impact_analysis",
        output_schema=ImpactAnalysis,
        required_inputs=("mode", "requirements"),
        optional_inputs=(
            "change_request",
            "architecture",
            "implementation_plan",
            "repository_analysis",
            "completed_tasks",
            "changed_files",
        ),
        allowed_tools=(),
        capabilities=("impact_analysis",),
    )

    def validate_output(self, output: ImpactAnalysis, context: AgentContext) -> list[str]:
        requirements = context.inputs.get("requirements") or {}
        known = {
            r["id"]
            for r in requirements.get("functional_requirements", [])
            + requirements.get("non_functional_requirements", [])
        }
        unknown = sorted(set(output.affected_requirements) - known) if known else []
        return [f"Unknown requirement ids in impact analysis: {unknown}"] if unknown else []
