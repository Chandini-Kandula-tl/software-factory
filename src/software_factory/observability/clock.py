"""Time and id helpers (single place so tests and records agree on formats)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_execution_id() -> str:
    return f"exec-{uuid.uuid4().hex[:12]}"
