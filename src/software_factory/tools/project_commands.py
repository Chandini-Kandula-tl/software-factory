"""Build / test / lint / format / package-manager / dependency-audit tools.

Each tool runs the approved project commands for one step through
``ShellTool`` (allowlist, confined working directory, timeout, scrubbed
environment, redacted output). The commands come from the human-approved
implementation plan — nothing here assumes a particular stack. Success is the
real exit code: 0 and no timeout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from software_factory.tools.base import ToolName, ToolPermissionError, ToolResult
from software_factory.tools.shell import ShellTool


class ProjectCommandTool:
    tool_name: ToolName = ToolName.SHELL

    def __init__(self, shell: ShellTool) -> None:
        self._shell = shell

    def run(
        self,
        argv: Sequence[str],
        cwd: Path | None = None,
        extra_env: Mapping[str, str | None] | None = None,
    ) -> ToolResult:
        if not argv:
            raise ToolPermissionError(f"Empty {self.tool_name} command")
        result = self._shell.run(argv, cwd=cwd, extra_env=extra_env)
        return result.model_copy(update={"tool": str(self.tool_name)})


class BuildTool(ProjectCommandTool):
    tool_name = ToolName.BUILD


class TestTool(ProjectCommandTool):
    tool_name = ToolName.TEST
    __test__ = False  # not a pytest test class


class LintTool(ProjectCommandTool):
    tool_name = ToolName.LINT


class FormatterTool(ProjectCommandTool):
    tool_name = ToolName.FORMATTER


class PackageManagerTool(ProjectCommandTool):
    tool_name = ToolName.PACKAGE_MANAGER


class DependencyAuditTool(ProjectCommandTool):
    tool_name = ToolName.DEPENDENCY_AUDIT


# Which tool runs each step of the plan's ``commands``.
STEP_TOOLS: dict[str, type[ProjectCommandTool]] = {
    "setup": PackageManagerTool,
    "format": FormatterTool,
    "lint": LintTool,
    "build": BuildTool,
    "test": TestTool,
    "package": BuildTool,
    "audit": DependencyAuditTool,
}


def tool_for_step(step: str, shell: ShellTool) -> ProjectCommandTool:
    try:
        return STEP_TOOLS[step](shell)
    except KeyError as exc:
        raise ToolPermissionError(f"No tool runs project step '{step}'") from exc
