"""Tests for statlab_contracts.artifacts: A1-A23 per plan/02_core_types.md §4.1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, ArtifactRef, parse_artifact_ref

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


def test_artifact_meta_minimal_valid(ref: ArtifactRef) -> None:
    """A13."""
    meta = ArtifactMeta(ref=ref, kind="dataframe")
    assert meta.label is None
    assert meta.rows is None
    assert meta.cols is None


def test_artifact_meta_all_fields(ref: ArtifactRef) -> None:
    """A14."""
    meta = ArtifactMeta(
        ref=ref,
        kind="model",
        label="fitted random forest",
        rows=100,
        cols=5,
    )
    assert meta.ref == ref
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
def test_artifact_meta_every_kind_constructs(ref: ArtifactRef, kind: ArtifactKind) -> None:
    """A15."""
    meta = ArtifactMeta(ref=ref, kind=kind)
    assert meta.kind == kind


def test_artifact_meta_unknown_kind_rejected(ref: ArtifactRef) -> None:
    """A16: a deliberately invalid Literal — model_validate, not a typed kwarg, since
    `init_typed = true` makes mypy correctly reject `kind="spreadsheet"` as a constructor
    argument. `model_validate` takes untyped input by design, matching what actually happens
    when this value arrives over the wire.
    """
    with pytest.raises(ValidationError):
        ArtifactMeta.model_validate({"ref": ref, "kind": "spreadsheet"})


def test_artifact_meta_negative_rows_rejected(ref: ArtifactRef) -> None:
    """A17."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=ref, kind="dataframe", rows=-1)


def test_artifact_meta_negative_cols_rejected(ref: ArtifactRef) -> None:
    """A18."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=ref, kind="dataframe", cols=-1)


def test_artifact_meta_zero_rows_valid(ref: ArtifactRef) -> None:
    """A19: an empty frame is a real, reportable outcome."""
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=0)
    assert meta.rows == 0


def test_artifact_meta_label_too_long_rejected(ref: ArtifactRef) -> None:
    """A20."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=ref, kind="dataframe", label="x" * 121)


def test_artifact_meta_extra_field_rejected(ref: ArtifactRef) -> None:
    """A21."""
    with pytest.raises(ValidationError):
        ArtifactMeta(ref=ref, kind="dataframe", bogus=1)  # type: ignore[call-arg]  # deliberately invalid extra kwarg under test


def test_artifact_meta_mutation_rejected(ref: ArtifactRef) -> None:
    """A22."""
    meta = ArtifactMeta(ref=ref, kind="dataframe")
    with pytest.raises(ValidationError):
        meta.rows = 5  # type: ignore[misc]  # deliberately mutating a frozen model under test


def test_artifact_meta_json_round_trip(ref: ArtifactRef) -> None:
    """A23."""
    meta = ArtifactMeta(
        ref=ref,
        kind="table",
        label="coefficients",
        rows=12,
        cols=4,
    )
    restored = ArtifactMeta.model_validate_json(meta.model_dump_json())
    assert restored == meta
