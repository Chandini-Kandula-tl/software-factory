"""Common tool result/error types.

Tool results are ground truth: ``success`` is derived from the real exit code
of the real process, never from anything an agent says.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from software_factory.domain.errors import FactoryError


class ToolName(StrEnum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    SHELL = "shell"
    BUILD = "build"
    TEST = "test"
    LINT = "lint"
    FORMATTER = "formatter"
    PACKAGE_MANAGER = "package_manager"
    DEPENDENCY_AUDIT = "dependency_audit"
    REPOSITORY_ANALYSIS = "repository_analysis"


class ToolResult(BaseModel):
    tool: str
    command: list[str] = Field(default_factory=list)
    cwd: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class ToolPermissionError(FactoryError):
    """The tool call is not allowed by policy (allowlist, workspace, approval)."""


class PathOutsideWorkspaceError(ToolPermissionError):
    """A path resolved outside the tool's permitted root."""
