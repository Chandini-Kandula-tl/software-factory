"""Project identity and metadata."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from software_factory.domain.errors import InvalidProjectIdError

PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class ProjectMode(StrEnum):
    GREENFIELD = "greenfield"
    EXISTING = "existing"


class Project(BaseModel):
    project_id: str
    name: str
    description: str = ""
    mode: ProjectMode = ProjectMode.GREENFIELD
    repository_path: str | None = None
    created_at: str
    factory_version: str
    paused: bool = False


def slugify_project_name(name: str) -> str:
    """Turn a human project name into a filesystem-safe project id."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:63]


def validate_project_id(project_id: str) -> str:
    """Reject ids that could escape the workspace or are otherwise malformed."""
    if not PROJECT_ID_PATTERN.match(project_id):
        raise InvalidProjectIdError(
            f"Invalid project id '{project_id}'",
            hint="Project ids use lowercase letters, digits and '-' (max 63 chars).",
        )
    return project_id


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    mode: ProjectMode
    created_at: str
    lifecycle_status: str = Field(default="CREATED")
    folder_missing: bool = False
