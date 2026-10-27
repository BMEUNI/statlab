"""Explicit append-only project facts; initialized contexts read a single snapshot."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Final

from pydantic import JsonValue

from statlab.persistence.models import MemoryRow
from statlab.persistence.repository import create_memory, list_memory

RECALLED_RUNS: Final = 3


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


def recall_memory(
    rows: Sequence[MemoryRow], *, dataset_sha256: str, target: str
) -> tuple[MemoryRow, ...]:
    """Every project note plus the few most relevant earlier run outcomes (chunk 88):
    same dataset scores 2, same target 1, otherwise 0 and left out; newest first among equals."""

    def score(row: MemoryRow) -> int:
        value = row.value()
        if not isinstance(value, dict):
            return 0
        if value.get("dataset_sha256") == dataset_sha256:
            return 2
        if value.get("target") == target:
            return 1
        return 0

    ranked = sorted(
        (row for row in rows if row.key == "run_outcome" and score(row) > 0),
        key=lambda row: (score(row), row.ts, row.id),
        reverse=True,
    )[:RECALLED_RUNS]
    chosen = {row.id for row in ranked}
    return tuple(row for row in rows if row.key != "run_outcome" or row.id in chosen)
