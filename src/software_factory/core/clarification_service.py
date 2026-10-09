"""Persists clarification answers and builds the clarification resume payload.

Answers are saved to the project (``clarification-answers.json``) as soon as
they are given, so they survive restarts before the workflow is continued.
"""

from __future__ import annotations

from typing import Any

from software_factory.core.workflow_service import WorkflowSnapshot
from software_factory.domain.errors import WorkflowActionError
from software_factory.domain.requirements import ClarificationQuestion
from software_factory.observability.clock import utc_now
from software_factory.persistence.artifacts import ArtifactStore


class ClarificationService:
    def pending_questions(self, snapshot: WorkflowSnapshot) -> list[ClarificationQuestion]:
        if snapshot.waiting_for != "clarification":
            return []
        assert snapshot.interrupt is not None
        return [ClarificationQuestion.model_validate(q) for q in snapshot.interrupt["questions"]]

    def answer(
        self, snapshot: WorkflowSnapshot, store: ArtifactStore, question_id: str, answer: str
    ) -> list[str]:
        """Save one answer; return the ids of required questions still unanswered."""
        questions = self.pending_questions(snapshot)
        if not questions:
            raise WorkflowActionError(
                f"Project is not waiting for clarification (status: {snapshot.lifecycle_status})"
            )
        ids = {q.id for q in questions}
        normalized = question_id.strip().upper()
        if normalized not in ids:
            raise WorkflowActionError(
                f"Unknown question id '{question_id}'",
                hint=f"Pending questions: {', '.join(sorted(ids))}",
            )
        if not answer.strip():
            raise WorkflowActionError("Answer is empty")
        store.save_answer(normalized, answer.strip(), utc_now())
        return self.unanswered_required(snapshot, store)

    def unanswered_required(self, snapshot: WorkflowSnapshot, store: ArtifactStore) -> list[str]:
        answers = store.read_answers()
        return [
            q.id
            for q in self.pending_questions(snapshot)
            if q.required and not (answers.get(q.id) or {}).get("answer")
        ]

    def resume_payload(self, snapshot: WorkflowSnapshot, store: ArtifactStore) -> dict[str, Any]:
        missing = self.unanswered_required(snapshot, store)
        if missing:
            raise WorkflowActionError(
                f"Required questions are unanswered: {', '.join(missing)}",
                hint='Answer with `factory answer <project> <id> "<answer>"`.',
            )
        ids = {q.id for q in self.pending_questions(snapshot)}
        answers = {qid: value for qid, value in store.read_answers().items() if qid in ids}
        return {"answers": answers}
