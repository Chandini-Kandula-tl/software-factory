"""Requirement analysis schemas.

``RequirementAnalysis`` is the structured output the Requirement Agent must
return. The factory validates it before anything reaches workflow state.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ImpactArea(StrEnum):
    FUNCTIONALITY = "functionality"
    ARCHITECTURE = "architecture"
    TECHNOLOGY = "technology"
    SECURITY = "security"
    DATA = "data"
    INTEGRATIONS = "integrations"
    DEPLOYMENT = "deployment"
    TESTING = "testing"
    NON_FUNCTIONAL = "non_functional"
    OTHER = "other"


class RequirementItem(BaseModel):
    id: str = Field(description="Stable id such as FR-1 or NFR-2")
    description: str
    priority: str = Field(default="must", description="must | should | could")
    source: str = Field(default="user", description="user | clarification | assumption")


class AcceptanceCriterion(BaseModel):
    id: str = Field(description="Stable id such as AC-1")
    requirement_ids: list[str] = Field(default_factory=list)
    description: str


class ProposedQuestion(BaseModel):
    """A clarification question as proposed by the Requirement Agent."""

    question: str
    reason: str = ""
    impact_area: ImpactArea = ImpactArea.OTHER
    required: bool = True


class RequirementAnalysis(BaseModel):
    summary: str
    application_type: str = ""
    functional_requirements: list[RequirementItem] = Field(default_factory=list)
    non_functional_requirements: list[RequirementItem] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_questions: list[ProposedQuestion] = Field(default_factory=list)


class ClarificationQuestion(BaseModel):
    """A question the factory is asking the user. Ids are factory-assigned."""

    id: str
    question: str
    reason: str = ""
    impact_area: ImpactArea = ImpactArea.OTHER
    required: bool = True
    round: int = 1


class ClarificationAnswer(BaseModel):
    question_id: str
    question: str
    answer: str
    round: int
    answered_at: str
