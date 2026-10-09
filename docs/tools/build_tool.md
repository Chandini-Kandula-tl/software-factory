# Build Tool (project commands)

**Code:** `_run_commands` / `_verify` / `make_build` in [`orchestration/nodes/development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py) · `BuildTool`, `LintTool`, `FormatterTool`, `PackageManagerTool`, `DependencyAuditTool`, `STEP_TOOLS`, `tool_for_step` in [`tools/project_commands.py`](../../src/software_factory/tools/project_commands.py) · **Tests:** `tests/workflow/test_build_workflow.py`, `tests/unit/test_tools.py` · **Spec:** §18, §40, §41, §68.12

## Purpose

Run the **project's own** setup, format, lint and build commands and report the real
result. No stack is assumed. The commands come from the approved plan's `commands`
section ([Implementation Planning Agent](../agents/implementation_planning_agent.md)),
never from the factory.

## How the build phase runs commands

The workflow runs the plan's commands in `development_nodes._run_commands`. For each step
it picks the step's tool with `tool_for_step(step, ToolKit(source, security).shell())`:

| Plan step | Tool class | `ToolName` (`details.tool` in history) |
|---|---|---|
| `setup` | `PackageManagerTool` | `package_manager` |
| `format` | `FormatterTool` | `formatter` |
| `lint` | `LintTool` | `lint` |
| `build` | `BuildTool` | `build` |
| `test` | `TestTool` | `test` |
| `package` | `BuildTool` | `build` |
| `audit` | `DependencyAuditTool` | `dependency_audit` |

The mapping is `STEP_TOOLS`; an unknown step raises `ToolPermissionError`
("No tool runs project step '<step>'"). Every class is a `ProjectCommandTool`:

```python
ProjectCommandTool(shell: ShellTool)
run(argv: Sequence[str], cwd: Path | None = None,
    extra_env: Mapping[str, str] | None = None) -> ToolResult   # result.tool == tool_name
```

`run` refuses an empty `argv` and otherwise delegates to [`ShellTool`](shell_tool.md), then
stamps the result with its own tool name. `ToolKit.shell()` builds that shell tool with:

- root = the project source directory;
- `security.allowed_commands` as the allowlist;
- `security.command_timeout_seconds` as the timeout (default 600);
- `security.shell_enabled`;
- `tool_name="project-command"`.

For every `ProjectCommand` (`argv`, `cwd`) in a step:

1. Run `argv` with `cwd = source / command.cwd` and `extra_env = COMMAND_ENV` (`CI=true`, `NO_COLOR=1`, `FORCE_COLOR=0`, `PYTHONDONTWRITEBYTECODE=1`). A `ToolPermissionError` (e.g. a cwd outside the repository) becomes a failed run (exit code `None`, the error as output) instead of crashing the node.
2. Save the full (redacted) stdout + stderr, with the command, cwd and exit code, to `artifacts/tool-runs/NNN-<step>.log` (numbered, never overwritten).
3. Create a `CommandRun` (`step`, `argv`, `cwd`, `exit_code`, `timed_out`, `duration_ms`, the last 4,000 characters of output as `output_tail`, and `log_file`).
4. Append a `TOOL` execution record (`stage = <step>`, status success/failed, summary `"<argv> → exit N"`, `details = {tool, cwd, log_file}`).
5. Stop at the first failing command.

`_verify` runs a list of steps in order, stops at the first failed step, and returns a
`VerificationResult` (`phase`, `passed`, `runs`, `failed_step`, `timestamp`).

## Build step order (`build` node, lifecycle `BUILDING`)

1. **setup**: only if the plan has setup commands **and** the dependency manifests changed since the last successful setup (`manifest_fingerprint(source)` differs from `development.setup_fingerprint`; see the [source workspace tool](source_workspace_tool.md)). A successful setup stores the new fingerprint.
2. **format**
3. **lint**
4. **build**

Empty steps are skipped. If every step passes, the lifecycle moves to `TESTING` (see the
[test tool](test_tool.md)). If a step fails, the `VerificationResult` is written to
`test-results.json` / `test-results.md`, and the workflow goes to `BUG_FIXING` while the
fix budget lasts (`limits.max_debug_iterations`); otherwise it goes to `FAILED`.

Final validation (`quality_nodes.final_validation`) runs setup (if manifests changed) →
lint → build → test → package. Security validation (`quality_nodes.security_validation`)
runs `audit` with `DependencyAuditTool`.

## Ground truth

```python
success = exit_code == 0 and not timed_out
```

A step passes only when this is true. An agent's claim is never evidence (spec §40, §68.12).

## Before anything runs

`development_nodes.command_problems(commands, security)` lists why the plan's commands
could not run on this machine:

- `security.shell_enabled` is false ("any program (security.shell_enabled is false)");
- a command's `cwd` is absolute or contains `..` ("… cwd '<cwd>' is outside the repo");
- a program is in `FORBIDDEN_EXECUTABLES` ("never allowed for safety");
- a program is not in `security.allowed_commands`;
- a program is not found on `PATH` ("not installed").

The check runs twice. `create_implementation_plan` stores the result in
`state["plan_checks"]`, and `implementation-plan.md` shows it in its "Environment check"
section before the human approves. `prepare_development` runs it again and fails the stage
with "The approved project commands need programs that are not available: ..." before any
code is written.

`prepare_development` also records the version of `git` and of every program in the plan
(`<program> --version`, first line, `"unknown"` if it fails) in
`development.tool_versions` and in its `WORKFLOW` history record, for reproducibility.

## Example

A plan step `{"argv": ["npm", "run", "build"], "cwd": "web"}` runs `npm run build` in
`<source>/web` with `CI=true` and writes a log such as `artifacts/tool-runs/004-build.log`:

```text
$ npm run build   (cwd: web)
exit code: 0

...output...
```

## Known limitations

- Build output is not parsed. Errors reach the Testing Agent as raw, redacted text (the last 4,000 characters).
- A `cwd` that escapes the source directory, or a disabled shell, is shown on the plan and caught again by `prepare_development` (the project goes to `FAILED` before any code is written). As a safety net, a command refused by policy at run time is recorded as a failed `CommandRun` rather than crashing the node.
- Setup (package downloads) is not network-restricted: `security.network_enabled` is not enforced at OS level.
