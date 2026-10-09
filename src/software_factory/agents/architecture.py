"""Architecture Agent — proposes an application architecture from approved requirements."""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.architecture import ArchitectureProposal


class ArchitectureAgent(LLMAgent[ArchitectureProposal]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="architecture-agent",
        version="1.2.0",
        purpose="Design the application architecture: components, boundaries, data flow, "
        "integrations, security and scalability concerns, alternatives and trade-offs.",
        llm_profile="reasoning",
        prompt_dir="architecture",
        output_schema=ArchitectureProposal,
        required_inputs=("requirements",),
        optional_inputs=(
            "repository_analysis",
            "impact_analysis",
            "previous_architecture",
            "update_request",
        ),
        allowed_tools=(),
        capabilities=("architecture_design",),
    )

    def validate_output(self, output: ArchitectureProposal, context: AgentContext) -> list[str]:
        requirements = context.inputs.get("requirements") or {}
        known = {
            r["id"]
            for r in requirements.get("functional_requirements", [])
            + requirements.get("non_functional_requirements", [])
        }
        covered = {rid for c in output.components for rid in c.requirement_ids}
        warnings = [
            f"Component references unknown requirement {rid}" for rid in sorted(covered - known)
        ]
        functional = {r["id"] for r in requirements.get("functional_requirements", [])}
        uncovered = sorted(functional - covered)
        if uncovered:
            warnings.append(f"Functional requirements not mapped to a component: {uncovered}")
        return warnings
