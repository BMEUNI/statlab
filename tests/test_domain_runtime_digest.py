"""Prompt digest of shared boundaries: pure, prompt context only."""

from __future__ import annotations

from statlab.domains.base import NodeResult
from statlab.domains.runtime import boundary_digest
from statlab.graph.state import BoundaryResult
from statlab_contracts.envelope import Failure


def _b(node: str, result: NodeResult) -> BoundaryResult:
    return BoundaryResult(node_name=node, execution_id=f"ne_{node}", result=result)


def test_last_entry_per_node_keeps_first_position() -> None:
    boundaries = [
        _b("intake", NodeResult(status="completed", summary={"a": 1})),
        _b("experiment_design", NodeResult(status="completed", summary={"x": 1})),
        _b("inferential_analysis", NodeResult(status="skipped", skip_reason="No goals")),
        _b("experiment_design", NodeResult(status="completed", summary={"x": 2})),
    ]
    assert boundary_digest(boundaries, keep_tools=True) == [
        {"node": "intake", "status": "completed", "summary": {"a": 1}},
        {"node": "experiment_design", "status": "completed", "summary": {"x": 2}},
        {
            "node": "inferential_analysis",
            "status": "skipped",
            "summary": {},
            "skip_reason": "No goals",
        },
    ]


def test_prunes_bulky_keys_and_shortens_rationales() -> None:
    boundary = _b(
        "champion_selection",
        NodeResult(
            status="completed",
            summary={
                "tools": [{"tool": "t"}],
                "registry": {"r": 1},
                "candidate_set": {"c": 1},
                "decision": {
                    "rationale": "One. Two! Three? Four.",
                    "candidate_set": {"c": 2},
                    "sections": [1],
                },
                "items": [{"rationale": "X. Y. Z.", "tools": 1}],
                "findings": ["kept"],
            },
        ),
    )
    lean = boundary_digest([boundary], keep_tools=False)[0]
    assert isinstance(lean, dict)
    assert lean["summary"] == {
        "decision": {"rationale": "One. Two!"},
        "items": [{"rationale": "X. Y."}],
        "findings": ["kept"],
    }
    full = boundary_digest([boundary], keep_tools=True)[0]
    assert isinstance(full, dict)
    assert full["summary"] == {
        "tools": [{"tool": "t"}],
        "decision": {"rationale": "One. Two!"},
        "items": [{"rationale": "X. Y.", "tools": 1}],
        "findings": ["kept"],
    }


def test_long_rationale_is_capped() -> None:
    boundary = _b("n", NodeResult(status="completed", summary={"rationale": "A" * 500}))
    entry = boundary_digest([boundary], keep_tools=True)[0]
    assert isinstance(entry, dict)
    assert entry["summary"] == {"rationale": "A" * 399 + "…"}


def test_failure_warnings_and_error_are_kept() -> None:
    failure = Failure(error_class="data", message="m")
    boundary = _b("n", NodeResult(status="failed", warnings=("w",), error=failure))
    entry = boundary_digest([boundary], keep_tools=True)[0]
    assert isinstance(entry, dict)
    assert entry["warnings"] == ["w"]
    assert entry["error"] == failure.model_dump(mode="json")
    assert "skip_reason" not in entry
