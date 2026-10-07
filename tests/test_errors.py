"""Tests for backend.statlab.errors: E1-E8 per plan/01_foundation.md §4.2."""

from __future__ import annotations

import pytest
from statlab.errors import ConfigError, StatlabError, traceback_frames


def test_str_without_cause() -> None:
    """E1."""
    assert str(StatlabError("boom")) == "boom"


def test_str_with_cause() -> None:
    """E2."""
    exc = StatlabError("boom", cause=ValueError("x"))
    assert str(exc) == "boom (caused by ValueError: x)"


def test_attributes_stored() -> None:
    """E3."""
    original = ValueError("x")
    exc = StatlabError("boom", cause=original)
    assert exc.message == "boom"
    assert exc.cause is original


def test_config_error_default_missing() -> None:
    """E4."""
    assert ConfigError("bad").missing == ()


def test_config_error_explicit_missing() -> None:
    """E5."""
    assert ConfigError("bad", missing=("A", "B")).missing == ("A", "B")


def test_subclass_relation() -> None:
    """E6."""
    assert issubclass(ConfigError, StatlabError)


def test_raise_from_preserves_chain() -> None:
    """E7."""
    original = ValueError("x")
    try:
        try:
            raise original
        except ValueError as exc:
            raise StatlabError("wrapped", cause=exc) from exc
    except StatlabError as wrapped:
        assert wrapped.__cause__ is original


def test_catchable_as_exception() -> None:
    """E8."""
    with pytest.raises(Exception):  # noqa: B017 - deliberately asserting broad catchability
        raise StatlabError("boom")


def test_traceback_frames_lists_locations_without_message_or_source() -> None:
    def explode() -> None:
        raise ValueError("hidden message")

    try:
        explode()
    except ValueError as exc:
        frames = traceback_frames(exc)
    assert "in explode" in frames
    assert "test_errors.py:" in frames
    assert "hidden message" not in frames
