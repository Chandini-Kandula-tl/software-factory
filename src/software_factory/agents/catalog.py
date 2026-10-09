"""Agent catalog: which agents exist and how to construct them.

Agents are reusable capabilities; workflows decide when they run.
"""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.agents.architecture import ArchitectureAgent
from software_factory.agents.code_review import CodeReviewAgent
from software_factory.agents.coding import CodingAgent
from software_factory.agents.impact_analysis import ImpactAnalysisAgent
from software_factory.agents.implementation_planning import ImplementationPlanningAgent
from software_factory.agents.prompts import PromptLibrary
from software_factory.agents.requirement import RequirementAgent
from software_factory.agents.technology_selection import TechnologySelectionAgent
from software_factory.agents.testing import TestingAgent
from software_factory.llm.interfaces import LLMClient

IMPLEMENTED_AGENTS = (
    RequirementAgent,
    ArchitectureAgent,
    TechnologySelectionAgent,
    ImplementationPlanningAgent,
    CodingAgent,
    TestingAgent,
    CodeReviewAgent,
    ImpactAnalysisAgent,
)


@dataclass
class FactoryAgents:
    requirement: RequirementAgent
    architecture: ArchitectureAgent
    technology_selection: TechnologySelectionAgent
    implementation_planning: ImplementationPlanningAgent
    coding: CodingAgent
    testing: TestingAgent
    code_review: CodeReviewAgent
    impact_analysis: ImpactAnalysisAgent


def build_agents(llm: LLMClient, prompts: PromptLibrary) -> FactoryAgents:
    return FactoryAgents(
        requirement=RequirementAgent(llm, prompts),
        architecture=ArchitectureAgent(llm, prompts),
        technology_selection=TechnologySelectionAgent(llm, prompts),
        implementation_planning=ImplementationPlanningAgent(llm, prompts),
        coding=CodingAgent(llm, prompts),
        testing=TestingAgent(llm, prompts),
        code_review=CodeReviewAgent(llm, prompts),
        impact_analysis=ImpactAnalysisAgent(llm, prompts),
    )
