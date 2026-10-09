"""Code Review Agent — reviews the implementation against approved decisions."""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentContext, AgentSpec, LLMAgent
from software_factory.domain.development import ReviewReport
from software_factory.domain.errors import AgentOutputError


class CodeReviewAgent(LLMAgent[ReviewReport]):
    spec: ClassVar[AgentSpec] = AgentSpec(
        name="code-review-agent",
        version="1.0.0",
        purpose="Review the implementation against requirements, architecture and technology; "
        "report defects, security concerns, missing tests and deviations by severity.",
        llm_profile="coding",
        prompt_dir="code_review",
        output_schema=ReviewReport,
        required_inputs=("requirements", "architecture", "technology_selection", "changes"),
        optional_inputs=("implementation_plan", "file_tree", "test_results", "previous_review"),
        allowed_tools=(),
        capabilities=("code_review",),
    )

    def validate_output(self, output: ReviewReport, context: AgentContext) -> list[str]:
        ids = [f.id for f in output.findings]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise AgentOutputError(f"Duplicate review finding ids: {duplicates}")
        requirements = context.inputs.get("requirements") or {}
        known = {
            r["id"]
            for r in requirements.get("functional_requirements", [])
            + requirements.get("non_functional_requirements", [])
        }
        reviewed = {c.requirement_id for c in output.requirement_coverage}
        missing = sorted(known - reviewed)
        return [f"Review did not assess requirements {missing}"] if missing else []
