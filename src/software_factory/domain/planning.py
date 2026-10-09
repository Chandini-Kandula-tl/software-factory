"""Implementation plan schema (Implementation Planning Agent output)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from software_factory.domain.development import ProjectCommand


class ImplementationTask(BaseModel):
    id: str = Field(description="Stable id such as T-1")
    title: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    acceptance_criteria_ids: list[str] = Field(default_factory=list)
    likely_files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


class Milestone(BaseModel):
    name: str
    task_ids: list[str] = Field(default_factory=list)


class ProjectCommands(BaseModel):
    """How the generated project is set up, checked and tested.

    These commands are approved by the human together with the plan and are
    the only commands the build phase runs. Test results come from ``test``.
    """

    setup: list[ProjectCommand] = Field(
        default_factory=list, description="Install dependencies, e.g. npm install"
    )
    format: list[ProjectCommand] = Field(default_factory=list)
    lint: list[ProjectCommand] = Field(default_factory=list)
    build: list[ProjectCommand] = Field(default_factory=list)
    test: list[ProjectCommand] = Field(
        min_length=1, description="Runs the automated test suite; must exit 0 when tests pass"
    )
    package: list[ProjectCommand] = Field(
        default_factory=list, description="Builds the release artifact, if any"
    )
    audit: list[ProjectCommand] = Field(
        default_factory=list, description="Dependency vulnerability audit, if available"
    )


class ImplementationPlan(BaseModel):
    summary: str
    release_artifact: str = Field(
        description="Deliverable the project produces, e.g. source repo, package, container image"
    )
    commands: ProjectCommands
    milestones: list[Milestone] = Field(default_factory=list)
    tasks: list[ImplementationTask] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
