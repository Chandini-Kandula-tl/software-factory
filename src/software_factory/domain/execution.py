"""Execution history records — the audit trail of what happened."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionKind(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    HUMAN_DECISION = "human_decision"
    CLARIFICATION = "clarification"
    WORKFLOW = "workflow"
    ERROR = "error"


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    INFO = "info"


class LLMCallRecord(BaseModel):
    """One model call made on behalf of an agent (including retries/fallbacks)."""

    provider: str
    model: str
    model_profile: str
    status: str
    attempt: int = 1
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_kind: str | None = None
    error: str | None = None


class ExecutionRecord(BaseModel):
    execution_id: str
    project_id: str
    kind: ExecutionKind
    stage: str
    status: ExecutionStatus
    started_at: str
    completed_at: str | None = None
    duration_ms: int | None = None
    agent: str | None = None
    agent_version: str | None = None
    model_profile: str | None = None
    provider: str | None = None
    model: str | None = None
    retry_count: int = 0
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    factory_version: str | None = None
    workflow_version: str | None = None
    config_fingerprint: str | None = None
    summary: str = ""
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
