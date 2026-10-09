"""Architecture proposal schema (Architecture Agent output)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArchitectureComponent(BaseModel):
    name: str
    responsibility: str
    interfaces: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(
        default_factory=list, description="Requirements this component satisfies"
    )


class ArchitectureAlternative(BaseModel):
    name: str
    summary: str
    reason_not_chosen: str = ""


class ArchitectureProposal(BaseModel):
    style: str = Field(description="e.g. modular monolith, client-server, event-driven")
    overview: str
    components: list[ArchitectureComponent] = Field(min_length=1)
    boundaries: list[str] = Field(default_factory=list)
    data_flow: list[str] = Field(default_factory=list)
    external_integrations: list[str] = Field(default_factory=list)
    security_considerations: list[str] = Field(default_factory=list)
    scalability_considerations: list[str] = Field(default_factory=list)
    advantages: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    alternatives: list[ArchitectureAlternative] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
