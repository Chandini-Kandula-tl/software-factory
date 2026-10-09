"""Factory Core facade — the only entry point the CLI (or a future API/UI) uses.

The CLI holds no workflow logic: every command maps to one method here.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from software_factory.config.settings import FactoryConfig
from software_factory.core.approval_service import ApprovalService
from software_factory.core.clarification_service import ClarificationService
from software_factory.core.project_recorder import pending_action
from software_factory.core.project_service import ProjectService
from software_factory.core.workflow_service import RunOutcome, WorkflowService, WorkflowSnapshot
from software_factory.domain.approvals import ApprovalStatus
from software_factory.domain.errors import WorkflowActionError
from software_factory.domain.execution import ExecutionKind, ExecutionRecord, ExecutionStatus
from software_factory.domain.lifecycle import LifecycleStatus, transition
from software_factory.domain.project import Project, ProjectSummary
from software_factory.domain.requirements import ClarificationQuestion
from software_factory.llm.interfaces import LLMClient
from software_factory.llm.registry import ModelRegistry
from software_factory.llm.router import ModelRouter
from software_factory.observability.clock import new_execution_id, utc_now
from software_factory.observability.logging import configure_logging
from software_factory.tools.git import GitTool

# Project documents shown in the UI, in reading order (also a whitelist).
DOCUMENTS = (
    "requirements.md",
    "clarification-questions.md",
    "repository-analysis.md",
    "impact-analysis.md",
    "architecture.md",
    "technology-stack.md",
    "implementation-plan.md",
    "change-request.md",
    "test-results.md",
    "test-analysis.md",
    "review.md",
    "security.md",
    "validation.md",
    "release-notes.md",
    "decisions.md",
    "progress.md",
    "errors.md",
    "project.md",
)
# Project-wide logs: copied (not moved) when a version is archived.
ONGOING_DOCUMENTS = ("decisions.md", "progress.md", "errors.md", "project.md")
# Build bookkeeping that carries over to the next version of a released app.
KEPT_DEVELOPMENT_KEYS = ("source_path", "setup_fingerprint", "tool_versions")
ARCHIVE_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}

TERMINAL = {
    LifecycleStatus.CANCELLED.value,
    LifecycleStatus.COMPLETED.value,
}


@dataclass
class ProjectStatus:
    project: Project
    lifecycle_status: str
    started: bool
    pending_action: str
    waiting_for: str | None
    interrupt: dict[str, Any] | None
    completed_stages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    development: dict[str, Any] = field(default_factory=dict)
    plan_checks: list[str] = field(default_factory=list)
    interrupted_mid_run: bool = False
    stopped_by_pause: bool = False
    note: str | None = None
    version: int = 1
    versions: list[dict[str, Any]] = field(default_factory=list)
    update_request: dict[str, Any] | None = None


class FactoryService:
    def __init__(self, config: FactoryConfig, llm: LLMClient | None = None) -> None:
        self.config = config
        configure_logging(config.workspace_path / "logs", config.logging.level)
        self.projects = ProjectService(config)
        self.llm = llm or ModelRouter(ModelRegistry(config), config.limits)
        self.workflows = WorkflowService(config, self.projects, self.llm)
        self.approvals = ApprovalService()
        self.clarifications = ClarificationService()

    # -- projects -------------------------------------------------------------

    def create_project(
        self,
        name: str,
        requirements: str | None = None,
        description: str = "",
        repository_path: str | None = None,
    ) -> Project:
        return self.projects.create(name, requirements, description, repository_path)

    def list_projects(self) -> list[ProjectSummary]:
        return self.projects.list()

    def set_requirements(self, project_id: str, requirements: str) -> None:
        snapshot = self._snapshot(project_id)
        if snapshot.started:
            raise WorkflowActionError(
                "Requirements can only be replaced before the workflow starts",
                hint="Reject the requirements at the approval gate with feedback instead.",
            )
        self.projects.set_requirements(project_id, requirements)

    # -- workflow -------------------------------------------------------------

    def start(self, project_id: str) -> ProjectStatus:
        if self._snapshot(project_id).started:
            raise WorkflowActionError(
                f"Project '{project_id}' has already started",
                hint="Use `factory continue` to resume it.",
            )
        requirements = self.projects.requirements_input(project_id)
        if not requirements or not requirements.strip():
            raise WorkflowActionError(
                "Project has no requirements yet",
                hint="Provide them with `factory requirements <project> --file <path>` "
                "or `--text`.",
            )
        self.projects.registry.set_paused(project_id, False)
        return self._status(project_id, self.workflows.start(self._fresh(project_id), requirements))

    def continue_(self, project_id: str) -> ProjectStatus:
        """Resume from wherever the project is: restart-safe and idempotent."""
        project = self.projects.load(project_id)
        self.projects.registry.set_paused(project_id, False)
        snapshot = self._snapshot(project_id)
        if not snapshot.started:
            return self.start(project_id)
        if snapshot.lifecycle_status in TERMINAL:
            return self._status(project_id, RunOutcome(snapshot), note="Nothing to continue.")
        if snapshot.interrupted_mid_run:
            return self._status(project_id, self.workflows.resume(self._fresh(project_id)))
        if (
            snapshot.lifecycle_status == LifecycleStatus.READY_FOR_DEVELOPMENT.value
            and not snapshot.next_nodes
        ):
            # Ended before the build phase existed: continue into development.
            return self._status(
                project_id, self.workflows.resume_after(self._fresh(project_id), "plan_approval")
            )
        external = self._check_external_changes(project_id, snapshot)
        if snapshot.waiting_for == "clarification":
            store = self.projects.store(project_id)
            payload = self.clarifications.resume_payload(snapshot, store)
            outcome = self.workflows.resume(project, payload)
            store.clear_answers(list(payload["answers"]))
            return self._status(project_id, outcome)
        if snapshot.waiting_for == "failure":
            note = "Workflow failed. Run `factory retry` after fixing."
        else:
            note = "Waiting for a human approval decision."
        return self._status(
            project_id, RunOutcome(snapshot), note=f"{external} {note}" if external else note
        )

    def _require_validated_code(self, snapshot: WorkflowSnapshot) -> None:
        """Only the exact commit that passed final validation may be released."""
        development = snapshot.values.get("development") or {}
        validated = (snapshot.values.get("validation") or {}).get("head_commit")
        source = development.get("source_path")
        if not source or not validated:
            return
        git = GitTool(Path(source), enabled=self.config.security.shell_enabled)
        allowed = set((snapshot.values.get("validation") or {}).get("test_side_effects") or [])
        if git.head() != validated or set(git.changed_paths()) - allowed:
            raise WorkflowActionError(
                "The code changed after final validation, so it cannot be released as is",
                hint='Reject the release (e.g. --reason "re-validate my manual changes"): the '
                "changes are then built, tested, reviewed and validated again.",
            )

    def _check_external_changes(self, project_id: str, snapshot: WorkflowSnapshot) -> str | None:
        """Spec §33: on resume, inspect the generated repository for outside changes.

        Changes are not blocked — they are recorded in history and reported, and
        they flow through the next build, test, review and security checks.
        """
        development = snapshot.values.get("development") or {}
        source = development.get("source_path")
        if not source or not Path(source).is_dir():
            return None
        git_state = GitTool(Path(source), enabled=self.config.security.shell_enabled).state()
        findings = []
        expected = development.get("last_commit")
        if expected and git_state.commit and git_state.commit != expected:
            findings.append(f"HEAD moved from {expected[:10]} to {git_state.commit[:10]}")
        allowed = set((snapshot.values.get("validation") or {}).get("test_side_effects") or [])
        outside = [p for p in GitTool(Path(source)).changed_paths() if p not in allowed]
        if outside:
            findings.append(f"{len(outside)} uncommitted file(s)")
        if not findings:
            return None
        message = (
            f"External changes detected in {source}: {'; '.join(findings)}. They will be "
            "built, tested and reviewed with the next step."
        )
        self.projects.store(project_id).append_history(
            ExecutionRecord(
                execution_id=new_execution_id(),
                project_id=project_id,
                kind=ExecutionKind.WORKFLOW,
                stage="resume",
                status=ExecutionStatus.INFO,
                started_at=utc_now(),
                config_fingerprint=self.config.fingerprint(),
                summary=message,
                details={"git": git_state.model_dump()},
            )
        )
        return message

    def update(self, project_id: str, change: str) -> ProjectStatus:
        """Start the next version of a released app from a description of the change.

        The same project continues on the same code: requirements, architecture,
        technology and plan are revised for the change (each approved again),
        then only the new tasks are built, tested, reviewed and released under
        the next ``release-N`` tag. The finished version's documents are kept
        in ``versions/v<N>/``.
        """
        self.projects.load(project_id)
        change = change.strip()
        if not change:
            raise WorkflowActionError("Describe the change you want")
        snapshot = self._snapshot(project_id)
        if snapshot.lifecycle_status != LifecycleStatus.COMPLETED.value:
            raise WorkflowActionError(
                f"Only a completed project can be updated (status: {snapshot.lifecycle_status})",
                hint="Finish or retry the current work first.",
            )
        values = snapshot.values
        development = dict(values.get("development") or {})
        source = development.get("source_path")
        if not source or not Path(source).is_dir():
            raise WorkflowActionError(
                f"The app's code folder no longer exists: {source}",
                hint="An update changes the released code, so that folder is needed.",
            )
        external = self._check_external_changes(project_id, snapshot)
        head = GitTool(Path(source), enabled=self.config.security.shell_enabled).head()
        release = values.get("release") or {}
        version = int(values.get("version") or 1)
        versions = [
            *(values.get("versions") or []),
            {
                "version": version,
                "tag": release.get("tag"),
                "commit": release.get("commit"),
                "released_at": release.get("released_at"),
                "change": (values.get("update_request") or {}).get("change"),
            },
        ]
        self.projects.store(project_id).archive_version(version, DOCUMENTS, ONGOING_DOCUMENTS)
        kept = {k: development[k] for k in KEPT_DEVELOPMENT_KEYS if k in development}
        update_values: dict[str, Any] = {
            "lifecycle_status": transition(
                snapshot.lifecycle_status, LifecycleStatus.REQUIREMENTS_ANALYSIS
            ).value,
            "version": version + 1,
            "versions": versions,
            "update_request": {
                "version": version + 1,
                "change": change,
                "previous_release": release.get("tag"),
                "requested_at": utc_now(),
            },
            "requirements_input": f"{values.get('requirements_input', '').rstrip()}\n\n"
            f"## Change requested for version {version + 1}\n\n{change}\n",
            "completed_stages": [],
            "approvals": {},
            "revision_feedback": {},
            "pending_questions": [],
            "clarification_round": 0,
            "repository": None,
            "impact_analysis": None,
            "plan_checks": [],
            "development": {**kept, "base_commit": head, "last_commit": head},
            "last_verification": None,
            "test_analysis": None,
            "change_request": None,
            "review": None,
            "security": None,
            "validation": None,
            "release": None,
            "failure": None,
        }
        self.projects.registry.set_paused(project_id, False)
        outcome = self.workflows.start_update(self._fresh(project_id), update_values)
        return self._status(project_id, outcome, note=external)

    def approve(
        self, project_id: str, stage: str | None = None, comment: str = ""
    ) -> ProjectStatus:
        return self._decide(project_id, ApprovalStatus.APPROVED, comment, stage)

    def reject(self, project_id: str, reason: str, stage: str | None = None) -> ProjectStatus:
        return self._decide(project_id, ApprovalStatus.REJECTED, reason, stage)

    def _decide(
        self, project_id: str, status: ApprovalStatus, reason: str, stage: str | None
    ) -> ProjectStatus:
        self.projects.load(project_id)
        snapshot = self._snapshot(project_id)
        payload = self.approvals.build_decision(snapshot, status, reason, stage)
        if (
            status is ApprovalStatus.APPROVED
            and (snapshot.interrupt or {}).get("stage") == "release"
        ):
            self._require_validated_code(snapshot)
        self.projects.registry.set_paused(project_id, False)
        return self._status(project_id, self.workflows.resume(self._fresh(project_id), payload))

    def retry(self, project_id: str, hint: str = "") -> ProjectStatus:
        self.projects.load(project_id)
        snapshot = self._snapshot(project_id)
        self.projects.registry.set_paused(project_id, False)
        if snapshot.waiting_for == "failure":
            payload = {"action": "retry", "hint": hint.strip()}
            return self._status(project_id, self.workflows.resume(self._fresh(project_id), payload))
        if hint.strip():
            raise WorkflowActionError(
                "A hint can only be given when retrying a failed project",
                hint="Use `factory reject --reason` to guide an agent at an approval gate.",
            )
        if snapshot.interrupted_mid_run:
            return self._status(project_id, self.workflows.resume(self._fresh(project_id)))
        raise WorkflowActionError(
            f"Nothing to retry (status: {snapshot.lifecycle_status})",
            hint="`factory retry` applies after a failed stage or an interrupted run.",
        )

    def abort(self, project_id: str) -> ProjectStatus:
        self.projects.load(project_id)
        snapshot = self._snapshot(project_id)
        if snapshot.waiting_for != "failure":
            raise WorkflowActionError("Only a failed project can be cancelled in this version")
        self.projects.registry.set_paused(project_id, False)
        return self._status(
            project_id, self.workflows.resume(self._fresh(project_id), {"action": "abort"})
        )

    def pause(self, project_id: str) -> ProjectStatus:
        self.projects.load(project_id)
        self.projects.registry.set_paused(project_id, True)
        return self.status(
            project_id,
            note="Paused. A run in progress stops after its current step; "
            "`factory continue` resumes.",
        )

    # -- inspection -----------------------------------------------------------

    def status(self, project_id: str, note: str | None = None) -> ProjectStatus:
        self.projects.load(project_id)
        return self._status(project_id, RunOutcome(self._snapshot(project_id)), note=note)

    def questions(self, project_id: str) -> tuple[list[ClarificationQuestion], dict[str, Any]]:
        self.projects.load(project_id)
        snapshot = self._snapshot(project_id)
        return (
            self.clarifications.pending_questions(snapshot),
            self.projects.store(project_id).read_answers(),
        )

    def answer(self, project_id: str, question_id: str, answer: str) -> list[str]:
        self.projects.load(project_id)
        snapshot = self._snapshot(project_id)
        return self.clarifications.answer(
            snapshot, self.projects.store(project_id), question_id, answer
        )

    def history(self, project_id: str) -> list[dict[str, Any]]:
        self.projects.load(project_id)
        return self.projects.store(project_id).read_history()

    # -- documents & deliverables (used by the web UI) ------------------------

    def documents(self, project_id: str, version: int | None = None) -> list[str]:
        """Project Markdown documents that exist, in reading order.

        ``version`` lists an earlier, archived version's documents instead.
        """
        store = self.projects.store(self.projects.load(project_id).project_id)
        folder = store.version_dir(version) if version else store.project_dir
        return [name for name in DOCUMENTS if (folder / name).is_file()]

    def read_document(self, project_id: str, name: str, version: int | None = None) -> str:
        if name not in DOCUMENTS:  # whitelist: never serve arbitrary paths
            raise WorkflowActionError(f"Unknown document '{name}'")
        self.projects.load(project_id)
        store = self.projects.store(project_id)
        text = store.read_version_text(version, name) if version else store.read_text(name)
        if text is None:
            raise WorkflowActionError(f"{name} does not exist yet")
        return text

    def source_archive(self, project_id: str) -> tuple[str, bytes]:
        """The generated code as a zip (without .git and dependency folders)."""
        status = self.status(project_id)
        source = status.development.get("source_path")
        if not source or not Path(source).is_dir():
            raise WorkflowActionError("This project has no generated code yet")
        root = Path(source)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if path.is_file() and not set(relative.parts) & ARCHIVE_SKIP:
                    archive.write(path, Path(project_id) / relative)
        return f"{project_id}.zip", buffer.getvalue()

    # -- helpers ----------------------------------------------------------------

    def _fresh(self, project_id: str) -> Project:
        return self.projects.load(project_id)

    def _snapshot(self, project_id: str) -> WorkflowSnapshot:
        project = self.projects.load(project_id)
        self.projects.require_folder(project_id)
        return self.workflows.snapshot(project)

    def delete_project(self, project_id: str) -> None:
        """Delete a project (register entry + its workspace folder); refused while running."""
        self.projects.load(project_id)
        if self.projects.folder_missing(project_id):
            self.projects.delete(project_id)
            return
        with self.workflows.run_lock(project_id):
            self.projects.delete(project_id)

    def _status(
        self, project_id: str, outcome: RunOutcome, note: str | None = None
    ) -> ProjectStatus:
        snapshot = outcome.snapshot
        values = snapshot.values
        project = self.projects.load(project_id)
        status = snapshot.lifecycle_status
        action = pending_action(status)
        if snapshot.interrupted_mid_run:
            action = "Run was interrupted — `factory continue` resumes from the last checkpoint"
        if project.paused:
            action = f"Paused — `factory continue` to resume. Then: {action}"
        return ProjectStatus(
            project=project,
            lifecycle_status=status,
            started=snapshot.started,
            pending_action=action,
            waiting_for=snapshot.waiting_for,
            interrupt=snapshot.interrupt,
            completed_stages=list(values.get("completed_stages") or []),
            warnings=list(values.get("warnings") or []),
            failure=values.get("failure"),
            development=dict(values.get("development") or {}),
            plan_checks=list(values.get("plan_checks") or []),
            interrupted_mid_run=snapshot.interrupted_mid_run,
            stopped_by_pause=outcome.stopped_by_pause,
            note=note,
            version=int(values.get("version") or 1),
            versions=list(values.get("versions") or []),
            update_request=values.get("update_request"),
        )
