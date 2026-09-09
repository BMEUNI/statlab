"""Tests for backend.statlab.domains.base: N1-N16 per plan/02_core_types.md §4.3."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import BaseModel, ValidationError
from statlab.domains.base import GatePolicy, NodeKind, NodeResult, NodeSpec, NodeStatus
from statlab_contracts.envelope import Failure


class _Decision(BaseModel):
    x: int


def _spec(**overrides: object) -> NodeSpec:
    defaults: dict[str, object] = {
        "name": "intake",
        "kind": "SHARED",
        "skippable": False,
        "decision_schema": _Decision,
        "tool_phases": ("intake",),
        "gate_policy": "always",
    }
    defaults.update(overrides)
    return NodeSpec(**defaults)  # type: ignore[arg-type]  # constructed from a dynamic dict of known-valid kwargs under test


# --- NodeSpec --------------------------------------------------------------------------


def test_node_spec_construct_preserves_values() -> None:
    """N1."""
    spec = _spec()
    assert spec.name == "intake"
    assert spec.kind == "SHARED"
    assert spec.skippable is False
    assert spec.decision_schema is _Decision
    assert spec.tool_phases == ("intake",)
    assert spec.gate_policy == "always"


def test_node_spec_frozen() -> None:
    """N2."""
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]  # deliberately mutating a frozen dataclass under test


def test_node_spec_slots_rejects_undeclared_attribute() -> None:
    """N3.

    On this toolchain (Python 3.14.3), a frozen+slots dataclass raises `TypeError` for an
    undeclared attribute, not `AttributeError` — the frozen dataclass's generated `__setattr__`
    always raises via `super().__setattr__(...)` regardless of whether the name is a declared
    slot; `__slots__` only changes how a *declared* name would have been stored. Pinning the
    observed behaviour rather than the spec table's illustrative exception type.
    """
    spec = _spec()
    with pytest.raises(TypeError):
        spec.bogus = 1  # type: ignore[attr-defined]  # deliberately setting an undeclared attribute under test


def test_node_spec_hashable() -> None:
    """N4."""
    spec = _spec()
    d = {spec: "value"}
    assert d[spec] == "value"


def test_node_spec_equality_by_value() -> None:
    """N5."""
    assert _spec() == _spec()


@pytest.mark.parametrize("kind", ["SHARED", "PER_CANDIDATE"])
def test_node_spec_both_kinds_accepted(kind: NodeKind) -> None:
    """N6."""
    spec = _spec(kind=kind)
    assert spec.kind == kind


@pytest.mark.parametrize("policy", ["always", "exception", "never"])
def test_node_spec_all_gate_policies_accepted(policy: GatePolicy) -> None:
    """N7."""
    spec = _spec(gate_policy=policy)
    assert spec.gate_policy == policy


# --- NodeResult validator matrix (§3.3) -------------------------------------------------

_FAILURE = Failure(error_class="bug", message="boom")


def _result(**overrides: object) -> NodeResult:
    defaults: dict[str, object] = {"status": "completed"}
    defaults.update(overrides)
    return NodeResult(**defaults)


_MATRIX: list[
    tuple[NodeStatus, str | None, Failure | None, tuple[str, ...], dict[str, float], bool]
] = [
    ("completed", None, None, (), {}, True),
    ("skipped", "nothing to engineer", None, (), {}, True),
    ("skipped", None, None, (), {}, False),
    ("skipped", "", None, (), {}, False),
    ("completed", "x", None, (), {}, False),
    ("failed", None, _FAILURE, (), {}, True),
    ("failed", None, None, (), {}, False),
    ("completed", None, _FAILURE, (), {}, False),
    ("failed", None, _FAILURE, ("a_0123456789abcdef",), {}, False),
    ("skipped", "x", None, (), {"auc": 0.9}, False),
    ("awaiting_user", None, None, (), {}, True),
]


@pytest.mark.parametrize(
    ("status", "skip_reason", "error", "artifacts", "metrics", "valid"), _MATRIX
)
def test_node_result_matrix(
    status: NodeStatus,
    skip_reason: str | None,
    error: Failure | None,
    artifacts: tuple[str, ...],
    metrics: dict[str, float],
    valid: bool,
) -> None:
    kwargs: dict[str, object] = {
        "status": status,
        "skip_reason": skip_reason,
        "error": error,
        "artifacts": artifacts,
        "metrics": metrics,
    }
    if valid:
        result = _result(**kwargs)
        assert result.status == status
    else:
        with pytest.raises(ValidationError):
            _result(**kwargs)


def test_node_result_minimal_completed_defaults_are_empty_containers() -> None:
    """N8."""
    result = _result()
    assert result.summary == {}
    assert result.artifacts == ()
    assert result.metrics == {}
    assert result.warnings == ()
    assert result.summary is not None
    assert result.artifacts is not None


def test_node_result_metrics_non_float_value_pinned() -> None:
    """N9: pin whichever pydantic actually does — coerce or reject.

    Verified: pydantic coerces a numeric string to float (pydantic's default lax numeric
    coercion), but rejects a genuinely non-numeric string.
    """
    result = _result(metrics={"auc": "0.9"})
    assert result.metrics == {"auc": 0.9}
    assert isinstance(result.metrics["auc"], float)

    with pytest.raises(ValidationError):
        _result(metrics={"auc": "not-a-number"})


def test_node_result_artifacts_list_coerced_to_tuple() -> None:
    """N10."""
    result = _result(artifacts=["a_0123456789abcdef"])
    assert isinstance(result.artifacts, tuple)
    assert result.artifacts == ("a_0123456789abcdef",)


def test_node_result_blank_skip_reason_rejected() -> None:
    """N11."""
    with pytest.raises(ValidationError):
        _result(status="skipped", skip_reason="")


def test_node_result_whitespace_only_skip_reason_rejected() -> None:
    """N12: reuse chunk 01's blank-token precedent."""
    with pytest.raises(ValidationError):
        _result(status="skipped", skip_reason="   ")


def test_node_result_mutation_rejected() -> None:
    """N13."""
    result = _result()
    with pytest.raises(ValidationError):
        result.status = "failed"  # type: ignore[misc]  # deliberately mutating a frozen model under test


def test_node_result_extra_field_rejected() -> None:
    """N14."""
    with pytest.raises(ValidationError):
        _result(bogus=1)


def test_node_result_json_round_trip_with_failure() -> None:
    """N15."""
    original = _result(status="failed", error=_FAILURE)
    restored = NodeResult.model_validate_json(original.model_dump_json())
    assert restored == original


@pytest.mark.parametrize(
    ("status", "companions"),
    [
        ("completed", {}),
        ("skipped", {"skip_reason": "nothing to engineer"}),
        ("failed", {"error": _FAILURE}),
        ("awaiting_user", {}),
    ],
)
def test_node_result_every_status_valid_with_companions(
    status: NodeStatus, companions: dict[str, object]
) -> None:
    """N16."""
    result = _result(status=status, **companions)
    assert result.status == status
