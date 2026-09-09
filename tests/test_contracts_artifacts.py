"""Tests for statlab_contracts.artifacts: A1-A23 per plan/02_core_types.md §4.1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, parse_artifact_ref

VALID_REF = "a_0123456789abcdef"


# --- parse_artifact_ref -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["a_0123456789abcdef", "a_ffffffffffffffff"],
)
def test_parse_artifact_ref_valid(value: str) -> None:
    """A1, A2."""
    assert parse_artifact_ref(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "a_0123456789ABCDEF",  # A3: uppercase
        "a_0123456789abcde",  # A4: 15 chars
        "a_0123456789abcdef0",  # A5: 17 chars
        "b_0123456789abcdef",  # A6: wrong prefix
        "a0123456789abcdef",  # A7: no underscore
        "",  # A8: empty
        "a_",  # A9: just prefix
        "a_0123456789abcdeg",  # A10: non-hex char
        " a_0123456789abcdef",  # A11: leading space
        "a_0123456789abcdef\n",  # A12: trailing newline
    ],
)
def test_parse_artifact_ref_invalid(value: str) -> None:
    """A3-A12."""
    with pytest.raises(ValueError, match="not a valid artifact ref"):
        parse_artifact_ref(value)


# --- ArtifactMeta ------------------------------------------------------------------------


def test_artifact_meta_minimal_valid() -> None:
    """A13."""
    meta = ArtifactMeta(ref=VALID_REF, kind="dataframe")
    assert meta.label is None
    assert meta.rows is None
    assert meta.cols is None


def test_artifact_meta_all_fields() -> None:
    """A14."""
    meta = ArtifactMeta(
        ref=VALID_REF,
        kind="model",
        label="fitted random forest",
        rows=100,
        cols=5,
    )
    assert meta.ref == VALID_REF
    assert meta.kind == "model"
    assert meta.label == "fitted random forest"
    assert meta.rows == 100
    assert meta.cols == 5


_ALL_KINDS: tuple[ArtifactKind, ...] = (
    "dataframe",
    "predictions",
    "transformer",
    "model",
    "inferential_fit",
    "curve",
    "table",
    "plot",
    "report",
)


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_artifact_meta_every_kind_constructs(kind: ArtifactKind) -> None:
    """A15."""
    meta = ArtifactMeta(ref=VALID_REF, kind=kind)
    assert meta.kind == kind


def test_artifact_meta_unknown_kind_rejected() -> None:
    """A16."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=VALID_REF, kind="spreadsheet")


def test_artifact_meta_negative_rows_rejected() -> None:
    """A17."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=VALID_REF, kind="dataframe", rows=-1)


def test_artifact_meta_negative_cols_rejected() -> None:
    """A18."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=VALID_REF, kind="dataframe", cols=-1)


def test_artifact_meta_zero_rows_valid() -> None:
    """A19: an empty frame is a real, reportable outcome."""
    meta = ArtifactMeta(ref=VALID_REF, kind="dataframe", rows=0)
    assert meta.rows == 0


def test_artifact_meta_label_too_long_rejected() -> None:
    """A20."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=VALID_REF, kind="dataframe", label="x" * 121)


def test_artifact_meta_extra_field_rejected() -> None:
    """A21."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=VALID_REF, kind="dataframe", bogus=1)  # type: ignore[call-arg]  # deliberately invalid extra field under test


def test_artifact_meta_mutation_rejected() -> None:
    """A22."""
    meta = ArtifactMeta(ref=VALID_REF, kind="dataframe")
    with pytest.raises(ValidationError):
        meta.rows = 5  # type: ignore[misc]  # deliberately mutating a frozen model under test


def test_artifact_meta_json_round_trip() -> None:
    """A23."""
    meta = ArtifactMeta(
        ref=VALID_REF,
        kind="table",
        label="coefficients",
        rows=12,
        cols=4,
    )
    restored = ArtifactMeta.model_validate_json(meta.model_dump_json())
    assert restored == meta
