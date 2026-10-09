"""Deterministic repository analysis facts (existing-project mode)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GitState(BaseModel):
    is_repository: bool = False
    branch: str | None = None
    commit: str | None = None
    clean: bool | None = None
    changed_files: int = 0


class RepositoryAnalysis(BaseModel):
    path: str
    languages: dict[str, int] = Field(
        default_factory=dict, description="Language -> number of source files"
    )
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    build_systems: list[str] = Field(default_factory=list)
    test_locations: list[str] = Field(default_factory=list)
    configuration_files: list[str] = Field(default_factory=list)
    likely_entry_points: list[str] = Field(default_factory=list)
    top_level_structure: list[str] = Field(default_factory=list)
    total_files_scanned: int = 0
    git: GitState = Field(default_factory=GitState)
