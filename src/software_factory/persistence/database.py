"""SQLite project registry (``<workspace>/factory.db``).

Holds the list of projects and their headline status. Per-project workflow
state lives in each project's own checkpoint database, so projects never share
workflow storage.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from software_factory.domain.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from software_factory.domain.project import Project, ProjectMode, ProjectSummary

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    repository_path TEXT,
    created_at TEXT NOT NULL,
    factory_version TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    lifecycle_status TEXT NOT NULL DEFAULT 'CREATED'
);
"""


class ProjectRegistry:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add(self, project: Project) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO projects (project_id, name, description, mode, repository_path,"
                    " created_at, factory_version, paused) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        project.project_id,
                        project.name,
                        project.description,
                        project.mode.value,
                        project.repository_path,
                        project.created_at,
                        project.factory_version,
                        int(project.paused),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectAlreadyExistsError(
                f"Project '{project.project_id}' already exists"
            ) from exc

    def get(self, project_id: str) -> Project:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(
                f"Project '{project_id}' does not exist",
                hint="List projects with `factory list`.",
            )
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"],
            mode=ProjectMode(row["mode"]),
            repository_path=row["repository_path"],
            created_at=row["created_at"],
            factory_version=row["factory_version"],
            paused=bool(row["paused"]),
        )

    def exists(self, project_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        return row is not None

    def list(self) -> list[ProjectSummary]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        return [
            ProjectSummary(
                project_id=row["project_id"],
                name=row["name"],
                mode=ProjectMode(row["mode"]),
                created_at=row["created_at"],
                lifecycle_status=row["lifecycle_status"],
            )
            for row in rows
        ]

    def set_paused(self, project_id: str, paused: bool) -> None:
        self.get(project_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET paused = ? WHERE project_id = ?", (int(paused), project_id)
            )

    def remove(self, project_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))

    def set_lifecycle_status(self, project_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET lifecycle_status = ? WHERE project_id = ?",
                (status, project_id),
            )
