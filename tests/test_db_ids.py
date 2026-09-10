"""Chunk 05 §4.4: `new_id`, `utc_now`, `parse_ts`."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from statlab.errors import PersistenceError
from statlab.persistence.ids import PREFIXES, new_id, parse_ts, utc_now
from statlab_contracts.artifacts import ARTIFACT_ID_PATTERN

# --- new_id ----------------------------------------------------------------------------------

_ID_FORMAT_RE = re.compile(r"^[a-z]+_[0-9a-f]{16}\Z")


def test_new_id_matches_prefix_hex_shape() -> None:
    value = new_id("p")
    assert _ID_FORMAT_RE.match(value)


@pytest.mark.parametrize("prefix", list(PREFIXES.values()))
def test_new_id_preserves_every_declared_prefix(prefix: str) -> None:
    value = new_id(prefix)
    assert value.startswith(f"{prefix}_")


def test_new_id_hex_part_is_16_chars() -> None:
    value = new_id("p")
    _, _, hex_part = value.partition("_")
    assert len(hex_part) == 16


def test_new_id_hex_part_is_lowercase() -> None:
    value = new_id("p")
    _, _, hex_part = value.partition("_")
    assert hex_part == hex_part.lower()


def test_new_id_hex_part_is_valid_hex() -> None:
    value = new_id("p")
    _, _, hex_part = value.partition("_")
    int(hex_part, 16)  # raises ValueError if not valid hex


def test_new_id_uniqueness_across_10000_calls() -> None:
    values = {new_id("p") for _ in range(10_000)}
    assert len(values) == 10_000


def test_new_id_shape_matches_artifact_id_pattern_when_prefix_is_a() -> None:
    """Same shape as chunk 02's `ARTIFACT_ID_PATTERN` so every id in the system reads alike."""
    value = new_id("a")
    assert ARTIFACT_ID_PATTERN.match(value)


def test_prefixes_excludes_candidate_and_artifact() -> None:
    """Candidates and artifacts are excluded deliberately (§3.4): artifacts are chunk 06's
    registry (`a_`), candidates are chunk 22's sequential `c_01`."""
    assert "candidate" not in PREFIXES
    assert "artifact" not in PREFIXES


def test_prefixes_are_all_distinct() -> None:
    assert len(set(PREFIXES.values())) == len(PREFIXES)


# --- utc_now -----------------------------------------------------------------------------------


def test_utc_now_is_aware() -> None:
    parsed = datetime.fromisoformat(utc_now())
    assert parsed.tzinfo is not None


def test_utc_now_is_utc() -> None:
    parsed = datetime.fromisoformat(utc_now())
    assert parsed.utcoffset() == timedelta(0)


def test_utc_now_round_trips_through_parse_ts() -> None:
    text = utc_now()
    parsed = parse_ts(text)
    assert parsed == datetime.fromisoformat(text)


# --- parse_ts ----------------------------------------------------------------------------------


def test_parse_ts_inverts_utc_now() -> None:
    text = utc_now()
    result = parse_ts(text)
    assert result.tzinfo is not None


def test_parse_ts_text_ordering_matches_chronological_ordering() -> None:
    """Timestamps spanning a year: ISO-8601 text sorts the same way the parsed datetimes do."""
    base = datetime(2025, 1, 1, tzinfo=UTC)
    texts = [(base + timedelta(days=30 * i)).isoformat() for i in range(13)]
    assert sorted(texts) == texts
    parsed = [parse_ts(t) for t in texts]
    assert sorted(parsed) == parsed


def test_parse_ts_rejects_naive_text() -> None:
    naive = datetime(2025, 1, 1).isoformat()
    with pytest.raises(PersistenceError, match="not timezone-aware"):
        parse_ts(naive)


def test_parse_ts_rejects_empty_text() -> None:
    with pytest.raises(PersistenceError, match="not a valid ISO-8601"):
        parse_ts("")


def test_parse_ts_rejects_non_timestamp_text() -> None:
    with pytest.raises(PersistenceError, match="not a valid ISO-8601"):
        parse_ts("definitely not a timestamp")
