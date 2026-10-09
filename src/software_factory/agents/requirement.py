"""Requirement Agent — turns raw requirements into structured requirements.

It proposes clarification questions; it does not decide whether the workflow
waits for the user. That decision is deterministic (see orchestration nodes).
"""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.errors import AgentOutputError
from software_factory.domain.requirements import RequirementAnalysis


class RequirementAgent(LLMAgent[RequirementAnalysis]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="requirement-agent",
        version="1.1.0",
        purpose="Analyze user requirements, detect ambiguity, and produce structured "
        "requirements with acceptance criteria.",
        llm_profile="reasoning",
        prompt_dir="requirement",
        output_schema=RequirementAnalysis,
        required_inputs=("requirements_input",),
        optional_inputs=(
            "project_mode",
            "clarifications",
            "previous_requirements",
            "update_request",
        ),
        allowed_tools=(),
        capabilities=("requirements_analysis", "ambiguity_detection"),
    )

    def validate_output(self, output: RequirementAnalysis, context: AgentContext) -> list[str]:
        warnings: list[str] = []
        ids = [r.id for r in output.functional_requirements + output.non_functional_requirements]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise AgentOutputError(f"Duplicate requirement ids: {duplicates}")
        if not output.functional_requirements and not output.clarification_questions:
            raise AgentOutputError(
                "Requirement analysis has neither functional requirements nor questions"
            )
        known = set(ids)
        for criterion in output.acceptance_criteria:
            unknown = [r for r in criterion.requirement_ids if r not in known]
            if unknown:
                warnings.append(f"{criterion.id} references unknown requirements {unknown}")
        if output.needs_clarification and not output.clarification_questions:
            warnings.append("Agent flagged needs_clarification but proposed no questions")
        return warnings
