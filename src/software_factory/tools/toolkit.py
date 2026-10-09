"""Least-privilege tool provisioning.

Tools are handed out by name, and only the names a caller is allowed to use.
The workflow asks for an agent's ``allowed_tools`` when it acts on that agent's
behalf (e.g. applying the Coding Agent's file changes), so an agent that does
not declare a tool can never have it used for it. Design-phase agents declare
none.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from software_factory.config.settings import SecurityConfig
from software_factory.tools.base import ToolName, ToolPermissionError
from software_factory.tools.filesystem import FilesystemTool
from software_factory.tools.git import GitTool
from software_factory.tools.project_commands import STEP_TOOLS
from software_factory.tools.shell import ShellTool


class ToolKit:
    def __init__(self, workspace_root: Path, security: SecurityConfig) -> None:
        self._root = workspace_root
        self._security = security

    def shell(self) -> ShellTool:
        return ShellTool(
            self._root,
            self._security.allowed_commands,
            timeout_seconds=self._security.command_timeout_seconds,
            enabled=self._security.shell_enabled,
            tool_name="project-command",
        )

    def _build(self, name: ToolName) -> Any:
        if name is ToolName.FILESYSTEM:
            return FilesystemTool(self._root)
        if name is ToolName.GIT:
            return GitTool(self._root, enabled=self._security.shell_enabled)
        if name is ToolName.SHELL:
            return self.shell()
        for tool_class in STEP_TOOLS.values():
            if tool_class.tool_name is name:
                return tool_class(self.shell())
        raise ToolPermissionError(f"Tool '{name}' is not available in this build")

    def tools_for(self, allowed: Iterable[ToolName | str]) -> dict[str, Any]:
        return {str(name): self._build(ToolName(name)) for name in allowed}

    def require(self, name: ToolName | str, allowed: Iterable[ToolName | str]) -> Any:
        """The named tool, if and only if it is in ``allowed``."""
        if str(name) not in {str(a) for a in allowed}:
            raise ToolPermissionError(f"Tool '{name}' is not allowed here")
        return self._build(ToolName(name))
