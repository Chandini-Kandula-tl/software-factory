"""Per-project LangGraph checkpoint storage.

Each project has its own SQLite checkpoint file under
``projects/<id>/checkpoints/``, and the LangGraph thread id is the project id.
One project can therefore never load another project's workflow state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

CHECKPOINT_FILENAME = "workflow-checkpoints.sqlite"


def checkpoint_path(project_dir: Path) -> Path:
    return project_dir / "checkpoints" / CHECKPOINT_FILENAME


@contextmanager
def open_checkpointer(project_dir: Path) -> Iterator[SqliteSaver]:
    path = checkpoint_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


def thread_config(project_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": project_id}}
