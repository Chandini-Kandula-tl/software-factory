"""Project creation, loading and isolation."""

from __future__ import annotations

import shutil
from pathlib import Path

from software_factory import __version__
from software_factory.config.settings import FactoryConfig
from software_factory.domain.errors import (
    FactoryError,
    ProjectAlreadyExistsError,
    ProjectFolderMissingError,
)
from software_factory.domain.lifecycle import LifecycleStatus
from software_factory.domain.project import (
    Project,
    ProjectMode,
    ProjectSummary,
    slugify_project_name,
    validate_project_id,
)
from software_factory.observability.clock import utc_now
from software_factory.persistence.artifacts import REQUIREMENTS_INPUT_FILE, ArtifactStore
from software_factory.persistence.database import ProjectRegistry
from software_factory.persistence.markdown import render_project

DATABASE_FILENAME = "factory.db"


class ProjectService:
    def __init__(self, config: FactoryConfig) -> None:
        self.workspace = config.workspace_path
        self.projects_root = self.workspace / "projects"
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.registry = ProjectRegistry(self.workspace / DATABASE_FILENAME)

    def project_dir(self, project_id: str) -> Path:
        validate_project_id(project_id)
        path = (self.projects_root / project_id).resolve()
        # Defence in depth: the id pattern already forbids traversal.
        if path.parent != self.projects_root.resolve():
            raise FactoryError(f"Project path escapes the workspace: {project_id}")
        return path

    def store(self, project_id: str) -> ArtifactStore:
        return ArtifactStore(self.project_dir(project_id))

    def create(
        self,
        name: str,
        requirements: str | None = None,
        description: str = "",
        repository_path: str | None = None,
    ) -> Project:
        project_id = validate_project_id(slugify_project_name(name))
        if self.registry.exists(project_id) or self.project_dir(project_id).exists():
            raise ProjectAlreadyExistsError(
                f"Project '{project_id}' already exists",
                hint="Choose another name or continue the existing project.",
            )
        repo: Path | None = None
        if repository_path:
            repo = Path(repository_path).expanduser().resolve()
            if not repo.is_dir():
                raise FactoryError(f"Repository path does not exist: {repo}")
            if not (repo / ".git").exists():
                raise FactoryError(
                    f"{repo} is not the top folder of a git repository",
                    hint="Choose the app's own folder (the one that contains .git), e.g. "
                    "~/Documents/GitHub/my-app — not a folder that holds several projects. "
                    "For a folder without git, run `git init` and commit first.",
                )
        project = Project(
            project_id=project_id,
            name=name,
            description=description,
            mode=ProjectMode.EXISTING if repo else ProjectMode.GREENFIELD,
            repository_path=str(repo) if repo else None,
            created_at=utc_now(),
            factory_version=__version__,
        )
        store = self.store(project_id)
        store.ensure_layout()
        self.registry.add(project)
        store.write_text("project.md", render_project(project, LifecycleStatus.CREATED.value))
        if requirements:
            self.set_requirements(project_id, requirements)
        return project

    def set_requirements(self, project_id: str, requirements: str) -> None:
        self.load(project_id)
        text = requirements.strip()
        if not text:
            raise FactoryError("Requirements text is empty")
        self.store(project_id).write_text(REQUIREMENTS_INPUT_FILE, text + "\n")

    def requirements_input(self, project_id: str) -> str | None:
        return self.store(project_id).read_text(REQUIREMENTS_INPUT_FILE)

    def load(self, project_id: str) -> Project:
        validate_project_id(project_id)
        return self.registry.get(project_id)

    def list(self) -> list[ProjectSummary]:
        return [
            summary.model_copy(update={"folder_missing": self.folder_missing(summary.project_id)})
            for summary in self.registry.list()
        ]

    def folder_missing(self, project_id: str) -> bool:
        return not self.project_dir(project_id).is_dir()

    def require_folder(self, project_id: str) -> None:
        """Refuse to work on a project whose folder was removed (never silently recreate it)."""
        if self.folder_missing(project_id):
            raise ProjectFolderMissingError(
                f"The folder of project '{project_id}' no longer exists",
                hint=f"Remove it from the list with `factory delete {project_id}` "
                "(or the Delete button in the web UI).",
            )

    def delete(self, project_id: str) -> None:
        """Remove a project from the register and delete its folder.

        Only the project's own folder under the workspace is deleted. For
        existing-code projects the user's repository is never touched.
        """
        self.load(project_id)
        project_dir = self.project_dir(project_id)
        if project_dir.is_dir():
            shutil.rmtree(project_dir)
        self.registry.remove(project_id)
