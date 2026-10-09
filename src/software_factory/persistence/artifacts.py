"""Per-project artifact store: Markdown for humans, JSON for machines.

All writes are atomic (write to a temp file, then rename) so an interrupted
process never leaves a half-written artifact behind, and every write passes
through secret redaction so provider errors or tool output can never leak a
key into project files.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from software_factory.domain.execution import ExecutionRecord
from software_factory.observability.redaction import redact

STATE_FILE = "state.json"
HISTORY_FILE = "execution-history.json"
ANSWERS_FILE = "clarification-answers.json"
REQUIREMENTS_INPUT_FILE = "requirements-input.md"


class ArtifactStore:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.artifacts_dir = project_dir / "artifacts"

    def ensure_layout(self) -> None:
        for directory in (self.project_dir, self.artifacts_dir, self.project_dir / "checkpoints"):
            directory.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.project_dir / name

    # -- generic IO ---------------------------------------------------------

    def write_text(self, name: str, content: str) -> Path:
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(redact(content))
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target

    def write_json(self, name: str, data: Any) -> Path:
        if isinstance(data, BaseModel):
            data = data.model_dump(mode="json")
        return self.write_text(name, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def read_json(self, name: str, default: Any = None) -> Any:
        target = self.path(name)
        if not target.exists():
            return default
        return json.loads(target.read_text(encoding="utf-8"))

    def read_text(self, name: str) -> str | None:
        target = self.path(name)
        return target.read_text(encoding="utf-8") if target.exists() else None

    # -- stage artifacts ------------------------------------------------------

    def write_stage_artifact(
        self, stem: str, model: BaseModel, markdown: str, revision: int
    ) -> None:
        """Write ``<stem>.json`` + ``<stem>.md`` and keep a copy of this revision."""
        self.write_json(f"{stem}.json", model)
        self.write_text(f"{stem}.md", markdown)
        self.write_json(f"artifacts/{stem}.rev{revision}.json", model)

    def write_artifact(self, stem: str, model: BaseModel, markdown: str) -> None:
        """Write ``<stem>.json`` + ``<stem>.md`` (latest only, no revision copy)."""
        self.write_json(f"{stem}.json", model)
        self.write_text(f"{stem}.md", markdown)

    # -- versions of a released app --------------------------------------------

    def version_dir(self, version: int) -> Path:
        return self.project_dir / "versions" / f"v{int(version)}"

    def archive_version(
        self, version: int, documents: tuple[str, ...], ongoing: tuple[str, ...]
    ) -> None:
        """Keep a released version's documents in ``versions/v<N>/``.

        Stage documents (and their JSON) move there, so the next version's
        pages show only what that version produced; ``ongoing`` documents
        (project-wide logs) are copied and keep growing in place.
        """
        target = self.version_dir(version)
        target.mkdir(parents=True, exist_ok=True)
        for name in documents:
            source = self.path(name)
            if not source.is_file():
                continue
            if name in ongoing:
                (target / name).write_bytes(source.read_bytes())
                continue
            os.replace(source, target / name)
            data = source.with_suffix(".json")
            if data.is_file():
                os.replace(data, target / data.name)

    def read_version_text(self, version: int, name: str) -> str | None:
        target = self.version_dir(version) / name
        return target.read_text(encoding="utf-8") if target.is_file() else None

    # -- execution history ----------------------------------------------------

    def append_history(self, record: ExecutionRecord) -> None:
        history = self.read_json(HISTORY_FILE, default=[])
        history.append(record.model_dump(mode="json"))
        self.write_json(HISTORY_FILE, history)

    def read_history(self) -> list[dict[str, Any]]:
        return list(self.read_json(HISTORY_FILE, default=[]))

    # -- clarification answers -------------------------------------------------

    def read_answers(self) -> dict[str, dict[str, Any]]:
        return dict(self.read_json(ANSWERS_FILE, default={}))

    def save_answer(self, question_id: str, answer: str, answered_at: str) -> None:
        answers = self.read_answers()
        answers[question_id] = {"answer": answer, "answered_at": answered_at}
        self.write_json(ANSWERS_FILE, answers)

    def clear_answers(self, question_ids: list[str]) -> None:
        answers = self.read_answers()
        for question_id in question_ids:
            answers.pop(question_id, None)
        self.write_json(ANSWERS_FILE, answers)
