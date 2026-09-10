"""IDs and timestamps: chunk 05 §3.4. The two primitives every repository writes with."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Final

from statlab.errors import PersistenceError

PREFIXES: Final = {
    "project": "p",
    "dataset": "ds",
    "contract": "ct",
    "run": "r",
    "session": "s",
    "message": "m",
    "node_execution": "ne",
    "tool_call": "tc",
    "model": "mo",
    "metric": "me",
    "gate": "g",
    "llm_call": "lc",
    "memory_entry": "mem",
}
"""Entity name -> id prefix. Greppable module constant, per §3.4.

Candidates and artifacts are excluded deliberately: artifacts are chunk 06's registry (`a_`,
already defined by chunk 02's `ARTIFACT_ID_PATTERN`), candidates are chunk 22's sequential,
human-readable `c_01` -- neither uses `new_id`.
"""


def new_id(prefix: str) -> str:
    """A collision-resistant id, `<prefix>_<16 lowercase hex>`.

    Same shape as chunk 02's `ARTIFACT_ID_PATTERN` (`^a_[0-9a-f]{16}$`) so every id in the
    system reads alike. 64 bits of `secrets.token_hex(8)` -- random, not sequential, because
    sequential ids would need a counter that two concurrent runs must coordinate on.
    """
    return f"{prefix}_{secrets.token_hex(8)}"


def utc_now() -> str:
    """Current time as an ISO-8601 UTC string, the storage format for every `*_at` column.

    SQLite has no datetime type. ISO-8601 text sorts chronologically as text, is readable in
    any DB browser, and round-trips through `datetime.fromisoformat` -- which the epoch-integer
    alternative does not do for the first two.
    """
    return datetime.now(UTC).isoformat()


def parse_ts(value: str) -> datetime:
    """Inverse of `utc_now`. Returns a timezone-aware datetime.

    Raises
    ------
    PersistenceError
        If `value` is not parseable, or parses to a naive datetime. A naive timestamp compared
        against an aware one raises `TypeError` at some distant call site; the useful place to
        fail is here, on read.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PersistenceError(f"not a valid ISO-8601 timestamp: {value!r}", cause=exc) from exc
    if parsed.tzinfo is None:
        raise PersistenceError(f"timestamp is not timezone-aware: {value!r}")
    return parsed
