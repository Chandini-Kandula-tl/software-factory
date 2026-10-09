"""Technology selection schema (Technology Selection Agent output).

No technology is hard-coded here: every category is chosen per project.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TechnologyChoice(BaseModel):
    category: str = Field(description="e.g. frontend, backend, database, deployment, testing")
    choice: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class TechnologySelection(BaseModel):
    summary: str
    choices: list[TechnologyChoice] = Field(min_length=1)
    not_applicable: list[str] = Field(
        default_factory=list,
        description="Categories deliberately left out (e.g. 'cache: not needed')",
    )
    tradeoffs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    existing_project_compatibility: str = ""
