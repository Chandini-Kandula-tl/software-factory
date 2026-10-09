"""Allowlisted command execution.

Commands run without a shell (argv lists only), inside a permitted root, with
a timeout, a scrubbed environment (no secret-looking variables), and redacted,
size-capped output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from software_factory.observability.redaction import SECRET_ENV_HINTS, redact
from software_factory.tools.base import PathOutsideWorkspaceError, ToolPermissionError, ToolResult

MAX_OUTPUT_CHARS = 20_000
# Never allowed, even if someone adds them to the allowlist by mistake.
FORBIDDEN_EXECUTABLES = frozenset({"sh", "bash", "zsh", "sudo", "su", "rm", "curl", "wget"})


class ShellTool:
    def __init__(
        self,
        root: Path,
        allowed_commands: Sequence[str],
        timeout_seconds: float = 300.0,
        enabled: bool = True,
        tool_name: str = "shell",
    ) -> None:
        self.root = root.resolve()
        self.allowed_commands = frozenset(allowed_commands) - FORBIDDEN_EXECUTABLES
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.tool_name = tool_name

    def validate(self, argv: Sequence[str], cwd: Path | None = None) -> Path:
        if not self.enabled:
            raise ToolPermissionError("Shell execution is disabled (security.shell_enabled)")
        if not argv:
            raise ToolPermissionError("Empty command")
        executable = Path(argv[0]).name
        if executable not in self.allowed_commands:
            raise ToolPermissionError(
                f"Command '{executable}' is not in the allowlist",
                hint="Add it to security.allowed_commands if it is safe.",
            )
        workdir = (cwd or self.root).resolve()
        if workdir != self.root and self.root not in workdir.parents:
            raise PathOutsideWorkspaceError(f"Working directory escapes the workspace: {workdir}")
        return workdir

    def run(
        self,
        argv: Sequence[str],
        cwd: Path | None = None,
        timeout_seconds: float | None = None,
        extra_env: Mapping[str, str | None] | None = None,
    ) -> ToolResult:
        """Run ``argv``. ``extra_env`` values override the environment; ``None`` removes a key."""
        workdir = self.validate(argv, cwd)
        timeout = timeout_seconds or self.timeout_seconds
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(argv),
                cwd=workdir,
                env=_merge_env(_scrubbed_env(), extra_env),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(
                tool=self.tool_name,
                command=list(argv),
                cwd=str(workdir),
                exit_code=None,
                stdout=_clean(exc.stdout),
                stderr=_clean(exc.stderr) + f"\n[timed out after {timeout}s]",
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )
        except FileNotFoundError:
            return ToolResult(
                tool=self.tool_name,
                command=list(argv),
                cwd=str(workdir),
                exit_code=127,
                stderr=f"Executable not found: {argv[0]}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        return ToolResult(
            tool=self.tool_name,
            command=list(argv),
            cwd=str(workdir),
            exit_code=completed.returncode,
            stdout=_clean(completed.stdout),
            stderr=_clean(completed.stderr),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def factory_environment_bin() -> str | None:
    """The factory's own virtual-environment ``bin`` directory, if it runs in one."""
    if sys.prefix != sys.base_prefix:
        return str(Path(sys.prefix) / "bin")
    return None


def project_environment(isolate: bool) -> dict[str, str | None]:
    """Environment overrides so generated projects see the user's machine, not the factory.

    The factory runs inside its own virtual environment (with pytest, LangGraph,
    ...). A generated project must not silently depend on that: its commands get
    a PATH without the factory's environment and no ``VIRTUAL_ENV``.
    """
    if not isolate:
        return {}
    own_bin = factory_environment_bin()
    entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and entry != own_bin
    ]
    return {"PATH": os.pathsep.join(entries), "VIRTUAL_ENV": None, "PYTHONHOME": None}


def find_program(program: str, isolate: bool) -> str | None:
    """Where ``program`` resolves for a generated project's commands."""
    path = project_environment(isolate).get("PATH") if isolate else None
    return shutil.which(program, path=path)


def _scrubbed_env() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not any(hint in name.upper() for hint in SECRET_ENV_HINTS)
    }


def _merge_env(base: dict[str, str], overrides: Mapping[str, str | None] | None) -> dict[str, str]:
    env = dict(base)
    for key, value in (overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _clean(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    output = redact(output)
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n[output truncated]"
    return output
