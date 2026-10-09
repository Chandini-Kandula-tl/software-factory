"""Agent contract: minimal context, required inputs, deterministic output validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.agents.architecture import ArchitectureAgent
from software_factory.agents.catalog import IMPLEMENTED_AGENTS
from software_factory.agents.implementation_planning import ImplementationPlanningAgent
from software_factory.agents.prompts import PromptLibrary
from software_factory.agents.requirement import RequirementAgent
from software_factory.agents.technology_selection import TechnologySelectionAgent
from software_factory.domain.errors import AgentOutputError
from software_factory.llm.models import Capability, LLMResponse
from tests.support import fixtures as fx

PROMPTS = PromptLibrary(Path(__file__).resolve().parents[2] / "prompts")


class RecordingLLM:
    """Minimal LLMClient: returns a fixed parsed output and records requests."""

    def __init__(self, output) -> None:
        self.output = output
        self.requests = []

    def generate(self, request):  # pragma: no cover - agents use structured calls
        raise NotImplementedError

    def generate_structured(self, request, schema):
        self.requests.append(request)
        return LLMResponse(
            content="{}",
            provider="fake",
            model="fake-model",
            model_profile=request.model_profile,
            parsed=schema.model_validate(self.output),
        )

    def supports(self, capability: Capability | str, profile=None) -> bool:
        return True


def test_every_agent_declares_a_complete_contract():
    for agent_cls in IMPLEMENTED_AGENTS:
        spec = agent_cls.spec
        assert spec.name and spec.version and spec.purpose
        assert spec.llm_profile in {"reasoning", "coding"}
        assert spec.required_inputs
        # Least privilege: only the Coding Agent touches files (applied by the workflow).
        expected_tools = ("filesystem",) if spec.name == "coding-agent" else ()
        assert tuple(str(t) for t in spec.allowed_tools) == expected_tools
        assert (PROMPTS.prompts_dir / spec.prompt_dir / "system.md").exists()
        assert (PROMPTS.prompts_dir / spec.prompt_dir / "task.md").exists()


def test_context_contains_only_declared_inputs():
    agent = ArchitectureAgent(RecordingLLM(fx.architecture()), PROMPTS)
    context = agent.build_context(
        "p",
        "Project",
        "architecture",
        {
            "requirements": {"summary": "x"},
            "requirements_input": "raw text",
            "technology_selection": {"x": 1},
            "repository_analysis": None,
        },
    )
    assert set(context.inputs) == {"requirements"}


def test_agent_run_returns_typed_result_with_trace():
    llm = RecordingLLM(fx.clear_requirements())
    agent = RequirementAgent(llm, PROMPTS)
    result = agent.run(
        agent.build_context("p", "Demo", "requirements", {"requirements_input": "Build a thing"})
    )
    assert result.output.functional_requirements[0].id == "FR-1"
    assert result.agent == "requirement-agent" and result.model == "fake-model"
    assert set(result.prompt_versions) == {"requirement/system.md", "requirement/task.md"}
    request = llm.requests[0]
    assert request.model_profile == "reasoning"
    assert request.metadata == {"project_id": "p", "agent": "requirement-agent"}
    assert "Build a thing" in request.messages[1].content


def test_missing_required_inputs_fail_before_llm_call():
    llm = RecordingLLM(fx.technology())
    agent = TechnologySelectionAgent(llm, PROMPTS)
    with pytest.raises(AgentOutputError, match="architecture"):
        agent.run(agent.build_context("p", "Demo", "technology", {"requirements": {"a": 1}}))
    assert llm.requests == []


def test_requirement_validation_rejects_duplicate_ids():
    output = fx.clear_requirements()
    output["functional_requirements"].append({"id": "FR-1", "description": "dup"})
    agent = RequirementAgent(RecordingLLM(output), PROMPTS)
    with pytest.raises(AgentOutputError, match="Duplicate"):
        agent.run(agent.build_context("p", "Demo", "requirements", {"requirements_input": "x"}))


def test_architecture_warns_about_unmapped_requirements():
    proposal = fx.architecture()
    proposal["components"] = [{"name": "API", "responsibility": "x", "requirement_ids": ["FR-9"]}]
    agent = ArchitectureAgent(RecordingLLM(proposal), PROMPTS)
    result = agent.run(
        agent.build_context("p", "Demo", "architecture", {"requirements": fx.clear_requirements()})
    )
    assert any("FR-9" in w for w in result.warnings)
    assert any("not mapped" in w for w in result.warnings)


def test_technology_validation_rejects_duplicate_categories():
    selection = fx.technology()
    selection["choices"].append({"category": "Backend", "choice": "Django", "rationale": "x"})
    agent = TechnologySelectionAgent(RecordingLLM(selection), PROMPTS)
    with pytest.raises(AgentOutputError, match="backend"):
        agent.run(
            agent.build_context(
                "p", "Demo", "technology", {"requirements": {"a": 1}, "architecture": {"b": 2}}
            )
        )


def test_plan_validation_rejects_dependency_cycles():
    plan = fx.plan()
    plan["tasks"][0]["depends_on"] = ["T-2"]
    agent = ImplementationPlanningAgent(RecordingLLM(plan), PROMPTS)
    inputs = {"requirements": {"a": 1}, "architecture": {"b": 2}, "technology_selection": {"c": 3}}
    with pytest.raises(AgentOutputError, match="cycle"):
        agent.run(agent.build_context("p", "Demo", "implementation_plan", inputs))
