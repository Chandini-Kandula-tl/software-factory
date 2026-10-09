"""Testing Agent — explains why a build or test run failed and how to fix it.

It never decides whether tests passed: that comes only from the test command's
real exit code. It classifies the failure so the workflow can stop for a human
when the problem is the environment rather than the code.
"""

from __future__ import annotations

from typing import ClassVar

from software_factory.agents.base import AgentSpec, LLMAgent
from software_factory.domain.development import TestFailureAnalysis


class TestingAgent(LLMAgent[TestFailureAnalysis]):
    __test__ = False  # not a pytest test class

    spec: ClassVar[AgentSpec] = AgentSpec(
        name="testing-agent",
        version="1.0.0",
        purpose="Analyze real build/test failures: classify the cause, find the root cause and "
        "propose a concrete fix for the Coding Agent.",
        llm_profile="coding",
        prompt_dir="testing",
        output_schema=TestFailureAnalysis,
        required_inputs=("verification",),
        optional_inputs=(
            "task",
            "acceptance_criteria",
            "commands",
            "relevant_files",
            "human_guidance",
        ),
        allowed_tools=(),
        capabilities=("failure_analysis", "test_strategy"),
    )
