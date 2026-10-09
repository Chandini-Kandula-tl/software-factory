"""Runs and resumes a project's LangGraph workflow.

A run executes nodes until the graph pauses at a human gate (interrupt), ends,
or the project is paused. Every node completion is checkpointed, so a killed
process resumes from the last completed node with ``factory continue``.
"""

from __future__ import annotations

import fcntl
import platform
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from software_factory import WORKFLOW_VERSION, __version__
from software_factory.agents.catalog import build_agents
from software_factory.agents.prompts import PromptLibrary
from software_factory.config.settings import FactoryConfig
from software_factory.core.project_recorder import ProjectRecorder
from software_factory.core.project_service import ProjectService
from software_factory.domain.errors import FactoryError
from software_factory.domain.lifecycle import LifecycleStatus
from software_factory.domain.project import Project
from software_factory.llm.interfaces import LLMClient
from software_factory.orchestration.graph import compile_workflow
from software_factory.orchestration.ports import WorkflowDependencies
from software_factory.persistence.checkpoints import open_checkpointer, thread_config
from software_factory.tools.repository_analysis import analyze_repository
from software_factory.tools.shell import find_program


@dataclass
class WorkflowSnapshot:
    started: bool
    values: dict[str, Any] = field(default_factory=dict)
    next_nodes: tuple[str, ...] = ()
    interrupt: dict[str, Any] | None = None

    @property
    def lifecycle_status(self) -> str:
        return self.values.get("lifecycle_status", LifecycleStatus.CREATED.value)

    @property
    def waiting_for(self) -> str | None:
        return self.interrupt.get("kind") if self.interrupt else None

    @property
    def interrupted_mid_run(self) -> bool:
        """Nodes remain to run but no human input is pending (e.g. process was killed)."""
        return bool(self.next_nodes) and self.interrupt is None


@dataclass
class RunOutcome:
    snapshot: WorkflowSnapshot
    stopped_by_pause: bool = False


class WorkflowService:
    def __init__(self, config: FactoryConfig, projects: ProjectService, llm: LLMClient) -> None:
        self._config = config
        self._projects = projects
        self._llm = llm
        self._prompts = PromptLibrary(config.prompts_path)

    @contextmanager
    def _workflow(self, project: Project) -> Iterator[CompiledStateGraph]:
        project_dir = self._projects.project_dir(project.project_id)
        deps = WorkflowDependencies(
            agents=build_agents(self._llm, self._prompts),
            recorder=ProjectRecorder(
                project, self._projects.store(project.project_id), self._projects.registry
            ),
            limits=self._config.limits,
            security=self._config.security,
            analyze_repository=analyze_repository,
            factory_version=__version__,
            workflow_version=WORKFLOW_VERSION,
            source_root=project_dir / "source",
            environment=self._environment(),
            config_fingerprint=self._config.fingerprint(),
        )
        with open_checkpointer(project_dir) as checkpointer:
            yield compile_workflow(deps, checkpointer)

    def _environment(self) -> dict[str, Any]:
        security = self._config.security
        allowed = security.allowed_commands
        installed = [c for c in allowed if find_program(c, security.isolate_project_environment)]
        return {
            "operating_system": f"{platform.system()} {platform.machine()}",
            "allowed_commands": allowed,
            "installed_commands": installed,
            "not_installed": [c for c in allowed if c not in installed],
            "note": "Project commands may only start with an installed, allowed command. "
            "Shell syntax (&&, |, cd) is not available.",
        }

    @contextmanager
    def run_lock(self, project_id: str) -> Iterator[None]:
        """Only one process may advance a project's workflow at a time."""
        lock_path: Path = self._projects.project_dir(project_id) / "checkpoints" / "run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise FactoryError(
                    f"Project '{project_id}' is already running in another process",
                    hint="Wait for it to finish, or `factory pause` it.",
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def snapshot(self, project: Project) -> WorkflowSnapshot:
        with self._workflow(project) as workflow:
            return _snapshot(workflow, project.project_id)

    def start(self, project: Project, requirements_input: str) -> RunOutcome:
        initial: dict[str, Any] = {
            "project_id": project.project_id,
            "project_name": project.name,
            "project_mode": project.mode.value,
            "repository_path": project.repository_path,
            "factory_version": __version__,
            "workflow_version": WORKFLOW_VERSION,
            "lifecycle_status": LifecycleStatus.CREATED.value,
            "completed_stages": [],
            "requirements_input": requirements_input,
            "clarification_round": 0,
            "question_counter": 0,
            "pending_questions": [],
            "clarifications": [],
            "approvals": {},
            "decision_log": [],
            "revision_feedback": {},
            "artifact_revisions": {},
            "failure": None,
            "errors": [],
            "warnings": [],
            "last_execution_id": None,
        }
        return self._execute(project, initial)

    def resume(self, project: Project, resume_value: Any | None = None) -> RunOutcome:
        payload: Command[Any] | None = (
            Command(resume=resume_value) if resume_value is not None else None
        )
        return self._execute(project, payload)

    def resume_after(self, project: Project, node: str) -> RunOutcome:
        """Re-open a graph that already ended, as if ``node`` had just completed.

        Used when a newer workflow adds stages after the one a project stopped
        at (e.g. projects that ended at READY_FOR_DEVELOPMENT before the build
        phase existed): the router then picks the new next stage.
        """
        with self._workflow(project) as workflow:
            workflow.update_state(thread_config(project.project_id), {}, as_node=node)
        return self._execute(project, None)

    def start_update(self, project: Project, values: dict[str, Any]) -> RunOutcome:
        """Re-open a released project for its next version.

        ``values`` resets the per-version state; applying it as the release
        node's output lets the router continue from the new lifecycle status.
        """
        with self.run_lock(project.project_id), self._workflow(project) as workflow:
            workflow.update_state(thread_config(project.project_id), values, as_node="release")
        return self._execute(project, None)

    def _execute(self, project: Project, graph_input: Any) -> RunOutcome:
        config = thread_config(project.project_id)
        with self.run_lock(project.project_id), self._workflow(project) as workflow:
            stopped = False
            for _chunk in workflow.stream(graph_input, config, stream_mode="updates"):
                if self._projects.registry.get(project.project_id).paused:
                    stopped = True
                    break
            return RunOutcome(_snapshot(workflow, project.project_id), stopped_by_pause=stopped)


def _snapshot(workflow: CompiledStateGraph, project_id: str) -> WorkflowSnapshot:
    state = workflow.get_state(thread_config(project_id))
    if not state.values and not state.next:
        return WorkflowSnapshot(started=False)
    interrupts = [i.value for task in state.tasks for i in task.interrupts]
    return WorkflowSnapshot(
        started=True,
        values=dict(state.values),
        next_nodes=tuple(state.next),
        interrupt=interrupts[0] if interrupts else None,
    )
