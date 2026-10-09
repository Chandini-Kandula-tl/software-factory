"""Technology Selection Agent — chooses a stack per project, never a fixed default."""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.errors import AgentOutputError
from software_factory.domain.technology import TechnologySelection


class TechnologySelectionAgent(LLMAgent[TechnologySelection]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="technology-selection-agent",
        version="1.2.0",
        purpose="Select technologies dynamically from requirements, architecture and "
        "existing-project constraints, with rationale, alternatives and risks.",
        llm_profile="reasoning",
        prompt_dir="technology_selection",
        output_schema=TechnologySelection,
        required_inputs=("requirements", "architecture"),
        optional_inputs=(
            "repository_analysis",
            "environment",
            "previous_technology_selection",
            "update_request",
        ),
        allowed_tools=(),
        capabilities=("technology_comparison",),
    )

    def validate_output(self, output: TechnologySelection, context: AgentContext) -> list[str]:
        categories = [c.category.strip().lower() for c in output.choices]
        duplicates = sorted({c for c in categories if categories.count(c) > 1})
        if duplicates:
            raise AgentOutputError(f"Technology categories chosen more than once: {duplicates}")
        return [
            f"No rationale given for {c.category}: {c.choice}"
            for c in output.choices
            if not c.rationale.strip()
        ]
