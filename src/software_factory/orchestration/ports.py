"""Interfaces the workflow depends on (implemented by Factory Core).

The workflow never imports persistence or CLI code directly; it receives these
collaborators when the graph is built.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from software_factory.agents.catalog import FactoryAgents
from software_factory.config.settings import LimitsConfig, SecurityConfig
from software_factory.domain.execution import ExecutionRecord
from software_factory.domain.repository import RepositoryAnalysis


class WorkflowRecorder(Protocol):
    def persist_state(self, state: Mapping[str, Any]) -> None:
        """Mirror workflow state into state.json / progress.md / decisions.md / errors.md."""

    def write_stage_artifact(self, stage: str, output: BaseModel, state: Mapping[str, Any]) -> None:
        """Write the Markdown + JSON artifact for a stage output."""

    def record_execution(self, record: ExecutionRecord) -> None:
        """Append to execution-history.json and the log."""

    def write_tool_output(self, name: str, content: str) -> str:
        """Save a full tool log under artifacts/tool-runs/; return its relative path."""


@dataclass
class WorkflowDependencies:
    agents: FactoryAgents
    recorder: WorkflowRecorder
    limits: LimitsConfig
    security: SecurityConfig
    analyze_repository: Callable[[Path], RepositoryAnalysis]
    factory_version: str
    workflow_version: str
    # Where a greenfield project's source code is generated.
    source_root: Path
    # Facts about the build machine given to technology selection and planning.
    environment: dict[str, Any]
    # Hash of the effective configuration, stored on every history record.
    config_fingerprint: str | None = None
