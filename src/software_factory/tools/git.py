"""Git tool with explicit, policy-checked operations.

Safe operations are exposed as methods. Destructive operations (push, reset,
clean, rebase, force deletes, ...) are refused unless the caller passes an
explicit human approval reference.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from software_factory.domain.repository import GitState
from software_factory.tools.base import ToolPermissionError, ToolResult
from software_factory.tools.shell import ShellTool

SAFE_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "rev-parse",
        "branch",
        "checkout",
        "switch",
        "add",
        "commit",
        "init",
        "show",
        "ls-files",
        "tag",
    }
)
DANGEROUS_SUBCOMMANDS = frozenset(
    {"push", "reset", "clean", "rebase", "filter-branch", "gc", "prune", "reflog", "rm", "stash"}
)
DANGEROUS_FLAGS = frozenset({"--force", "-f", "-D", "--hard", "--delete", "-d"})


# Values following these options are free text (e.g. commit messages), not flags.
_VALUE_OPTIONS = frozenset({"-m", "--message"})


def _flags(args: Sequence[str]) -> list[str]:
    flags, skip_next = [], False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_OPTIONS:
            skip_next = True
        flags.append(arg)
    return flags


def _is_dangerous(args: Sequence[str]) -> bool:
    subcommand, rest = args[0], _flags(args[1:])
    if subcommand in DANGEROUS_SUBCOMMANDS or any(a in DANGEROUS_FLAGS for a in rest):
        return True
    # `git checkout -- <path>` / `git checkout .` discard uncommitted changes.
    return subcommand == "checkout" and ("--" in rest or "." in rest)


_IDENTITY_DEFAULTS = {
    "GIT_AUTHOR_NAME": "Software Factory",
    "GIT_AUTHOR_EMAIL": "software-factory@localhost",
    "GIT_COMMITTER_NAME": "Software Factory",
    "GIT_COMMITTER_EMAIL": "software-factory@localhost",
}


def _identity_env(repository: Path) -> dict[str, str]:
    """Fallback commit identity; an identity from the environment or git config wins."""
    configured = subprocess.run(
        ["git", "config", "--get", "user.email"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if configured.stdout.strip():
        return {}
    return {k: v for k, v in _IDENTITY_DEFAULTS.items() if k not in os.environ}


class GitTool:
    def __init__(
        self, repository: Path, timeout_seconds: float = 60.0, enabled: bool = True
    ) -> None:
        self.repository = repository.resolve()
        # Git runs subprocesses, so it obeys security.shell_enabled like ShellTool.
        self._shell = ShellTool(
            self.repository,
            ["git"],
            timeout_seconds=timeout_seconds,
            enabled=enabled,
            tool_name="git",
        )

    def run(self, args: Sequence[str], approval_reference: str | None = None) -> ToolResult:
        if not args:
            raise ToolPermissionError("Empty git command")
        subcommand = args[0]
        dangerous = _is_dangerous(args)
        if subcommand not in SAFE_SUBCOMMANDS | DANGEROUS_SUBCOMMANDS:
            raise ToolPermissionError(f"git {subcommand} is not a permitted operation")
        if dangerous and not approval_reference:
            raise ToolPermissionError(
                f"git {' '.join(args)} is destructive and requires explicit human approval"
            )
        # Never prompt for signing keys; commits get a factory identity only when
        # the user has none configured (git reads these env vars first).
        return self._shell.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            extra_env=_identity_env(self.repository),
        )

    # -- convenience operations --------------------------------------------

    def init(self) -> ToolResult:
        return self.run(["init"])

    def status(self) -> ToolResult:
        return self.run(["status", "--porcelain"])

    def diff(self, *paths: str) -> ToolResult:
        return self.run(["diff", *paths])

    def log(self, limit: int = 10) -> ToolResult:
        return self.run(["log", f"-{limit}", "--oneline"])

    def create_branch(self, name: str) -> ToolResult:
        return self.run(["checkout", "-b", name])

    def checkout(self, name: str) -> ToolResult:
        return self.run(["checkout", name])

    def commit(self, message: str, paths: Sequence[str] = (".",)) -> ToolResult:
        added = self.run(["add", "-A", *paths])
        if not added.success:
            return added
        return self.run(["commit", "--allow-empty", "-m", message])

    def changed_paths(self) -> list[str]:
        """Paths with uncommitted changes (modified, added, deleted or untracked)."""
        result = self.run(["status", "--porcelain", "--untracked-files=all"])
        paths = []
        for line in result.stdout.splitlines():
            entry = line[3:].strip()
            if " -> " in entry:  # rename: "old -> new"
                entry = entry.split(" -> ", 1)[1]
            if entry:
                paths.append(entry.strip('"'))
        return sorted(paths)

    def head(self) -> str | None:
        result = self.run(["rev-parse", "HEAD"])
        return result.stdout.strip() if result.success else None

    def tracked_files(self) -> list[str]:
        result = self.run(["ls-files"])
        return [line for line in result.stdout.splitlines() if line.strip()]

    def tag(self, name: str, message: str) -> ToolResult:
        return self.run(["tag", "-a", name, "-m", message])

    def branch_exists(self, name: str) -> bool:
        return bool(self.run(["branch", "--list", name]).stdout.strip())

    def state(self) -> GitState:
        inside = self._shell.run(["git", "rev-parse", "--is-inside-work-tree"])
        if not inside.success or inside.stdout.strip() != "true":
            return GitState(is_repository=False)
        branch = self._shell.run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        commit = self._shell.run(["git", "rev-parse", "HEAD"])
        status = self.status()
        changed = [line for line in status.stdout.splitlines() if line.strip()]
        return GitState(
            is_repository=True,
            branch=branch.stdout.strip() if branch.success else None,
            commit=commit.stdout.strip() if commit.success else None,
            clean=not changed if status.success else None,
            changed_files=len(changed),
        )
