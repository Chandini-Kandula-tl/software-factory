"""Helpers for the generated project's source tree.

Everything here is deterministic: which files exist, which files are relevant
to a task, and whether dependency manifests changed since the last setup run.
The Coding Agent only ever sees what these helpers select, within a budget.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from software_factory.tools.repository_analysis import SKIP_DIRS

DEFAULT_GITIGNORE = """\
# Added by Software Factory
node_modules/
.venv/
venv/
__pycache__/
*.pyc
dist/
build/
.pytest_cache/
.env
*.log
.DS_Store
"""

DEPENDENCY_MANIFESTS = frozenset(
    {
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "Gemfile",
        "composer.json",
    }
)


LOCK_FILES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock"}
)


def list_source_files(root: Path, limit: int = 400) -> list[str]:
    """Relative paths of project files (skipping VCS, dependency and build dirs)."""
    files: list[str] = []
    stack = [root]
    while stack and len(files) < limit:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), reverse=True)
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            else:
                files.append(entry.relative_to(root).as_posix())
    return sorted(files)[:limit]


def read_files(
    root: Path, paths: Iterable[str], budget_chars: int, per_file_chars: int = 12_000
) -> dict[str, str]:
    """Read files in the given order until the character budget is used up."""
    selected: dict[str, str] = {}
    used = 0
    for relative in dict.fromkeys(paths):
        if Path(relative).name in LOCK_FILES:
            continue  # machine-generated and huge; never useful to an agent
        path = (root / relative).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(text) > per_file_chars:
            text = text[:per_file_chars] + "\n… [truncated]"
        if used + len(text) > budget_chars:
            continue  # a smaller file later in the list may still fit
        selected[relative] = text
        used += len(text)
    return selected


def mentioned_files(text: str, known_files: Iterable[str]) -> list[str]:
    """Known project files whose path (or file name) appears in tool output."""
    return [
        path
        for path in known_files
        if path in text or (len(Path(path).name) > 4 and Path(path).name in text)
    ]


def manifest_fingerprint(root: Path) -> str:
    """Hash of every dependency manifest; changes mean setup must run again."""
    digest = hashlib.sha256()
    for relative in list_source_files(root, limit=5000):
        if Path(relative).name in DEPENDENCY_MANIFESTS:
            digest.update(relative.encode())
            digest.update((root / relative).read_bytes())
    return digest.hexdigest()[:16]
