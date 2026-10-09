# Test Tool (project test command)

**Code:** `make_test` / `_missing_planned_tests` / `_run_commands` in [`orchestration/nodes/development_nodes.py`](../../src/software_factory/orchestration/nodes/development_nodes.py) · class `TestTool` in [`tools/project_commands.py`](../../src/software_factory/tools/project_commands.py) · **Tests:** `tests/workflow/test_build_workflow.py` · **Spec:** §15, §18, §40, §68.12

## Purpose

Run the **project's own** test command and return the real result. This is the only
acceptable evidence that tests passed (spec §15).

## How it runs

The `test` node (lifecycle `TESTING`, after a successful build) runs the plan's
`commands.test` (mandatory, at least one command) with `TestTool` (chosen by
`tool_for_step("test", …)`, which runs through [`ShellTool`](shell_tool.md)) via
`development_nodes._run_commands`. History records carry `details.tool = "test"`. The
mechanics are the same as for the [build tool](build_tool.md):

- allowlist and cwd confinement;
- `security.command_timeout_seconds`;
- `COMMAND_ENV` (`CI=true`, `NO_COLOR=1`, `FORCE_COLOR=0`, `PYTHONDONTWRITEBYTECODE=1`);
- the full log saved to `artifacts/tool-runs/NNN-test.log`;
- a `CommandRun` plus a `TOOL` history record per command;
- a stop at the first failing command.

The resulting `VerificationResult` (phase `test`) is written to `test-results.json` /
`test-results.md` (latest version only).

## Planned test files must exist

A test command can exit 0 while running zero tests, so before running it the node checks
the current task's planned tests (`_missing_planned_tests`). Only entries of `task.tests`
that look like file paths (contain `/` or have a file extension, and no spaces) are
checked; plain test names cannot be checked deterministically. The check applies only to
task work (`development.phase == "task"`), not to fixes.

If any planned file is missing, the test command is **not** run. The node records a failed
verification with one `CommandRun`:

- `argv = ["(factory check) planned test files exist"]`, `cwd = "."`, `exit_code = 1`;
- `output_tail = "The task's planned tests were not written. Missing test files: <files>. Create them with real assertions for the task's behaviour."`

This goes through the normal fix loop (`BUG_FIXING`) like any other test failure. Covered by
`test_missing_planned_test_files_cannot_pass`.

## Ground truth and what happens next

```python
success = exit_code == 0 and not timed_out   # a timed-out run is a failure
```

**Pass:**

1. The workflow commits all changes with the [git tool](git_tool.md) (`add -A`, then `commit --allow-empty`):
   - task work: `T-n: <title>`;
   - fixes: `fix(<phase>): <summary>`.
2. It resets `debug_iterations` to 0.
3. It moves on to the next task (`DEVELOPMENT`), to `CODE_REVIEW` when the queue is empty, or back to the stage that requested the fix (`PHASE_RETURN`).

**Fail:**

- Goes to `BUG_FIXING`, where the [Testing Agent](../agents/testing_agent.md) analyzes the failure and the [Coding Agent](../agents/coding_agent.md) fixes it, while `debug_iterations < limits.max_debug_iterations`.
- When the budget is used up, the project goes to `FAILED`.

Code is therefore **committed only when the tests pass**. Final validation runs the test
command again as part of its full check.

## `TestTool` class

```python
TestTool(shell: ShellTool)
run(argv, cwd=None, extra_env=None) -> ToolResult      # result.tool == "test"
```

`TestTool` is the `ProjectCommandTool` for the `test` step (`__test__ = False` so pytest
does not collect it). See the [build tool](build_tool.md#how-the-build-phase-runs-commands)
for the shared class and the step mapping.

## Example

The build-phase tests use `{"argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]}`.
A wrong `add` makes it exit 1: the run is recorded as `failed`, and the fix is analyzed
and applied. The next run exits 0, is recorded as `success`, and `T-1: Add function`
is committed.

## Known limitations

- Test reports and coverage are not parsed. Only the exit code counts.
- The plan's per-task `tests` are not run individually; every test run executes the whole `test` command. Only their existence (for path-like entries) is checked.
- Tests run with the host's network access (`security.network_enabled` is not enforced at OS level).
