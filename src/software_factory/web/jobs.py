"""Background execution of long factory actions for the web UI.

Workflow steps can take minutes (a local model writing code), so the web page
never waits for them: the action runs in a worker thread and the page polls
the project status. Only one action per project runs at a time; the factory's
own run lock also guards against a second process.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from software_factory.domain.errors import FactoryError
from software_factory.observability.clock import utc_now


@dataclass
class Job:
    action: str
    started_at: str
    future: Future[Any] | None = None
    error: str | None = None
    hint: str | None = None
    finished_at: str | None = None


@dataclass
class JobRunner:
    """Runs one background action per project. ``synchronous`` is for tests."""

    synchronous: bool = False
    max_workers: int = 4
    _jobs: dict[str, Job] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pool: ThreadPoolExecutor | None = None

    def is_running(self, project_id: str) -> bool:
        job = self._jobs.get(project_id)
        return bool(job and job.finished_at is None)

    def last(self, project_id: str) -> Job | None:
        return self._jobs.get(project_id)

    def clear_error(self, project_id: str) -> None:
        job = self._jobs.get(project_id)
        if job and job.finished_at:
            job.error = job.hint = None

    def submit(self, project_id: str, action: str, work: Callable[[], Any]) -> bool:
        """Start ``work`` unless an action is already running for this project."""
        with self._lock:
            if self.is_running(project_id):
                return False
            job = Job(action=action, started_at=utc_now())
            self._jobs[project_id] = job
        if self.synchronous:
            self._execute(job, work)
            return True
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="factory"
            )
        job.future = self._pool.submit(self._execute, job, work)
        return True

    @staticmethod
    def _execute(job: Job, work: Callable[[], Any]) -> None:
        try:
            work()
        except FactoryError as error:
            job.error, job.hint = str(error), error.hint
        except Exception as error:  # noqa: BLE001 - surfaced to the user, never swallowed silently
            job.error = f"Unexpected error: {error!r}"
        finally:
            job.finished_at = utc_now()
