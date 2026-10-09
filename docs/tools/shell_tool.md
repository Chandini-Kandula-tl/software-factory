# Shell Tool

**Code:** [`src/software_factory/tools/shell.py`](../../src/software_factory/tools/shell.py) · **Tests:** `tests/unit/test_tools.py` · **Spec:** §18, §68.12

## Purpose

Run an **allowlisted executable** as an argv list inside a permitted root, with a
timeout, a scrubbed environment and redacted, size-capped output. It is also the engine
under the [git tool](git_tool.md) and the [build](build_tool.md) / [test](test_tool.md)
tools.

## Current usage

- Tool name `shell`. `ToolKit.shell()` (also used by `ToolKit.tools_for(["shell"])` and `ToolKit.require("shell", …)`) builds `ShellTool(workspace_root, security.allowed_commands, timeout_seconds=security.command_timeout_seconds, enabled=security.shell_enabled, tool_name="project-command")`.
- **No agent is granted it.** The build-phase workflow runs the approved plan's project commands (setup, format, lint, build, test, package, audit) in `_run_commands` ([`development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py)) through the step's `ProjectCommandTool` (`tool_for_step(step, ToolKit(source, security).shell())`), which delegates to this shell tool. See the [build tool](build_tool.md) and [test tool](test_tool.md).
- `prepare_development` uses `ToolKit.shell()` to run `<program> --version` (timeout 20 s) for `git` and every plan program, recorded as `development.tool_versions`.

## API

```python
ShellTool(root, allowed_commands, timeout_seconds=300.0, enabled=True, tool_name="shell")
validate(argv, cwd=None) -> Path      # policy checks only; returns the working dir
run(argv, cwd=None, timeout_seconds=None, extra_env=None) -> ToolResult
```

`extra_env: Mapping[str, str | None] | None` is merged **on top of** the scrubbed
environment, so its values win; a value of `None` **removes** that variable. It is not
scrubbed. Callers:

- the build phase passes `project_environment(security.isolate_project_environment)` plus `COMMAND_ENV` = `CI=true`, `NO_COLOR=1`, `FORCE_COLOR=0`, `PYTHONDONTWRITEBYTECODE=1`. `COMMAND_ENV` keeps tools non-interactive and out of watch mode, removes colour codes from logs, and prevents a stale `.pyc` from masking a fix written within the same second. `project_environment` isolates the project from the factory's own Python environment (next section);
- the [git tool](git_tool.md) passes its fallback commit identity.

## Isolation from the factory's environment

The factory itself runs in a Python virtual environment that contains `python`,
`pytest`, LangGraph and more. A generated project must not silently depend on it.
Two module-level helpers in `tools/shell.py`:

```python
factory_environment_bin() -> str | None          # <sys.prefix>/bin when running in a venv
project_environment(isolate: bool) -> dict[str, str | None]
find_program(program: str, isolate: bool) -> str | None
```

- `project_environment(True)` returns `{"PATH": <PATH without the factory venv's bin>,
  "VIRTUAL_ENV": None, "PYTHONHOME": None}` — the two `None`s unset those variables when
  passed as `extra_env`. `project_environment(False)` returns `{}`.
- `find_program(program, isolate)` is `shutil.which(program, path=<that PATH>)`.

`security.isolate_project_environment` (default `true`) selects the mode. The same lookup
is used everywhere the factory asks "is this program available to the project?": the
project commands' environment (`_run_commands`), the plan's command check
(`command_problems`: "X (not installed)"), and the environment facts given to the
Technology Selection and Implementation Planning agents (`WorkflowService._environment`:
`installed_commands` / `not_installed`). This was found live: inside the factory's venv
`python` and `pytest` looked installed although the machine had no such tools for the
project.

## Policy (checked in `validate`, raises `ToolPermissionError`)

| Rule | Detail |
|---|---|
| Disabled | `enabled=False` (from `security.shell_enabled`) → error "Shell execution is disabled". The same flag disables the [git tool](git_tool.md) when built by `ToolKit`. |
| Empty command | Error. |
| Allowlist | `Path(argv[0]).name` must be in `allowed_commands` (default, in both `SecurityConfig` and `config/factory.yaml`: `git, python, python3, pip, pytest, ruff, uv, node, npm, npx`; the default `security.command_timeout_seconds` is 600). A full path like `/usr/bin/python3` is matched by its basename. |
| Always forbidden | `sh`, `bash`, `zsh`, `sudo`, `su`, `rm`, `curl`, `wget` are removed from the allowlist even if configured. |
| Working directory | `cwd` (default: root) must resolve to the root or inside it, else `PathOutsideWorkspaceError`. |

## Execution

- `subprocess.run(list(argv), ...)` — **no shell**, so no globbing, pipes, `&&` or variable expansion.
- **Environment:** the current environment minus any variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD` or `CREDENTIAL` (case-insensitive), plus `extra_env` (`None` values remove variables). This is the shared `SECRET_ENV_HINTS` list in `observability/redaction.py`; output redaction uses the same list to find secret values to mask.
- **Timeout:** `timeout_seconds` argument or the tool default. On expiry the call does **not** raise: it returns a `ToolResult` with `timed_out=True`, `exit_code=None`, partial output, and `[timed out after Ns]` appended to stderr.
- **Missing executable:** returns `exit_code=127` with `Executable not found: ...`.
- **Output:** stdout and stderr are decoded, passed through secret redaction (`observability/redaction.py`), and truncated to **20,000 characters** each with `[output truncated]` appended.

## Result = ground truth

`ToolResult` ([`tools/base.py`](../../src/software_factory/tools/base.py)):
`tool`, `command`, `cwd`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `timed_out`.

```python
success = exit_code == 0 and not timed_out
```

Success is derived from the real process, never from anything an agent says (spec §68.12).

## Example

```python
shell = ShellTool(workspace, ["python3", "pytest"])
shell.run(["python3", "-c", "print('hello')"]).success   # True
shell.run(["bash", "-c", "echo hi"])                       # ToolPermissionError
shell.run(["ls"])                                          # ToolPermissionError: not in the allowlist
```

## Known limitations

- **Network:** `security.network_enabled` (default `false`) is a configuration flag only; it is **not enforced at OS level**. An allowlisted command (e.g. `npm`, `python3`) can still reach the network.
- Allowlisting is per executable, not per argument: `python3 -c "<anything>"` is allowed once `python3` is. Interpreters on the allowlist effectively grant arbitrary code execution inside the process's OS permissions.
- Confinement covers `cwd` only; the child process can still access absolute paths outside the root.
- Env scrubbing is name-based; secrets in variables with other names are passed through (output is still redacted).
- `timeout_seconds=0` is treated as "use the default" (falsy check).
- Isolation removes only the factory venv's `bin` directory from `PATH` and unsets `VIRTUAL_ENV` / `PYTHONHOME`; other Python installations on `PATH` (e.g. a global `python3`) remain visible, by design.

See also: [security architecture](../architecture/security_architecture.md).
