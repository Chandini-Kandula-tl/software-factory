"""End-to-end design-phase workflow tests (spec §51 acceptance scenario, §52 cases)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from software_factory.core.factory_service import FactoryService
from software_factory.domain.errors import (
    FactoryError,
    InvalidProjectIdError,
    ProjectFolderMissingError,
    ProjectNotFoundError,
    WorkflowActionError,
)
from software_factory.llm.errors import LLMError, LLMErrorKind
from tests.support import fixtures as fx


def _create(factory: FactoryService, name: str = "employee-management") -> str:
    return factory.create_project(name, fx.EMPLOYEE_REQUIREMENTS).project_id


def _script_design(script, clarify: bool = True) -> None:
    if clarify:
        script.add(fx.REQUIREMENT_AGENT, fx.requirements_needing_clarification())
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    script.add(fx.TECHNOLOGY_AGENT, fx.technology())
    script.add(fx.PLANNING_AGENT, fx.plan())


def test_mvp_acceptance_scenario_with_restart(config, script, tmp_path):
    """create → questions → answers → approve ×4, with a process restart mid-way."""
    _script_design(script)
    factory = FactoryService(config)
    pid = _create(factory)

    status = factory.start(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_CLARIFICATION"
    questions, _ = factory.questions(pid)
    assert [q.id for q in questions] == ["Q1", "Q2", "Q3"]
    assert [q.required for q in questions] == [True, True, False]

    assert factory.answer(pid, "Q1", "Web application") == ["Q2"]

    # Simulate the user terminating the process: a brand-new service instance
    # must see the persisted answer without any chat history.
    factory = FactoryService(config)
    assert factory.answer(pid, "q2", "Admins see everything; employees see themselves") == []
    with pytest.raises(WorkflowActionError):
        factory.approve(pid)  # not waiting for approval yet

    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    assert status.interrupt["stage"] == "requirements"
    # Second requirement-agent call saw the answers.
    second_prompt = script.calls_for(fx.REQUIREMENT_AGENT)[1].messages[1].content
    assert "Web application" in second_prompt

    status = factory.approve(pid, stage="requirements")
    assert status.lifecycle_status == "WAITING_FOR_ARCHITECTURE_APPROVAL"

    factory = FactoryService(config)  # restart again
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_TECHNOLOGY_APPROVAL"
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_PLAN_APPROVAL"
    status = factory.approve(pid)
    # Plan approval starts the build phase; no coding output is scripted here
    # (see test_build_workflow.py), so the first coding call fails in a controlled way.
    assert status.lifecycle_status == "FAILED"
    assert status.failure["stage"] == "development"
    assert status.completed_stages == [
        "requirements",
        "repository_analysis",
        "architecture",
        "technology",
        "implementation_plan",
    ]

    project_dir = factory.projects.project_dir(pid)
    for name in (
        "project.md",
        "requirements.md",
        "requirements.json",
        "architecture.md",
        "architecture.json",
        "technology-stack.md",
        "technology-stack.json",
        "implementation-plan.md",
        "implementation-plan.json",
        "state.json",
        "progress.md",
        "decisions.md",
        "errors.md",
        "execution-history.json",
    ):
        assert (project_dir / name).exists(), name
    assert (project_dir / "checkpoints" / "workflow-checkpoints.sqlite").exists()

    requirements_md = (project_dir / "requirements.md").read_text()
    assert "Web application" in requirements_md  # clarification recorded
    assert "preferred color scheme" not in requirements_md.split("## Clarifications")[1]
    decisions = (project_dir / "decisions.md").read_text()
    assert decisions.count("approved by human") == 4

    state = json.loads((project_dir / "state.json").read_text())
    assert state["development"]["task_queue"] == ["T-1", "T-2"]
    assert set(state["approvals"]) == {
        "requirements",
        "architecture",
        "technology",
        "implementation_plan",
    }

    history = factory.history(pid)
    agent_runs = [h for h in history if h["kind"] == "agent" and h["status"] == "success"]
    assert [h["agent"] for h in agent_runs] == [
        fx.REQUIREMENT_AGENT,
        fx.REQUIREMENT_AGENT,
        fx.ARCHITECTURE_AGENT,
        fx.TECHNOLOGY_AGENT,
        fx.PLANNING_AGENT,
    ]
    first = agent_runs[0]
    assert first["provider"] == "local" and first["model"] == "test-model"
    assert first["prompt_versions"]["requirement/system.md"].startswith("1.0.0+")
    assert first["factory_version"] and first["workflow_version"]
    assert sum(1 for h in history if h["kind"] == "human_decision") == 4


def test_clear_requirements_skip_clarification(factory, script):
    _script_design(script, clarify=False)
    pid = _create(factory)
    status = factory.start(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    questions, _ = factory.questions(pid)
    assert questions == []


def test_multiple_clarification_rounds(factory, script):
    second_round = fx.requirements_needing_clarification()
    second_round["clarification_questions"] = [
        {"question": "How many employees?", "required": True, "impact_area": "data"}
    ]
    script.add(
        fx.REQUIREMENT_AGENT,
        fx.requirements_needing_clarification(),
        second_round,
        fx.clear_requirements(),
    )
    pid = _create(factory)
    factory.start(pid)
    factory.answer(pid, "Q1", "Web")
    factory.answer(pid, "Q2", "Admins")
    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_CLARIFICATION"
    questions, _ = factory.questions(pid)
    assert [q.id for q in questions] == ["Q4"]  # ids continue across rounds
    assert questions[0].round == 2
    factory.answer(pid, "Q4", "About 300")
    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    third_prompt = script.calls_for(fx.REQUIREMENT_AGENT)[2].messages[1].content
    assert "About 300" in third_prompt and "Admins" in third_prompt


def test_continue_refuses_until_required_answers_exist(factory, script):
    script.add(fx.REQUIREMENT_AGENT, fx.requirements_needing_clarification())
    pid = _create(factory)
    factory.start(pid)
    factory.answer(pid, "Q1", "Web")
    with pytest.raises(WorkflowActionError, match="Q2"):
        factory.continue_(pid)
    with pytest.raises(WorkflowActionError, match="Unknown question"):
        factory.answer(pid, "Q9", "x")


def test_clarification_round_limit_records_open_questions(tmp_path, script):
    from tests.conftest import make_config

    factory = FactoryService(make_config(tmp_path, max_clarification_rounds=1))
    new_question = fx.requirements_needing_clarification()
    new_question["clarification_questions"] = [
        {"question": "Which countries' labour laws apply?", "required": True}
    ]
    script.add(fx.REQUIREMENT_AGENT, fx.requirements_needing_clarification(), new_question)
    pid = _create(factory)
    factory.start(pid)
    factory.answer(pid, "Q1", "Web")
    factory.answer(pid, "Q2", "Admins")
    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    assert any("Clarification limit" in w for w in status.warnings)
    requirements = json.loads((factory.projects.project_dir(pid) / "requirements.json").read_text())
    assert "Which countries' labour laws apply?" in requirements["open_questions"]


def test_reject_revises_with_feedback_then_approve(factory, script):
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    revised = fx.architecture()
    revised["style"] = "client-server"
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture(), revised)
    pid = _create(factory)
    factory.start(pid)
    factory.approve(pid)

    with pytest.raises(WorkflowActionError, match="reason"):
        factory.reject(pid, "")
    with pytest.raises(WorkflowActionError, match="Unknown stage"):
        factory.approve(pid, stage="nonsense")
    with pytest.raises(WorkflowActionError, match="waiting for 'architecture'"):
        factory.approve(pid, stage="technology")

    status = factory.reject(pid, "Prefer a simpler client-server split")
    assert status.lifecycle_status == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert status.interrupt["revision"] == 2
    revision_prompt = script.calls_for(fx.ARCHITECTURE_AGENT)[1].messages[1].content
    assert "Prefer a simpler client-server split" in revision_prompt
    assert "modular monolith" in revision_prompt  # previous proposal supplied

    project_dir = factory.projects.project_dir(pid)
    assert (project_dir / "artifacts" / "architecture.rev1.json").exists()
    assert (project_dir / "artifacts" / "architecture.rev2.json").exists()
    assert "client-server" in (project_dir / "architecture.md").read_text()
    decisions = (project_dir / "decisions.md").read_text()
    assert "architecture** rejected" in decisions

    script.add(fx.TECHNOLOGY_AGENT, fx.technology())
    status = factory.approve(pid)
    assert status.lifecycle_status == "WAITING_FOR_TECHNOLOGY_APPROVAL"


def test_llm_failure_then_retry(factory, script):
    unavailable = LLMError(
        LLMErrorKind.MODEL_UNAVAILABLE,
        "model not pulled",
        "local",
        "test-model",
        hint="ollama pull test-model",
    )
    script.add(fx.REQUIREMENT_AGENT, unavailable)
    pid = _create(factory)
    status = factory.start(pid)
    assert status.lifecycle_status == "FAILED"
    assert status.failure["stage"] == "requirements"
    assert status.failure["error_kind"] == "model_unavailable"
    assert status.failure["hint"] == "ollama pull test-model"
    errors_md = (factory.projects.project_dir(pid) / "errors.md").read_text()
    assert "model not pulled" in errors_md
    failed = [h for h in factory.history(pid) if h["status"] == "failed"]
    assert failed and failed[0]["llm_calls"][0]["error_kind"] == "model_unavailable"

    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    status = factory.retry(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    assert status.failure is None


def test_invalid_output_fails_after_bounded_repairs(factory, script):
    script.add(fx.REQUIREMENT_AGENT, "not json", '{"summary": 5}', "{}")
    pid = _create(factory)
    status = factory.start(pid)
    assert status.lifecycle_status == "FAILED"
    assert status.failure["error_kind"] == "invalid_structured_output"
    assert len(script.calls_for(fx.REQUIREMENT_AGENT)) == 3  # 1 + 2 repairs, then stop


def test_cancel_after_failure(factory, script):
    script.add(fx.REQUIREMENT_AGENT, LLMError(LLMErrorKind.AUTHENTICATION, "bad key"))
    pid = _create(factory)
    factory.start(pid)
    status = factory.abort(pid)
    assert status.lifecycle_status == "CANCELLED"
    assert factory.continue_(pid).note == "Nothing to continue."


def test_killed_mid_run_resumes_from_checkpoint(factory, script):
    """A crash inside a node leaves the last checkpoint intact; continue re-runs it."""
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    script.add(fx.ARCHITECTURE_AGENT, KeyboardInterrupt())
    pid = _create(factory)
    factory.start(pid)
    with pytest.raises(KeyboardInterrupt):
        factory.approve(pid)
    status = factory.status(pid)
    assert status.interrupted_mid_run
    assert "interrupted" in status.pending_action.lower()

    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_ARCHITECTURE_APPROVAL"
    assert len(script.calls_for(fx.REQUIREMENT_AGENT)) == 1  # not re-run


def test_pause_stops_run_after_current_step(factory, script, monkeypatch):
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    pid = _create(factory)
    original = factory.projects.registry.set_lifecycle_status

    def pause_when_analyzing(project_id: str, status: str) -> None:
        original(project_id, status)
        if status == "WAITING_FOR_REQUIREMENT_APPROVAL":
            factory.projects.registry.set_paused(project_id, True)

    monkeypatch.setattr(factory.projects.registry, "set_lifecycle_status", pause_when_analyzing)
    factory.projects.registry.set_paused(pid, False)
    status = factory.start(pid)
    assert status.stopped_by_pause
    assert status.project.paused
    assert "Paused" in status.pending_action

    monkeypatch.undo()
    status = factory.continue_(pid)
    assert not status.project.paused
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"


def test_pause_command_marks_project(factory, script):
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    pid = _create(factory)
    factory.start(pid)
    status = factory.pause(pid)
    assert status.project.paused
    status = factory.continue_(pid)
    assert not status.project.paused


def test_projects_are_isolated(factory, script):
    script.add(
        fx.REQUIREMENT_AGENT, fx.clear_requirements(), fx.requirements_needing_clarification()
    )
    a = _create(factory, "project-a")
    b = factory.create_project("project-b", "A command-line todo tool.").project_id
    factory.start(a)
    factory.start(b)
    assert factory.status(a).lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    assert factory.status(b).lifecycle_status == "WAITING_FOR_REQUIREMENT_CLARIFICATION"
    prompt_b = script.calls_for(fx.REQUIREMENT_AGENT)[1].messages[1].content
    assert "command-line todo" in prompt_b and "employee" not in prompt_b.lower()
    dir_a, dir_b = factory.projects.project_dir(a), factory.projects.project_dir(b)
    assert dir_a != dir_b
    assert all(h["project_id"] == a for h in factory.history(a))
    with pytest.raises(WorkflowActionError):
        factory.answer(a, "Q1", "x")  # questions belong to project b only


def test_invalid_and_unknown_project_ids(factory):
    with pytest.raises(InvalidProjectIdError):
        factory.status("../etc")
    with pytest.raises(ProjectNotFoundError):
        factory.status("does-not-exist")


def test_start_requires_requirements_and_only_once(factory, script):
    pid = factory.create_project("empty").project_id
    with pytest.raises(WorkflowActionError, match="no requirements"):
        factory.start(pid)
    factory.set_requirements(pid, fx.EMPLOYEE_REQUIREMENTS)
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    factory.start(pid)
    with pytest.raises(WorkflowActionError, match="already started"):
        factory.start(pid)
    with pytest.raises(WorkflowActionError):
        factory.set_requirements(pid, "changed")


def test_existing_project_runs_repository_analysis(factory, script, tmp_path: Path):
    repo = tmp_path / "legacy-app"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('hi')\n")
    (repo / "pyproject.toml").write_text('[project]\ndependencies = ["fastapi"]\n')
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    script.add(fx.IMPACT_AGENT, fx.impact())
    project = factory.create_project("legacy", "Add an audit log.", repository_path=str(repo))
    assert project.mode.value == "existing"
    factory.start(project.project_id)
    factory.approve(project.project_id)
    project_dir = factory.projects.project_dir(project.project_id)
    analysis = json.loads((project_dir / "repository-analysis.json").read_text())
    assert analysis["languages"] == {"Python": 1}
    assert "FastAPI" in analysis["frameworks"]
    architecture_prompt = script.calls_for(fx.ARCHITECTURE_AGENT)[0].messages[1].content
    assert "pyproject.toml" in architecture_prompt
    # Spec §68.7: impact analysis runs on the existing system before architecture.
    assert "Existing note listing" in architecture_prompt
    impact_md = (project_dir / "impact-analysis.md").read_text()
    assert "backwards compatible" in impact_md and "Existing note listing" in impact_md
    impact_prompt = script.calls_for(fx.IMPACT_AGENT)[0].messages[1].content
    assert "Mode: existing_project" in impact_prompt and "FastAPI" in impact_prompt


def test_secrets_are_not_persisted(factory, script, monkeypatch):
    secret = "sk-test-SECRET-value-1234567890abcdef"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    script.add(
        fx.REQUIREMENT_AGENT,
        LLMError(LLMErrorKind.AUTHENTICATION, f"invalid key {secret}"),
    )
    pid = _create(factory)
    factory.start(pid)
    workspace = factory.config.workspace_path
    for path in workspace.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".log"}:
            assert secret not in path.read_text(), path


def test_repeated_question_is_not_asked_again(factory, script):
    """Real qwen2.5:7b behaviour: after answers, it re-asked the same question."""
    repeat = fx.clear_requirements()
    repeat["needs_clarification"] = True
    repeat["clarification_questions"] = [
        {"question": "Who should be able to access employee information??", "required": True}
    ]
    script.add(fx.REQUIREMENT_AGENT, fx.requirements_needing_clarification(), repeat)
    pid = _create(factory)
    factory.start(pid)
    factory.answer(pid, "Q1", "Web")
    factory.answer(pid, "Q2", "Admins only")
    status = factory.continue_(pid)
    assert status.lifecycle_status == "WAITING_FOR_REQUIREMENT_APPROVAL"
    assert any("repeat already-answered" in w for w in status.warnings)
    requirements = json.loads((factory.projects.project_dir(pid) / "requirements.json").read_text())
    assert not any("access employee" in q for q in requirements["open_questions"])


def test_existing_code_must_be_a_git_repository_root(factory, tmp_path: Path):
    """Found live: a user picked the folder that holds all their repositories."""
    parent = tmp_path / "GitHub"
    (parent / "app-one").mkdir(parents=True)
    with pytest.raises(FactoryError, match="not the top folder of a git repository"):
        factory.create_project("x", "Change it.", repository_path=str(parent))


def test_delete_project_removes_folder_and_entry(factory, script):
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    pid = _create(factory)
    factory.start(pid)
    folder = factory.projects.project_dir(pid)
    assert folder.is_dir()
    factory.delete_project(pid)
    assert not folder.exists()
    assert pid not in [p.project_id for p in factory.list_projects()]
    with pytest.raises(ProjectNotFoundError):
        factory.status(pid)


def test_deleted_folder_is_reported_not_recreated(factory, script):
    """Found live: viewing a project whose folder was deleted re-created it."""
    import shutil

    pid = _create(factory)
    folder = factory.projects.project_dir(pid)
    shutil.rmtree(folder)
    assert [p.folder_missing for p in factory.list_projects()] == [True]
    with pytest.raises(ProjectFolderMissingError, match="no longer exists"):
        factory.status(pid)
    assert not folder.exists()  # still gone
    factory.delete_project(pid)
    assert factory.list_projects() == []


def test_delete_is_refused_while_the_project_runs(factory, script):
    pid = _create(factory)
    with factory.workflows.run_lock(pid), pytest.raises(FactoryError, match="already running"):
        factory.delete_project(pid)
    factory.delete_project(pid)
