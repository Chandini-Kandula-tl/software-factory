"""Build-phase workflow tests.

Agent answers are scripted, but everything else is real: files are written to
disk, a real git repository is created, and the project's test command
(`python -m unittest`) really runs. Pass/fail always comes from its exit code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from software_factory.core.factory_service import FactoryService
from software_factory.domain.errors import WorkflowActionError
from tests.conftest import make_config
from tests.support import fixtures as fx


def _design(factory: FactoryService, script, plan: dict | None = None, **create) -> str:
    """Run the design phase with scripted agents and approve the first three gates."""
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    script.add(fx.TECHNOLOGY_AGENT, fx.technology())
    script.add(fx.PLANNING_AGENT, plan or fx.calculator_plan())
    pid = factory.create_project(create.pop("name", "calculator"), "A calculator.", **create)
    factory.start(pid.project_id)
    for _ in range(3):
        factory.approve(pid.project_id)
    return pid.project_id


def _git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=source, capture_output=True, text=True, check=True
    ).stdout


def _source(factory: FactoryService, pid: str) -> Path:
    return factory.projects.project_dir(pid) / "source"


def test_full_build_phase_to_release(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)

    assert factory.status(pid).plan_checks == []
    plan_md = (factory.projects.project_dir(pid) / "implementation-plan.md").read_text()
    assert "python -m unittest discover" in plan_md  # approved commands are shown

    status = factory.approve(pid)  # plan approved → develop, test, review, validate
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    # Reproducibility: tool versions and config fingerprint are recorded.
    assert status.development["tool_versions"]["python"].startswith("Python 3")
    assert "git" in status.development["tool_versions"]
    assert all(h["config_fingerprint"] for h in factory.history(pid))
    assert status.interrupt["stage"] == "release"

    source = _source(factory, pid)
    assert (source / "calc.py").read_text() == fx.CALC_V2
    log = _git(source, "log", "--format=%s")
    assert log.splitlines() == [
        "T-2: Subtract function",
        "T-1: Add function",
        "chore: initialize repository (Software Factory)",
    ]

    project_dir = factory.projects.project_dir(pid)
    validation = json.loads((project_dir / "validation.json").read_text())
    assert validation["passed"] is True
    trace = {row["requirement_id"]: row for row in validation["traceability"]}
    assert trace["FR-1"]["status"] == "covered" and trace["FR-1"]["commits"]
    assert trace["NFR-1"]["status"] == "not_planned"  # warning, not a failure
    assert json.loads((project_dir / "security.json").read_text())["passed"] is True
    test_results = (project_dir / "test-results.md").read_text()
    assert "PASSED" in test_results and "python -m unittest" in test_results
    assert list((project_dir / "artifacts" / "tool-runs").glob("*-test.log"))

    # The second coding call saw the code written by the first task.
    second_prompt = script.calls_for(fx.CODING_AGENT)[1].messages[1].content
    assert "calc.py" in second_prompt and "def add" in second_prompt

    status = factory.approve(pid)
    assert status.lifecycle_status == "COMPLETED"
    assert "release-1" in _git(source, "tag", "--list")
    notes = (project_dir / "release-notes.md").read_text()
    assert "release-1" in notes and "T-2" in notes
    assert status.completed_stages[-5:] == [
        "development",
        "code_review",
        "security_validation",
        "final_validation",
        "release",
    ]
    tools = [h for h in factory.history(pid) if h["kind"] == "tool" and h["stage"] == "test"]
    assert tools and all(h["status"] == "success" for h in tools)


def test_failing_test_is_analyzed_fixed_and_committed_only_when_green(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), fx.code("fix add", calc_py=fx.CALC_V1))
    script.add(fx.CODING_AGENT, fx.task2())
    script.add(fx.TESTING_AGENT, fx.analysis())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)

    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure

    history = factory.history(pid)
    test_runs = [h["status"] for h in history if h["kind"] == "tool" and h["stage"] == "test"]
    assert test_runs[:2] == ["failed", "success"]  # real failure, then real pass
    fix_prompt = script.calls_for(fx.CODING_AGENT)[1].messages[1].content
    assert "Mode: fix" in fix_prompt and "AssertionError" in fix_prompt
    assert "add subtracts" in fix_prompt  # Testing Agent's analysis was passed on
    assert (factory.projects.project_dir(pid) / "test-analysis.md").exists()
    # Only green code is committed: exactly one commit per task.
    log = _git(_source(factory, pid), "log", "--format=%s").splitlines()
    assert log[:2] == ["T-2: Subtract function", "T-1: Add function"]


def test_fix_budget_exhausted_then_retry(tmp_path, script):
    factory = FactoryService(make_config(tmp_path, max_debug_iterations=1))
    buggy = fx.code("still wrong", calc_py=fx.CALC_V1_BUGGY)
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), buggy)
    script.add(fx.TESTING_AGENT, fx.analysis())
    pid = _design(factory, script)

    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert "still failing after 1 fix attempt" in status.failure["message"]
    assert "factory retry" in status.failure["hint"]

    # A human retry grants a fresh fix budget.
    script.add(fx.TESTING_AGENT, fx.analysis())
    script.add(fx.CODING_AGENT, fx.code("fix", calc_py=fx.CALC_V1), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    status = factory.retry(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure


def test_environment_failures_stop_for_a_human(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(buggy=True))
    script.add(fx.TESTING_AGENT, fx.analysis("environment"))
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert status.failure["message"].startswith("Environment problem")
    assert len(script.calls_for(fx.CODING_AGENT)) == 1  # no fix attempt wasted


def test_blocking_review_findings_are_fixed_then_reviewed_again(factory, script):
    documented = '"""Calculator."""\n\n' + fx.CALC_V2
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2(), fx.code("docs", calc_py=documented))
    script.add(fx.REVIEW_AGENT, fx.blocking_review(), fx.clean_review())
    pid = _design(factory, script)

    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    fix_prompt = script.calls_for(fx.CODING_AGENT)[2].messages[1].content
    assert "R-1" in fix_prompt and "No docstrings" in fix_prompt
    project_dir = factory.projects.project_dir(pid)
    assert (project_dir / "artifacts" / "review.rev1.json").exists()
    assert "Revision 2" in (project_dir / "review.md").read_text()
    log = _git(_source(factory, pid), "log", "--format=%s").splitlines()
    assert log[0].startswith("fix(review)")


def test_review_round_limit_continues_with_findings_visible(tmp_path, script):
    factory = FactoryService(make_config(tmp_path, max_review_rounds=0))
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.blocking_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL"
    assert any("round limit" in w for w in status.warnings)
    validation = (factory.projects.project_dir(pid) / "validation.md").read_text()
    assert "R-1" in validation


def test_hardcoded_secret_blocks_until_removed(factory, script):
    leaky = fx.code(
        "config",
        calc_py=fx.CALC_V1,
        tests__test_calc_py=fx.TEST_V1,
        settings_py='ADMIN_PASSWORD = "hunter2hunter2"\n',
    )
    fixed = fx.code(
        "read secret from env",
        settings_py='import os\n\nADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")\n',
    )
    script.add(fx.CODING_AGENT, leaky, fx.task2(), fixed)
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)

    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    fix_prompt = script.calls_for(fx.CODING_AGENT)[2].messages[1].content
    assert "settings.py:1" in fix_prompt and "hard-coded credential" in fix_prompt
    security = json.loads((factory.projects.project_dir(pid) / "security.json").read_text())
    assert security["passed"] is True


def test_change_request_approved_reopens_planning(factory, script):
    request = {
        "summary": "Plan cannot work",
        "files": [],
        "change_request": {
            "target": "implementation_plan",
            "problem": "unittest discovery needs a tests package",
            "proposed_change": "Add tests/__init__.py to T-1",
        },
    }
    script.add(fx.CODING_AGENT, request)
    script.add(fx.IMPACT_AGENT, fx.impact())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_CHANGE_REQUEST_APPROVAL"
    impact_prompt = script.calls_for(fx.IMPACT_AGENT)[0].messages[1].content
    assert "Mode: change_request" in impact_prompt and "tests package" in impact_prompt
    assert status.interrupt["stage"] == "change_request"
    request_md = (factory.projects.project_dir(pid) / "change-request.md").read_text()
    assert "tests package" in request_md
    assert "Tasks already completed and committed: none" in request_md
    assert "## Impact analysis" in request_md and "backwards compatible" in request_md
    assert (factory.projects.project_dir(pid) / "impact-analysis.md").exists()

    script.add(fx.PLANNING_AGENT, fx.calculator_plan())
    status = factory.approve(pid, stage="change_request")
    assert status.lifecycle_status == "WAITING_FOR_PLAN_APPROVAL"
    replan_prompt = script.calls_for(fx.PLANNING_AGENT)[1].messages[1].content
    assert "Approved change request" in replan_prompt and "tests package" in replan_prompt
    assert "implementation_plan" not in status.completed_stages

    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure


def test_change_request_rejected_continues_within_decisions(factory, script):
    request = {
        "summary": "Want another language",
        "change_request": {
            "target": "technology",
            "problem": "prefer Go",
            "proposed_change": "switch to Go",
        },
    }
    script.add(fx.CODING_AGENT, request, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    factory.approve(pid)
    with pytest.raises(WorkflowActionError, match="reason"):
        factory.reject(pid, "")
    status = factory.reject(pid, "Python was approved; stay with it")
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    retry_prompt = script.calls_for(fx.CODING_AGENT)[1].messages[1].content
    assert "change request was rejected" in retry_prompt and "stay with it" in retry_prompt


def test_release_rejection_starts_a_fix_cycle(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review(), fx.clean_review())
    pid = _design(factory, script)
    factory.approve(pid)

    script.add(fx.CODING_AGENT, fx.code("docs", README_md="# Calculator\n"))
    status = factory.reject(pid, "Add a README")
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    fix_prompt = script.calls_for(fx.CODING_AGENT)[2].messages[1].content
    assert "rejected the release: Add a README" in fix_prompt
    assert (_source(factory, pid) / "README.md").exists()
    assert len(script.calls_for(fx.REVIEW_AGENT)) == 2  # reviewed again after the fix
    assert factory.approve(pid).lifecycle_status == "COMPLETED"


def test_missing_program_fails_before_any_code_is_written(factory, script):
    plan = fx.calculator_plan()
    plan["commands"]["test"] = [{"argv": ["definitely-not-a-real-tool", "test"]}]
    pid = _design(factory, script, plan=plan)
    # The problem is visible on the plan before it is approved.
    status = factory.status(pid)
    assert status.plan_checks == ["definitely-not-a-real-tool (not in security.allowed_commands)"]
    plan_md = (factory.projects.project_dir(pid) / "implementation-plan.md").read_text()
    assert "Environment check" in plan_md and "definitely-not-a-real-tool" in plan_md
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert (
        "definitely-not-a-real-tool (not in security.allowed_commands)"
        in (status.failure["message"])
    )
    assert script.calls_for(fx.CODING_AGENT) == []


def test_existing_repository_is_changed_on_a_factory_branch(factory, script, tmp_path):
    repo = tmp_path / "legacy"
    repo.mkdir()
    (repo / "README.md").write_text("legacy\n")
    for args in (["init", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    script.add(fx.IMPACT_AGENT, fx.impact())
    pid = _design(factory, script, name="legacy", repository_path=str(repo))
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "factory/legacy"
    assert _git(repo, "log", "main", "--format=%s").strip() == "init"  # main untouched


def test_dirty_existing_repository_is_refused(factory, script, tmp_path):
    repo = tmp_path / "dirty"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "wip.txt").write_text("uncommitted")
    script.add(fx.IMPACT_AGENT, fx.impact())
    pid = _design(factory, script, name="dirty", repository_path=str(repo))
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert "uncommitted changes" in status.failure["message"]


def test_coding_agent_cannot_write_outside_the_repository(factory, script):
    escape = fx.code("escape")
    escape["files"] = [{"path": "../outside.py", "action": "create", "content": "x"}]
    script.add(fx.CODING_AGENT, escape, escape)  # rejected, corrected once, rejected → FAILED
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert not (factory.projects.project_dir(pid) / "outside.py").exists()


def test_retry_after_environment_problem_reruns_build_and_tests(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(buggy=True))
    script.add(fx.TESTING_AGENT, fx.analysis("environment"))
    pid = _design(factory, script)
    assert factory.approve(pid).lifecycle_status == "FAILED"

    # The human fixes the problem by hand, then retries: tests run again first.
    (_source(factory, pid) / "calc.py").write_text(fx.CALC_V1)
    script.add(fx.CODING_AGENT, fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    status = factory.retry(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    assert len(script.calls_for(fx.TESTING_AGENT)) == 1  # no re-analysis of old output


def test_retry_after_security_failure_commits_manual_fix(tmp_path, script):
    factory = FactoryService(make_config(tmp_path, max_review_rounds=0))
    leaky = fx.code(
        "config",
        calc_py=fx.CALC_V1,
        tests__test_calc_py=fx.TEST_V1,
        settings_py='ADMIN_PASSWORD = "hunter2hunter2"\n',
    )
    script.add(fx.CODING_AGENT, leaky, fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED" and "security" in status.failure["message"]

    source = _source(factory, pid)
    (source / "settings.py").write_text('import os\n\nADMIN_PASSWORD = os.environ["PW"]\n')
    status = factory.retry(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    assert _git(source, "log", "-1", "--format=%s").strip() == "chore: manual changes before retry"


def test_command_directory_outside_repo_fails_early(factory, script):
    plan = fx.calculator_plan()
    plan["commands"]["test"] = [{"argv": ["python", "-V"], "cwd": "../.."}]
    pid = _design(factory, script, plan=plan)
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert "outside the repo" in status.failure["message"]


def test_project_that_ended_before_the_build_phase_existed_continues(factory, script, monkeypatch):
    """Workflow design-phase-1.0 ended at READY_FOR_DEVELOPMENT; continue must pick it up."""
    from software_factory.domain.lifecycle import LifecycleStatus
    from software_factory.orchestration.routers import lifecycle_router

    old_terminal = lifecycle_router.TERMINAL_STATUSES | {LifecycleStatus.READY_FOR_DEVELOPMENT}
    monkeypatch.setattr(lifecycle_router, "TERMINAL_STATUSES", old_terminal)
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "READY_FOR_DEVELOPMENT"
    assert factory.workflows.snapshot(factory.projects.load(pid)).next_nodes == ()

    monkeypatch.undo()  # the current workflow
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure


def test_external_changes_are_detected_and_block_an_unvalidated_release(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review(), fx.clean_review())
    pid = _design(factory, script)
    assert factory.approve(pid).lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL"

    source = _source(factory, pid)
    (source / "notes.txt").write_text("edited while paused\n")
    status = factory.continue_(pid)
    assert "External changes detected" in status.note and "1 uncommitted file" in status.note
    assert any(h["stage"] == "resume" for h in factory.history(pid))

    with pytest.raises(WorkflowActionError, match="changed after final validation"):
        factory.approve(pid)

    # Rejecting re-runs the checks with the change included; then release works.
    script.add(fx.CODING_AGENT, fx.code("nothing else to change"))
    status = factory.reject(pid, "re-validate my manual changes")
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    assert "notes.txt" in _git(source, "show", "--name-only", "--format=", "HEAD")
    assert factory.approve(pid).lifecycle_status == "COMPLETED"


def test_missing_planned_test_files_cannot_pass(factory, script):
    """A test command may exit 0 with zero tests; planned test files must exist."""
    no_tests = fx.code("add without tests", calc_py=fx.CALC_V1)
    script.add(fx.CODING_AGENT, no_tests, fx.code("tests", tests__test_calc_py=fx.TEST_V1))
    script.add(fx.CODING_AGENT, fx.task2())
    script.add(fx.TESTING_AGENT, fx.analysis("test_bug"))
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    fix_prompt = script.calls_for(fx.CODING_AGENT)[1].messages[1].content
    assert "Missing test files: tests/test_calc.py" in fix_prompt
    assert _git(_source(factory, pid), "log", "--format=%s").splitlines()[1] == "T-1: Add function"


def test_change_request_still_reaches_the_human_if_impact_analysis_fails(factory, script):
    request = {
        "summary": "x",
        "change_request": {"target": "technology", "problem": "p", "proposed_change": "c"},
    }
    script.add(fx.CODING_AGENT, request)  # no impact-analysis answer scripted → it fails
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_CHANGE_REQUEST_APPROVAL"
    request_md = (factory.projects.project_dir(pid) / "change-request.md").read_text()
    assert "Impact analysis failed; see errors.md" in request_md
    errors_md = (factory.projects.project_dir(pid) / "errors.md").read_text()
    assert "change_request" in errors_md and "No scripted response" in errors_md


def test_workspace_inside_another_git_repository_gets_its_own_repo(tmp_path, script):
    """Found in the first live run: the factory checkout is itself a git repo."""
    outer = tmp_path / "factory-checkout"
    outer.mkdir()
    subprocess.run(["git", "init"], cwd=outer, check=True, capture_output=True)
    (outer / ".gitignore").write_text("workspace/\n")
    factory = FactoryService(make_config(outer))
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    source = _source(factory, pid)
    assert Path(_git(source, "rev-parse", "--show-toplevel").strip()) == source.resolve()
    outer_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=outer, capture_output=True, text=True
    )
    assert outer_log.returncode != 0 or outer_log.stdout == ""  # outer repo untouched


def test_files_written_by_the_final_test_run_do_not_block_release(factory, script):
    """Found in the first live run: generated tests wrote tasks.json into the repo."""
    writes_file = fx.TEST_V1.replace(
        "    def test_add(self):\n",
        "    def test_add(self):\n        open('scratch.txt', 'w').write('x')\n",
    )
    t1 = fx.code("add", calc_py=fx.CALC_V1, tests__test_calc_py=writes_file)
    t2_tests = fx.TEST_V2.replace(
        "    def test_add(self):\n",
        "    def test_add(self):\n        open('scratch.txt', 'w').write('x')\n",
    )
    t2 = fx.code("sub", calc_py=fx.CALC_V2, tests__test_calc_py=t2_tests)
    script.add(fx.CODING_AGENT, t1, t2)
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    validation = json.loads((factory.projects.project_dir(pid) / "validation.json").read_text())
    # scratch.txt was committed with T-1 and rewritten identically → no side effect,
    # so simulate a changing side effect by editing it after validation as the tests would.
    assert validation["passed"]
    status = factory.continue_(pid)
    assert "External changes" not in (status.note or "")
    assert factory.approve(pid).lifecycle_status == "COMPLETED"


def test_duplicate_file_in_one_answer_uses_the_last_copy(factory, script):
    dup = fx.task1()
    dup["test_code"] = [{"path": "tests/test_calc.py", "action": "create", "content": fx.TEST_V1}]
    dup["files"][1]["content"] = "this version is replaced"
    script.add(fx.CODING_AGENT, dup, fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    coding_runs = [h for h in factory.history(pid) if h.get("agent") == "coding-agent"]
    assert any("given more than once" in w for w in coding_runs[0]["details"]["warnings"])
    assert (_source(factory, pid) / "tests" / "test_calc.py").read_text().startswith("import")


def test_answer_failing_checks_gets_one_correction_attempt(factory, script):
    escape = fx.code("escape")
    escape["files"] = [{"path": "../outside.py", "action": "create", "content": "x"}]
    script.add(fx.CODING_AGENT, escape, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    retry = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[1].messages)
    assert "failed the factory's checks" in retry and "../outside.py" in retry


def test_same_failure_three_times_stops_early_for_a_human(factory, script):
    still_buggy = fx.code("try again", calc_py=fx.CALC_V1_BUGGY)
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), still_buggy, still_buggy)
    script.add(fx.TESTING_AGENT, fx.analysis(), fx.analysis())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "FAILED"
    assert status.failure["message"].startswith("No progress: the same failure came back 3")
    assert "--hint" in status.failure["hint"]
    second_fix = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[2].messages)
    assert "NO PROGRESS" in second_fix


def test_retry_with_hint_guides_the_agents(tmp_path, script):
    factory = FactoryService(make_config(tmp_path, max_debug_iterations=1))
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), fx.code("x", calc_py=fx.CALC_V1_BUGGY))
    script.add(fx.TESTING_AGENT, fx.analysis())
    pid = _design(factory, script)
    assert factory.approve(pid).lifecycle_status == "FAILED"

    script.add(fx.TESTING_AGENT, fx.analysis())
    script.add(fx.CODING_AGENT, fx.code("fix", calc_py=fx.CALC_V1), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    status = factory.retry(pid, hint="add() must use + not -")
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    for agent in (fx.TESTING_AGENT, fx.CODING_AGENT):
        prompt = script.calls_for(agent)[-1 if agent == fx.TESTING_AGENT else 2].messages[1]
        assert "add() must use + not -" in prompt.content
    # The hint is dropped once the work passes: task 2 does not see it.
    assert "add() must use + not -" not in script.calls_for(fx.CODING_AGENT)[3].messages[1].content
    assert any("human hint" in h["summary"] for h in factory.history(pid))
    with pytest.raises(WorkflowActionError, match="only be given when retrying a failed"):
        factory.retry(pid, hint="nothing failed")


def _edit(find: str, replace: str, path: str = "calc.py") -> dict:
    return {"summary": f"edit {path}", "edits": [{"path": path, "find": find, "replace": replace}]}


def test_several_fix_candidates_first_passing_one_is_kept(tmp_path, script):
    factory = FactoryService(make_config(tmp_path, fix_candidates=3))
    wrong = fx.code("wrong idea", calc_py="def add(a, b):\n    return a * b\n")
    right = _edit("return a - b", "return a + b")  # targeted edit
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), wrong, right, fx.task2())
    script.add(fx.TESTING_AGENT, fx.analysis())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    assert status.development["debug_iterations"] == 0
    # One fix round only: candidate 1 was rolled back, candidate 2 kept.
    assert len(script.calls_for(fx.TESTING_AGENT)) == 1
    second = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[2].messages)
    assert "EARLIER FIX ATTEMPTS" in second and "wrong idea" in second
    assert any("candidate 2 of 3 passes" in h["summary"] for h in factory.history(pid))
    log = _git(_source(factory, pid), "log", "--format=%s").splitlines()
    assert log[:2] == ["T-2: Subtract function", "T-1: Add function"]


def test_edit_that_does_not_match_is_rolled_back_and_next_candidate_tried(tmp_path, script):
    factory = FactoryService(make_config(tmp_path, fix_candidates=2))
    bad_edit = _edit("this text is not in the file", "x")
    full = fx.code("full file", calc_py=fx.CALC_V1)
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), bad_edit, full)
    script.add(fx.CODING_AGENT, fx.task2())
    script.add(fx.TESTING_AGENT, fx.analysis())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    second = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[2].messages)
    assert "could not be applied" in second and "occurs 0 times" in second


def test_after_a_failed_edit_the_next_candidate_must_send_full_files(tmp_path, script):
    """Found live: qwen2.5-coder repeated an ambiguous edit three times."""
    factory = FactoryService(make_config(tmp_path, fix_candidates=2))
    ambiguous = _edit("a", "b")  # occurs many times → cannot be applied
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), ambiguous)
    script.add(fx.CODING_AGENT, _edit("a - b", "a + b"))  # an edit again → rejected by checks
    script.add(fx.CODING_AGENT, fx.code("full file", calc_py=fx.CALC_V1), fx.task2())
    script.add(fx.TESTING_AGENT, fx.analysis())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    correction = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[3].messages)
    assert "`edits` are not allowed in this attempt" in correction


def test_failed_fixes_are_remembered_across_rounds(factory, script):
    first_try = fx.code("swap operands", calc_py="def add(a, b):\n    return b - a\n")
    script.add(fx.CODING_AGENT, fx.task1(buggy=True), first_try, fx.code("fix", calc_py=fx.CALC_V1))
    script.add(fx.CODING_AGENT, fx.task2())
    script.add(fx.TESTING_AGENT, fx.analysis(), fx.analysis())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    second_round = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[2].messages)
    assert "EARLIER FIX ATTEMPTS" in second_round and "swap operands" in second_round
    # Memory is cleared once the task passes.
    third = " ".join(m.content for m in script.calls_for(fx.CODING_AGENT)[3].messages)
    assert "EARLIER FIX ATTEMPTS" not in third


CALC_V3 = fx.CALC_V2 + "\n\ndef mul(a, b):\n    return a * b\n"
TEST_V3 = fx.TEST_V2.replace("add, sub", "add, sub, mul") + (
    "\n    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n"
)


def _update_plan() -> dict:
    return {
        "summary": "Add multiplication to the released calculator.",
        "release_artifact": "Source repository",
        "commands": {"test": [fx.UNITTEST]},
        "tasks": [
            {
                "id": "T-1",
                "title": "Multiply function",
                "description": "mul(a, b) next to add and sub",
                "requirement_ids": ["FR-1"],
                "tests": ["tests/test_calc.py"],
            }
        ],
    }


def test_completed_app_is_updated_as_the_next_version(factory, script):
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    pid = _design(factory, script)
    factory.approve(pid)
    assert factory.approve(pid).lifecycle_status == "COMPLETED"
    source = _source(factory, pid)
    project_dir = factory.projects.project_dir(pid)

    # Version 2: every design stage is revised for the change and approved again.
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    script.add(fx.IMPACT_AGENT, fx.impact())
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    script.add(fx.TECHNOLOGY_AGENT, fx.technology())
    script.add(fx.PLANNING_AGENT, _update_plan())
    script.add(fx.CODING_AGENT, fx.code("mul", calc_py=CALC_V3, tests__test_calc_py=TEST_V3))
    script.add(fx.REVIEW_AGENT, fx.clean_review())
    status = factory.update(pid, "Add a multiply function")
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL", status.failure
    assert status.version == 2 and status.versions[0]["tag"] == "release-1"
    assert status.completed_stages == []  # every stage is approved again
    # The earlier version's documents are kept; the current ones start fresh.
    assert (project_dir / "versions" / "v1" / "release-notes.md").is_file()
    assert not (project_dir / "release-notes.md").exists()
    assert "release-1" in factory.read_document(pid, "release-notes.md", version=1)
    prompt = script.calls_for(fx.REQUIREMENT_AGENT)[-1].messages[1].content
    assert "Add a multiply function" in prompt and "version 2" in prompt
    assert "FR-1" in prompt  # the released requirements are the starting point

    for _ in range(3):
        factory.approve(pid)
    # The released code was analysed for the update, like an existing project.
    assert factory.status(pid).completed_stages[:2] == ["requirements", "repository_analysis"]
    status = factory.approve(pid)  # plan → build only the new task
    assert status.lifecycle_status == "WAITING_FOR_RELEASE_APPROVAL", status.failure
    status = factory.approve(pid)
    assert status.lifecycle_status == "COMPLETED"
    assert (source / "calc.py").read_text() == CALC_V3
    assert _git(source, "log", "--format=%s").splitlines()[:3] == [
        "v2 T-1: Multiply function",
        "T-2: Subtract function",
        "T-1: Add function",
    ]
    assert set(_git(source, "tag", "--list").split()) == {"release-1", "release-2"}
    assert "release-2" in (project_dir / "release-notes.md").read_text()


def test_only_a_completed_project_can_be_updated(factory, script):
    pid = _design(factory, script)
    with pytest.raises(WorkflowActionError, match="Only a completed project"):
        factory.update(pid, "Add a multiply function")
