"""Factory-level error hierarchy.

Every error the factory raises on purpose derives from ``FactoryError`` so the
CLI can print an actionable message instead of a traceback.
"""

from __future__ import annotations


class FactoryError(Exception):
    """Base class for controlled factory errors."""

    hint: str | None = None

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        if hint is not None:
            self.hint = hint


class ConfigurationError(FactoryError):
    """Configuration file is missing, invalid, or references unknown entries."""


class ProjectNotFoundError(FactoryError):
    """No project exists with the given id."""


class ProjectAlreadyExistsError(FactoryError):
    """A project with the same id already exists."""


class ProjectFolderMissingError(FactoryError):
    """The project is registered but its folder was removed outside the factory."""


class InvalidProjectIdError(FactoryError):
    """Project id contains characters that are not allowed."""


class InvalidTransitionError(FactoryError):
    """A lifecycle transition was attempted that the state machine forbids."""


class QualityGateError(FactoryError):
    """A stage entry/exit criterion is not satisfied."""


class WorkflowActionError(FactoryError):
    """The requested action does not match what the workflow is waiting for."""


class AgentOutputError(FactoryError):
    """An agent produced output that failed validation."""
