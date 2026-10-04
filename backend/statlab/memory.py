"""Explicit append-only project facts; initialized contexts read a single snapshot."""

from __future__ import annotations

import sqlite3

from pydantic import JsonValue

from statlab.persistence.models import MemoryRow
from statlab.persistence.repository import create_memory, list_memory


def remember(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    key: str,
    value: JsonValue,
    source_run_id: str | None = None,
) -> MemoryRow:
    return create_memory(
        conn, project_id=project_id, key=key, value=value, source_run_id=source_run_id
    )


def read_memory(conn: sqlite3.Connection, project_id: str) -> tuple[MemoryRow, ...]:
    return list_memory(conn, project_id)
