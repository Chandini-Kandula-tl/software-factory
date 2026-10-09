"""Structured JSON-lines logging with secret redaction.

Every record carries the MVP observability fields when available:
project_id, execution_id, stage, agent, provider, model, status, duration, error.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from software_factory.observability.redaction import redact

CONTEXT_FIELDS = (
    "project_id",
    "execution_id",
    "stage",
    "agent",
    "provider",
    "model",
    "status",
    "duration_ms",
    "error",
)


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact(str(value)) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


_configured_path: Path | None = None


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    """Send factory logs to ``<log_dir>/factory.log`` as JSON lines (idempotent)."""
    global _configured_path
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "factory.log"
    if _configured_path == log_path:
        return
    root = logging.getLogger("software_factory")
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonLinesFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False
    _configured_path = log_path
