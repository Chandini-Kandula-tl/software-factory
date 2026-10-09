"""Build-phase workflow nodes: preparing the repository, coding each task,
building, testing and fixing. Review, security, final validation and release
are in ``quality_nodes``.

    prepare_development → implement_task → build → test
        test ─┬─ pass → commit → next task, or code_review when all tasks are done
              └─ fail → debug (Testing Agent explains, Coding Agent fixes) → build
    code_review → security_validation → final_validation → release_approval (human) → release

Rules enforced here, deterministically:
- only real tool results decide pass/fail (exit code 0 and no timeout);
- every loop is bounded (limits.max_debug_iterations, limits.max_review_rounds);
- the Coding Agent's change requests stop the workflow for a human;
- code is committed only after the project's own tests pass.

``state["development"]`` holds the build-phase bookkeeping:
source_path, base_commit, last_commit, task_queue, completed_tasks, phase
(what a fix returns to), fix_context (what to fix), debug_iterations,
review_rounds, security_rounds, setup_fingerprint, tool_versions,
last_changes, change_request_feedback.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from software_factory.config.settings import SecurityConfig
from software_factory.domain.approvals import ApprovalStage
from software_factory.domain.development import (
    CodeChanges,
    CommandRun,
    FailureCategory,
    FileAction,
    ProjectCommand,
    TestFailureAnalysis,
    VerificationResult,
)
from software_factory.domain.errors import FactoryError, QualityGateError
from software_factory.domain.execution import ExecutionKind, ExecutionStatus
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.lifecycle import transition
from software_factory.domain.planning import ImplementationPlan, ImplementationTask, ProjectCommands
from software_factory.domain.project import ProjectMode
from software_factory.domain.quality_gates import require_entry
from software_factory.observability.clock import utc_now
from software_factory.orchestration.nodes.common import (
    Timer,
    execution_record,
    fail_stage,
    invoke_agent,
)
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.orchestration.state import FactoryState
from software_factory.tools.base import ToolName, ToolPermissionError, ToolResult
from software_factory.tools.filesystem import FilesystemTool
from software_factory.tools.git import GitTool
from software_factory.tools.project_commands import tool_for_step
from software_factory.tools.shell import (
    FORBIDDEN_EXECUTABLES,
    find_program,
    project_environment,
)
from software_factory.tools.source_workspace import (
    DEFAULT_GITIGNORE,
    DEPENDENCY_MANIFESTS,
    list_source_files,
    manifest_fingerprint,
    mentioned_files,
    read_files,
)
from software_factory.tools.toolkit import ToolKit

NodeFn = Callable[[FactoryState], dict[str, Any]]
OUTPUT_TAIL_CHARS = 4000
# Environment for project commands: never interactive, never watch mode, and no
# Python bytecode cache (a fix written within the same second as the broken
# file could otherwise be masked by a stale .pyc).
COMMAND_ENV = {
    "CI": "true",
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}

# Where a successful fix returns to (development["phase"]).
PHASE_RETURN = {
    "task": None,  # handled by task bookkeeping
    "review": S.CODE_REVIEW,
    "security": S.SECURITY_VALIDATION,
    "validation": S.FINAL_VALIDATION,
    "release_feedback": S.CODE_REVIEW,
}


# -- helpers ----------------------------------------------------------------------


def _dev(state: FactoryState) -> dict[str, Any]:
    return dict(state.get("development") or {})


def _plan(state: FactoryState) -> ImplementationPlan:
    return ImplementationPlan.model_validate(state["implementation_plan"])


def _task(plan: ImplementationPlan, task_id: str) -> ImplementationTask:
    return next(t for t in plan.tasks if t.id == task_id)


def _source(dev: dict[str, Any]) -> Path:
    return Path(dev["source_path"])


def _toolkit(deps: WorkflowDependencies, source: Path) -> ToolKit:
    return ToolKit(source, deps.security)


def _git(deps: WorkflowDependencies, source: Path) -> GitTool:
    return GitTool(source, enabled=deps.security.shell_enabled)


def _to(state: FactoryState, target: S) -> str:
    return transition(state["lifecycle_status"], target).value


def _ordered_task_ids(plan: ImplementationPlan, done: set[str]) -> list[str]:
    """Plan order, but never before a dependency (stable topological sort)."""
    pending = [t for t in plan.tasks if t.id not in done]
    known = {t.id for t in plan.tasks}
    ordered: list[str] = []
    placed = set(done)
    while pending:
        ready = [t for t in pending if all(d in placed or d not in known for d in t.depends_on)]
        if not ready:  # cycles are rejected at planning time; keep plan order if any slip in
            ready = pending[:1]
        for task in ready:
            ordered.append(task.id)
            placed.add(task.id)
        pending = [t for t in pending if t.id not in placed]
    return ordered


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, ensure_ascii=False)


def _commands_summary(plan: ImplementationPlan) -> dict[str, Any]:
    return {step: commands for step, commands in plan.commands.model_dump().items() if commands}


def _run_commands(
    deps: WorkflowDependencies,
    state: FactoryState,
    source: Path,
    step: str,
    commands: Iterable[ProjectCommand],
) -> list[CommandRun]:
    """Run approved project commands for one step; stop at the first failure."""
    tool = tool_for_step(step, _toolkit(deps, source).shell())
    runs: list[CommandRun] = []
    for command in commands:
        timer = Timer()
        workdir = source / command.cwd
        try:
            result = tool.run(
                command.argv,
                cwd=workdir,
                extra_env={
                    **project_environment(deps.security.isolate_project_environment),
                    **COMMAND_ENV,
                },
            )
        except ToolPermissionError as error:
            # Refused by policy (e.g. cwd outside the repo): a failed run, not a crash.
            result = ToolResult(
                tool="project-command", command=command.argv, exit_code=None, stderr=str(error)
            )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        log_file = deps.recorder.write_tool_output(
            f"{step}.log",
            f"$ {' '.join(command.argv)}   (cwd: {command.cwd})\n"
            f"exit code: {result.exit_code}{' (timed out)' if result.timed_out else ''}\n\n"
            + output,
        )
        run = CommandRun(
            step=step,
            argv=command.argv,
            cwd=command.cwd,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_ms=result.duration_ms,
            output_tail=output[-OUTPUT_TAIL_CHARS:],
            log_file=log_file,
        )
        runs.append(run)
        deps.recorder.record_execution(
            execution_record(
                deps,
                state,
                kind=ExecutionKind.TOOL,
                stage=step,
                status=ExecutionStatus.SUCCESS if run.success else ExecutionStatus.FAILED,
                timer=timer,
                summary=f"{' '.join(command.argv)} → exit {result.exit_code}"
                + (" (timed out)" if result.timed_out else ""),
                details={"tool": result.tool, "cwd": command.cwd, "log_file": log_file},
            )
        )
        if not run.success:
            break
    return runs


def _verify(
    deps: WorkflowDependencies,
    state: FactoryState,
    source: Path,
    phase: str,
    steps: list[tuple[str, list[ProjectCommand]]],
) -> VerificationResult:
    runs: list[CommandRun] = []
    for step, commands in steps:
        step_runs = _run_commands(deps, state, source, step, commands)
        runs += step_runs
        if step_runs and not step_runs[-1].success:
            return VerificationResult(
                phase=phase, passed=False, runs=runs, failed_step=step, timestamp=utc_now()
            )
    return VerificationResult(phase=phase, passed=True, runs=runs, timestamp=utc_now())


def _apply_changes(
    deps: WorkflowDependencies, state: FactoryState, source: Path, changes: CodeChanges
) -> list[str]:
    # Written on the Coding Agent's behalf, so only with a tool it is allowed to use.
    fs: FilesystemTool = _toolkit(deps, source).require(
        ToolName.FILESYSTEM, deps.agents.coding.spec.allowed_tools
    )
    written: list[str] = []
    for change in changes.all_files:
        if change.action is FileAction.DELETE:
            if fs.delete(change.path):
                written.append(change.path)
        else:
            fs.write_text(change.path, change.content)
            written.append(change.path)
    for edit in changes.edits:
        if not fs.exists(edit.path):
            raise ChangeApplyError(f"Edit for {edit.path}: the file does not exist")
        text = fs.read_text(edit.path)
        count = text.count(edit.find)
        if count != 1:
            raise ChangeApplyError(
                f"Edit for {edit.path}: the `find` text occurs {count} times (must be exactly once)"
            )
        fs.write_text(edit.path, text.replace(edit.find, edit.replace, 1))
        written.append(edit.path)
    deps.recorder.record_execution(
        execution_record(
            deps,
            state,
            kind=ExecutionKind.TOOL,
            stage="apply_changes",
            status=ExecutionStatus.SUCCESS,
            summary=f"Applied {len(written)} file change(s): {', '.join(written[:12])}"
            + (" …" if len(written) > 12 else ""),
            details={"files": written},
        )
    )
    return written


class ChangeApplyError(FactoryError):
    """A proposed change could not be applied (e.g. an edit's text was not found)."""


def _snapshot(source: Path, paths: Iterable[str]) -> dict[str, str | None]:
    """Current content of the given files (None = does not exist), to undo a candidate."""
    fs = FilesystemTool(source)
    saved: dict[str, str | None] = {}
    for path in paths:
        try:
            saved[path] = fs.read_text(path) if fs.exists(path) else None
        except (ToolPermissionError, OSError, UnicodeDecodeError):
            saved[path] = None
    return saved


def _restore(source: Path, saved: dict[str, str | None]) -> None:
    fs = FilesystemTool(source)
    for path, content in saved.items():
        try:
            if content is None:
                fs.delete(path)
            else:
                fs.write_text(path, content)
        except (ToolPermissionError, OSError):
            continue


def _check_steps(
    plan: ImplementationPlan, dev: dict[str, Any], source: Path
) -> list[tuple[str, list[ProjectCommand]]]:
    """Build and test steps used to try a fix candidate."""
    steps: list[tuple[str, list[ProjectCommand]]] = []
    if plan.commands.setup and manifest_fingerprint(source) != dev.get("setup_fingerprint"):
        steps.append(("setup", plan.commands.setup))
    return steps + [
        ("format", plan.commands.format),
        ("lint", plan.commands.lint),
        ("build", plan.commands.build),
        ("test", plan.commands.test),
    ]


def _attempts_text(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return ""
    lines = ["", "", "EARLIER FIX ATTEMPTS FOR THIS FAILURE (all failed — do something different):"]
    for number, attempt in enumerate(attempts[-4:], start=1):
        changed = ", ".join(attempt.get("files", []))
        lines.append(
            f"{number}. {attempt.get('summary', '')} (changed: {changed})"
            f"\n   Result: {attempt.get('result', 'tests still failed')}"
        )
    return "\n".join(lines)


def _code_context(
    deps: WorkflowDependencies,
    source: Path,
    preferred: Iterable[str],
) -> tuple[list[str], dict[str, str]]:
    """Most relevant files first, then manifests, then the rest while the budget lasts.

    Small projects therefore get their whole code base; large ones get the
    files the task or failure points at.
    """
    tree = list_source_files(source)
    manifests = [p for p in tree if Path(p).name in DEPENDENCY_MANIFESTS]
    wanted = [p for p in preferred if p in tree] + manifests + tree
    files = read_files(source, wanted, budget_chars=deps.limits.max_context_chars)
    return tree, files


def _change_request_update(
    deps: WorkflowDependencies,
    state: FactoryState,
    dev: dict[str, Any],
    changes: CodeChanges,
    task_id: str | None,
) -> dict[str, Any]:
    assert changes.change_request is not None
    git = _git(deps, _source(dev))
    base = dev.get("base_commit")
    changed = git.run(["diff", "--name-only", f"{base}..HEAD"]).stdout.split() if base else []
    request = {
        **changes.change_request.model_dump(mode="json"),
        "task_id": task_id,
        "origin_status": state["lifecycle_status"],
        "raised_at": utc_now(),
        "summary": changes.summary,
        "completed_tasks": [t["task_id"] for t in dev.get("completed_tasks", [])],
        "changed_files": changed,
    }
    # Spec §36: change request → impact analysis → human decision.
    result, failure = invoke_agent(
        deps,
        state,
        deps.agents.impact_analysis,
        stage="change_request",
        available={
            "mode": "change_request",
            "requirements": state.get("requirements"),
            "architecture": state.get("architecture"),
            "implementation_plan": state.get("implementation_plan"),
            "change_request": changes.change_request.model_dump(mode="json"),
            "completed_tasks": request["completed_tasks"],
            "changed_files": changed,
        },
    )
    extra: dict[str, Any] = {}
    if result is not None:
        request["impact_analysis"] = result.output.model_dump(mode="json")
        deps.recorder.write_stage_artifact("impact_analysis", result.output, state)
        if result.warnings:
            extra["warnings"] = [
                *state.get("warnings", []),
                *(f"impact-analysis-agent: {w}" for w in result.warnings),
            ]
    else:  # the human still decides; the failure is in history and errors
        request["impact_analysis_error"] = "Impact analysis failed; see errors.md"
    deps.recorder.write_stage_artifact(
        "change_request", changes.change_request, {**state, "change_request": request}
    )
    if result is None and "errors" in failure:
        extra["errors"] = failure["errors"]
    return {
        **extra,
        "change_request": request,
        "development": dev,
        "lifecycle_status": _to(state, S.WAITING_FOR_CHANGE_REQUEST_APPROVAL),
    }


def _fix_or_fail(
    deps: WorkflowDependencies,
    state: FactoryState,
    dev: dict[str, Any],
    verification: VerificationResult,
    stage: str,
) -> dict[str, Any]:
    """A failed verification goes to BUG_FIXING while the fix budget lasts."""
    base = {"last_verification": verification.model_dump(mode="json")}
    if dev.get("debug_iterations", 0) < deps.limits.max_debug_iterations:
        dev["fix_context"] = {
            "source": "verification",
            "verification": verification.model_dump(mode="json"),
        }
        return {**base, "development": dev, "lifecycle_status": _to(state, S.BUG_FIXING)}
    failed = verification.runs[-1] if verification.runs else None
    error = FactoryError(
        f"{verification.failed_step} still failing after {dev.get('debug_iterations', 0)} "
        f"fix attempt(s)"
        + (f": {' '.join(failed.argv)} exited {failed.exit_code}" if failed else ""),
        hint="See test-results.md and the log in artifacts/tool-runs/. Fix the code by hand "
        "or adjust the plan, then `factory retry` (the fix budget restarts).",
    )
    return {**base, "development": dev, **fail_stage(deps, state, stage=stage, error=error)}


# -- nodes ------------------------------------------------------------------------


def make_prepare_development(deps: WorkflowDependencies) -> NodeFn:
    def prepare_development(state: FactoryState) -> dict[str, Any]:
        timer = Timer()
        try:
            require_entry("development", state)
        except QualityGateError as error:
            return fail_stage(deps, state, stage="development", error=error, timer=timer)

        try:
            plan = _plan(state)
        except ValueError:
            # A plan approved before project commands existed: re-plan with feedback.
            approvals = dict(state.get("approvals") or {})
            approvals.pop(ApprovalStage.IMPLEMENTATION_PLAN.value, None)
            feedback = dict(state.get("revision_feedback") or {})
            feedback["implementation_plan"] = (
                "The plan must now include `commands` (setup, lint, build, test, ...) so the "
                "factory can build and test the project. Keep the tasks unless they must change."
            )
            return {
                "approvals": approvals,
                "revision_feedback": feedback,
                "completed_stages": [
                    s for s in state.get("completed_stages", []) if s != "implementation_plan"
                ],
                "lifecycle_status": _to(state, S.IMPLEMENTATION_PLANNING),
            }

        missing = command_problems(plan.commands, deps.security)
        if missing:
            return fail_stage(
                deps,
                state,
                stage="development",
                timer=timer,
                error=FactoryError(
                    "The approved project commands need programs that are not available: "
                    + ", ".join(missing),
                    hint="Install them (and add them to security.allowed_commands if needed), "
                    "then `factory retry`.",
                ),
            )

        dev = _dev(state)
        try:
            source, base_commit = _prepare_repository(deps, state, dev)
        except (FactoryError, OSError) as error:
            return fail_stage(deps, state, stage="development", error=error, timer=timer)

        done = {t["task_id"] for t in dev.get("completed_tasks", [])}
        queue = _ordered_task_ids(plan, done)
        tool_versions = _tool_versions(deps, source, plan)
        dev.update(
            {
                "source_path": str(source),
                "base_commit": dev.get("base_commit") or base_commit,
                "last_commit": base_commit,
                "task_queue": queue,
                "completed_tasks": dev.get("completed_tasks", []),
                "phase": "task",
                "fix_context": None,
                "debug_iterations": 0,
                "review_rounds": 0,
                "security_rounds": 0,
                "tool_versions": tool_versions,
            }
        )
        deps.recorder.record_execution(
            execution_record(
                deps,
                state,
                kind=ExecutionKind.WORKFLOW,
                stage="development",
                status=ExecutionStatus.INFO,
                timer=timer,
                summary=f"Development workspace ready at {source}; {len(queue)} task(s) queued",
                details={"task_queue": queue, "tool_versions": tool_versions},
            )
        )
        return {
            "development": dev,
            "change_request": None,
            "lifecycle_status": _to(state, S.DEVELOPMENT if queue else S.CODE_REVIEW),
        }

    return prepare_development


def command_problems(commands: ProjectCommands, security: SecurityConfig) -> list[str]:
    """Why the plan's commands could not run on this machine (empty list = fine).

    Used twice: shown on the plan before it is approved, and enforced before
    development starts.
    """
    if not security.shell_enabled:
        return ["any program (security.shell_enabled is false)"]
    problems: list[str] = []
    programs: set[str] = set()
    for step, step_commands in commands.model_dump().items():
        for command in step_commands:
            programs.add(command["argv"][0])
            cwd = Path(command["cwd"])
            if cwd.is_absolute() or ".." in cwd.parts:
                problems.append(
                    f"{' '.join(command['argv'])} ({step}: cwd '{command['cwd']}' is outside "
                    "the repo)"
                )
    allowed = set(security.allowed_commands)
    for program in sorted(programs):
        name = Path(program).name
        if name in FORBIDDEN_EXECUTABLES:
            problems.append(f"{name} (never allowed for safety)")
        elif name not in allowed:
            problems.append(f"{name} (not in security.allowed_commands)")
        elif find_program(program, security.isolate_project_environment) is None:
            problems.append(f"{name} (not installed)")
    return problems


def _tool_versions(deps: WorkflowDependencies, source: Path, plan: ImplementationPlan) -> dict:
    """Versions of the programs the plan uses, for reproducibility (spec §68.16)."""
    shell = _toolkit(deps, source).shell()
    programs = {"git"} | {
        c["argv"][0] for step in plan.commands.model_dump().values() for c in step
    }
    versions: dict[str, str] = {}
    for program in sorted(programs):
        try:
            result = shell.run([program, "--version"], timeout_seconds=20)
        except ToolPermissionError:
            continue
        text = (result.stdout or result.stderr).strip().splitlines()
        versions[Path(program).name] = text[0][:120] if result.success and text else "unknown"
    return versions


def _prepare_repository(
    deps: WorkflowDependencies, state: FactoryState, dev: dict[str, Any]
) -> tuple[Path, str | None]:
    if dev.get("source_path"):
        source = Path(dev["source_path"])
        git = _git(deps, source)
        if state.get("project_mode") == ProjectMode.EXISTING.value:
            branch = f"factory/{state['project_id']}"
            if git.state().branch != branch and not git.checkout(branch).success:
                raise FactoryError(
                    f"Could not return to branch {branch}",
                    hint="Commit or stash changes in the repository, then `factory retry`.",
                )
        return source, git.head()

    if state.get("project_mode") == ProjectMode.EXISTING.value and state.get("repository_path"):
        source = Path(str(state["repository_path"]))
        git = _git(deps, source)
        git_state = git.state()
        if not git_state.is_repository:
            raise FactoryError(f"{source} is not a git repository")
        if not git_state.clean:
            raise FactoryError(
                f"{source} has uncommitted changes",
                hint="Commit or stash them first; the factory works on its own branch.",
            )
        branch = f"factory/{state['project_id']}"
        result = git.checkout(branch) if git.branch_exists(branch) else git.create_branch(branch)
        if not result.success:
            raise FactoryError(f"Could not switch to branch {branch}: {result.stderr.strip()}")
        return source, git.head()

    source = deps.source_root
    source.mkdir(parents=True, exist_ok=True)
    git = _git(deps, source)
    # The generated app gets its OWN repository, even when the workspace itself
    # lies inside another git repository (e.g. the factory's own checkout).
    if not (source / ".git").exists() and not git.init().success:
        raise FactoryError(f"git init failed in {source}")
    toplevel = git.run(["rev-parse", "--show-toplevel"]).stdout.strip()
    if not toplevel or Path(toplevel).resolve() != source.resolve():
        raise FactoryError(
            f"{source} is not the root of its own git repository (git reports {toplevel!r})"
        )
    fs = FilesystemTool(source)
    if not fs.exists(".gitignore"):
        fs.write_text(".gitignore", DEFAULT_GITIGNORE)
    if git.head() is None:
        committed = git.commit("chore: initialize repository (Software Factory)")
        if not committed.success:
            raise FactoryError(f"Initial commit failed: {committed.stderr.strip()}")
    return source, git.head()


def make_implement_task(deps: WorkflowDependencies) -> NodeFn:
    def implement_task(state: FactoryState) -> dict[str, Any]:
        dev = _dev(state)
        plan = _plan(state)
        task = _task(plan, dev["task_queue"][0])
        source = _source(dev)
        tree, files = _code_context(deps, source, [*task.likely_files, *task.tests])
        feedback = dev.get("change_request_feedback")
        result, update = invoke_agent(
            deps,
            state,
            deps.agents.coding,
            stage="development",
            available={
                "mode": "implement",
                "task": task.model_dump(mode="json"),
                "requirements": state.get("requirements"),
                "architecture": state.get("architecture"),
                "technology_selection": state.get("technology_selection"),
                "commands": _commands_summary(plan),
                "file_tree": tree,
                "relevant_files": files,
                "change_request_feedback": feedback,
            },
        )
        if result is None:
            return update
        dev.pop("change_request_feedback", None)
        changes: CodeChanges = result.output
        if changes.change_request:
            return {**update, **_change_request_update(deps, state, dev, changes, task.id)}
        try:
            dev["last_changes"] = _apply_changes(deps, state, source, changes)
        except (ChangeApplyError, ToolPermissionError, OSError) as error:
            return {**update, **fail_stage(deps, state, stage="development", error=error)}
        dev["phase"] = "task"
        dev["fix_context"] = None
        return {**update, "development": dev, "lifecycle_status": _to(state, S.BUILDING)}

    return implement_task


def make_build(deps: WorkflowDependencies) -> NodeFn:
    def build(state: FactoryState) -> dict[str, Any]:
        dev = _dev(state)
        plan = _plan(state)
        source = _source(dev)
        fingerprint = manifest_fingerprint(source)
        steps: list[tuple[str, list[ProjectCommand]]] = []
        if plan.commands.setup and fingerprint != dev.get("setup_fingerprint"):
            steps.append(("setup", plan.commands.setup))
        steps += [
            ("format", plan.commands.format),
            ("lint", plan.commands.lint),
            ("build", plan.commands.build),
        ]
        verification = _verify(deps, state, source, "build", steps)
        if any(r.step == "setup" and r.success for r in verification.runs):
            dev["setup_fingerprint"] = fingerprint
        if not verification.passed:
            deps.recorder.write_stage_artifact("test_results", verification, state)
            return _fix_or_fail(deps, state, dev, verification, "build")
        return {
            "development": dev,
            "last_verification": verification.model_dump(mode="json"),
            "lifecycle_status": _to(state, S.TESTING),
        }

    return build


def make_test(deps: WorkflowDependencies) -> NodeFn:
    def test(state: FactoryState) -> dict[str, Any]:
        dev = _dev(state)
        plan = _plan(state)
        source = _source(dev)
        missing = _missing_planned_tests(plan, dev, source)
        if missing:
            # A test command can "pass" with zero tests; planned test files must exist.
            verification = VerificationResult(
                phase="test",
                passed=False,
                failed_step="test",
                timestamp=utc_now(),
                runs=[
                    CommandRun(
                        step="test",
                        argv=["(factory check) planned test files exist"],
                        cwd=".",
                        exit_code=1,
                        output_tail="The task's planned tests were not written. Missing test "
                        f"files: {', '.join(missing)}. Create them with real assertions for "
                        "the task's behaviour.",
                    )
                ],
            )
        else:
            verification = _verify(deps, state, source, "test", [("test", plan.commands.test)])
        deps.recorder.write_stage_artifact("test_results", verification, state)
        if not verification.passed:
            return _fix_or_fail(deps, state, dev, verification, "test")

        git = _git(deps, source)
        phase = dev.get("phase") or "task"
        if phase == "task":
            task = _task(plan, dev["task_queue"][0])
            version = int(state.get("version") or 1)
            prefix = f"v{version} " if version > 1 else ""  # task ids restart per version
            message = f"{prefix}{task.id}: {task.title}"
        else:
            message = (
                f"fix({phase}): {(dev.get('fix_context') or {}).get('summary', 'apply fixes')}"
            )
        committed = git.commit(message)
        if not committed.success:
            return fail_stage(
                deps,
                state,
                stage="test",
                error=FactoryError(f"git commit failed: {committed.stderr.strip()}"),
            )
        commit = git.head()
        dev.update(
            {
                "debug_iterations": 0,
                "fix_context": None,
                "last_changes": [],
                "last_commit": commit,
                "failure_history": [],
                "human_hint": None,
                "fix_attempts": [],
                "pending_attempt": None,
            }
        )
        update: dict[str, Any] = {"last_verification": verification.model_dump(mode="json")}

        if phase == "task":
            task_id = dev["task_queue"].pop(0)
            dev["completed_tasks"] = [
                *dev.get("completed_tasks", []),
                {"task_id": task_id, "commit": commit, "completed_at": utc_now()},
            ]
            if dev["task_queue"]:
                target = S.DEVELOPMENT
            else:
                update["completed_stages"] = [*state.get("completed_stages", []), "development"]
                target = S.CODE_REVIEW
        else:
            target = PHASE_RETURN[phase] or S.CODE_REVIEW
            dev["phase"] = None
        return {**update, "development": dev, "lifecycle_status": _to(state, target)}

    return test


def _missing_planned_tests(
    plan: ImplementationPlan, dev: dict[str, Any], source: Path
) -> list[str]:
    """Planned test *files* of the current task that do not exist.

    Only entries that look like file paths are checked (``tests/test_x.py``,
    ``src/app.test.ts``); plain test names cannot be checked deterministically.
    """
    if (dev.get("phase") or "task") != "task" or not dev.get("task_queue"):
        return []
    task = _task(plan, dev["task_queue"][0])
    return [
        test
        for test in task.tests
        if ("/" in test or Path(test).suffix) and " " not in test and not (source / test).exists()
    ]


NO_PROGRESS_LIMIT = 3  # identical failures in a row before stopping for a human


def _failure_signature(details: str) -> str:
    """A fingerprint of *what* failed, ignoring numbers that change between runs."""
    lines = sorted(
        {
            re.sub(r"\d+", "#", line.strip())
            for line in details.splitlines()
            if re.search(r"(Error|FAIL|assert|Exception|error:)", line)
        }
    )
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]


def make_debug(deps: WorkflowDependencies) -> NodeFn:
    def debug(state: FactoryState) -> dict[str, Any]:
        dev = _dev(state)
        dev["debug_iterations"] = dev.get("debug_iterations", 0) + 1
        plan = _plan(state)
        source = _source(dev)
        fix = dev.get("fix_context") or {}
        task = (
            _task(plan, dev["task_queue"][0])
            if dev.get("phase") == "task" and dev["task_queue"]
            else None
        )
        task_payload = (
            task.model_dump(mode="json")
            if task
            else {"id": "FIX", "title": f"Fix {fix.get('source', 'reported')} problems"}
        )
        tree = list_source_files(source)
        details = _fix_details(fix)
        guidance = dev.get("human_hint")
        if fix.get("source") == "verification":
            # Stuck detection: the same failure again means the last fix did not help.
            signature = _failure_signature(details)
            history = [*dev.get("failure_history", []), signature]
            dev["failure_history"] = history
            repeats = history.count(signature)
            if repeats >= NO_PROGRESS_LIMIT:
                return {
                    "development": dev,
                    **fail_stage(
                        deps,
                        state,
                        stage="bug_fixing",
                        error=FactoryError(
                            f"No progress: the same failure came back {repeats} times in a row "
                            "after automatic fixes",
                            hint="Look at test-results.md and test-analysis.md, then retry with "
                            'guidance: factory retry <project> --hint "what the real problem is"',
                        ),
                    ),
                }
            if repeats > 1:
                details += (
                    f"\n\nNO PROGRESS: this exact failure has now happened {repeats} times. "
                    "The previous fix did not work — do not repeat it. Re-read the implementation "
                    "code under test against the requirements; the bug is probably in the code, "
                    "not in the test."
                )
        preferred = [
            *mentioned_files(details, tree),
            *dev.get("last_changes", []),
            *(task.likely_files if task else []),
        ]
        _, files = _code_context(deps, source, preferred)

        analysis: TestFailureAnalysis | None = None
        updates: dict[str, Any] = {}
        if fix.get("source") == "verification":
            result, updates = invoke_agent(
                deps,
                state,
                deps.agents.testing,
                stage="bug_fixing",
                available={
                    "verification": details,
                    "task": task_payload,
                    "acceptance_criteria": (state.get("requirements") or {}).get(
                        "acceptance_criteria"
                    ),
                    "commands": _commands_summary(plan),
                    "relevant_files": files,
                    "human_guidance": guidance,
                },
            )
            if result is None:
                return {**updates, "development": dev}
            analysis = result.output
            deps.recorder.write_stage_artifact("test_analysis", analysis, state)
            if analysis.failure_category is FailureCategory.ENVIRONMENT:
                return {
                    **updates,
                    "development": dev,
                    **fail_stage(
                        deps,
                        state,
                        stage="bug_fixing",
                        error=FactoryError(
                            f"Environment problem, not a code problem: {analysis.root_cause}",
                            hint=f"{analysis.suggested_fix} Then `factory retry`.",
                        ),
                    ),
                }

        # Remember what the previous round's fix was, now that we know it failed.
        attempts = list(dev.get("fix_attempts") or [])
        if dev.get("pending_attempt") and fix.get("source") == "verification":
            attempts.append({**dev.pop("pending_attempt"), "result": _last_error_line(details)})
        dev["fix_attempts"] = attempts

        is_verification = fix.get("source") == "verification"
        candidates = deps.limits.fix_candidates if is_verification else 1
        tried: list[dict[str, Any]] = []
        agent_update: dict[str, Any] = {}
        chosen: CodeChanges | None = None
        allow_edits = True  # switched off once an edit fails to apply
        for index in range(candidates):
            result, agent_update = invoke_agent(
                deps,
                state,
                deps.agents.coding,
                stage="bug_fixing",
                available={
                    "mode": "fix",
                    "task": task_payload,
                    "requirements": state.get("requirements"),
                    "architecture": state.get("architecture"),
                    "technology_selection": state.get("technology_selection"),
                    "commands": _commands_summary(plan),
                    "file_tree": tree,
                    "relevant_files": files,
                    "fix_details": details + _attempts_text([*attempts, *tried]),
                    "failure_analysis": analysis.model_dump(mode="json") if analysis else None,
                    "change_request_feedback": dev.get("change_request_feedback"),
                    "human_guidance": guidance,
                    "allow_edits": allow_edits,
                },
            )
            if result is None:
                if chosen is None and not tried:
                    return {**agent_update, "development": dev}
                break
            dev.pop("change_request_feedback", None)
            changes: CodeChanges = result.output
            if changes.change_request:
                return {
                    **agent_update,
                    **_change_request_update(deps, state, dev, changes, task.id if task else None),
                }
            saved = _snapshot(source, changes.touched_paths)
            try:
                dev["last_changes"] = _apply_changes(deps, state, source, changes)
            except ChangeApplyError as error:
                _restore(source, saved)
                allow_edits = False  # small models repeat a failing edit; demand full files
                tried.append(
                    {
                        "summary": changes.summary,
                        "files": changes.touched_paths,
                        "result": f"could not be applied: {error}. Next attempt: do NOT use "
                        "`edits`; return the complete file in `files`.",
                    }
                )
                continue
            except (ToolPermissionError, OSError) as error:
                _restore(source, saved)
                return {**agent_update, **fail_stage(deps, state, stage="bug_fixing", error=error)}
            chosen = changes
            if candidates == 1:
                break
            # Try the candidate for real; keep the first one that passes.
            missing = _missing_planned_tests(plan, dev, source)
            check = (
                None
                if missing
                else _verify(deps, state, source, "candidate", _check_steps(plan, dev, source))
            )
            if check is not None and check.passed:
                deps.recorder.record_execution(
                    execution_record(
                        deps,
                        state,
                        kind=ExecutionKind.WORKFLOW,
                        stage="bug_fixing",
                        status=ExecutionStatus.INFO,
                        summary=f"Fix candidate {index + 1} of {candidates} passes; keeping it",
                    )
                )
                break
            outcome = (
                f"planned test files missing: {', '.join(missing)}"
                if missing
                else _last_error_line(
                    _fix_details({"source": "verification", "verification": check.model_dump()})
                )
                if check
                else "tests still failed"
            )
            if index < candidates - 1:
                _restore(source, saved)
                tried.append(
                    {"summary": changes.summary, "files": changes.touched_paths, "result": outcome}
                )
                chosen = None

        if chosen is None:
            dev["fix_attempts"] = [*attempts, *tried]
            not_applied = sum(1 for t in tried if t["result"].startswith("could not be applied"))
            failed_tests = len(tried) - not_applied
            reason = (
                f"None of the {len(tried)} proposed fixes could be applied"
                if not failed_tests
                else f"None of the {len(tried)} proposed fixes worked ({failed_tests} were "
                f"tested and still failed, {not_applied} could not be applied)"
            )
            return {
                **agent_update,
                "development": dev,
                **fail_stage(
                    deps,
                    state,
                    stage="bug_fixing",
                    error=ChangeApplyError(
                        reason,
                        hint='Retry, optionally with guidance: factory retry <project> --hint "…"',
                    ),
                ),
            }
        dev["fix_attempts"] = [*attempts, *tried]
        if is_verification:
            dev["pending_attempt"] = {"summary": chosen.summary, "files": chosen.touched_paths}
        fix["summary"] = chosen.summary[:120]
        dev["fix_context"] = fix
        return {
            **agent_update,
            "test_analysis": analysis.model_dump(mode="json") if analysis else None,
            "development": dev,
            "lifecycle_status": _to(state, S.BUILDING),
        }

    return debug


def _last_error_line(details: str) -> str:
    """The most telling line of a failure, for the attempt log."""
    lines = [line.strip() for line in details.splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(r"(Error|FAIL|assert|Exception)", line):
            return line[:200]
    return lines[-1][:200] if lines else "failed"


def _fix_details(fix: dict[str, Any]) -> str:
    source = fix.get("source")
    if source == "verification":
        verification = VerificationResult.model_validate(fix["verification"])
        failed = verification.runs[-1] if verification.runs else None
        if failed is None:
            return "Verification failed without output."
        return (
            f"Step '{failed.step}' failed.\nCommand: {' '.join(failed.argv)} (cwd: {failed.cwd})\n"
            f"Exit code: {failed.exit_code}{' (timed out)' if failed.timed_out else ''}\n"
            f"Output (last {OUTPUT_TAIL_CHARS} characters):\n{failed.output_tail}"
        )
    if source in ("review", "security", "validation"):
        return f"Fix these {source} findings:\n" + _json(fix.get("findings", []))
    if source == "release_feedback":
        return f"The human reviewer rejected the release: {fix.get('feedback', '')}"
    return _json(fix)


def prepare_retry(
    deps: WorkflowDependencies, state: FactoryState, target: S, hint: str = ""
) -> tuple[S, dict[str, Any], str]:
    """Adjust a human retry of a build-phase failure.

    - The fix budgets restart (debug iterations, review and security rounds).
    - A failed *fix* of a build/test failure restarts from a fresh build and
      test run, so a fixed environment is actually re-checked instead of the old
      output being analysed again.
    - After all tasks are done, manual edits the human made in the repository
      are committed, so security and final validation see a clean tree.
    """
    dev = _dev(state)
    dev.update(
        {"debug_iterations": 0, "review_rounds": 0, "security_rounds": 0, "failure_history": []}
    )
    notes = []
    if hint:
        dev["human_hint"] = hint  # kept until the failing work passes
    fix = dev.get("fix_context") or {}
    if target is S.BUG_FIXING and fix.get("source") == "verification":
        target = S.BUILDING
        dev["fix_context"] = None
        notes.append("re-running build and tests")
    if dev.get("source_path") and not dev.get("task_queue"):
        git = _git(deps, _source(dev))
        if git.state().clean is False and git.commit("chore: manual changes before retry").success:
            notes.append("committed manual changes")
            dev["last_commit"] = git.head()
    return target, dev, "; ".join(notes)
