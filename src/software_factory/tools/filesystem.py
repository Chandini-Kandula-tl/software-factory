"""Filesystem tool confined to a single root directory."""

from __future__ import annotations

from pathlib import Path

from software_factory.tools.base import PathOutsideWorkspaceError, ToolPermissionError

DEFAULT_MAX_FILE_BYTES = 2_000_000


class FilesystemTool:
    def __init__(self, root: Path, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        self.root = root.resolve()
        self.max_file_bytes = max_file_bytes

    def resolve(self, relative: str | Path) -> Path:
        """Resolve ``relative`` inside the root; reject traversal and symlink escapes."""
        candidate = Path(relative)
        if candidate.is_absolute():
            raise PathOutsideWorkspaceError(f"Absolute paths are not allowed: {relative}")
        resolved = (self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PathOutsideWorkspaceError(f"Path escapes the workspace: {relative}")
        return resolved

    def exists(self, relative: str | Path) -> bool:
        return self.resolve(relative).exists()

    def read_text(self, relative: str | Path) -> str:
        path = self.resolve(relative)
        if path.stat().st_size > self.max_file_bytes:
            raise ToolPermissionError(f"File exceeds {self.max_file_bytes} bytes: {relative}")
        return path.read_text(encoding="utf-8")

    def write_text(self, relative: str | Path, content: str) -> Path:
        if len(content.encode("utf-8")) > self.max_file_bytes:
            raise ToolPermissionError(f"Content exceeds {self.max_file_bytes} bytes")
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def delete(self, relative: str | Path) -> bool:
        path = self.resolve(relative)
        if path.is_dir():
            raise ToolPermissionError(f"Refusing to delete a directory: {relative}")
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_dir(self, relative: str | Path = ".") -> list[str]:
        path = self.resolve(relative)
        return sorted(
            str(child.relative_to(self.root)) + ("/" if child.is_dir() else "")
            for child in path.iterdir()
        )
