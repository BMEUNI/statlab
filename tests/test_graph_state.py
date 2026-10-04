"""Typed state and declared topology identity."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError
from statlab.graph import BoundaryResult, CandidateProgress, RunState
from statlab_contracts.artifacts import new_artifact_ref
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec


def node(name: str = "alpha", *, candidate: bool = False, skippable: bool = False) -> NodeSpec:
    return NodeSpec(
        name, "PER_CANDIDATE" if candidate else "SHARED", skippable, BaseModel, (), "never"
    )


def candidate_spec() -> CandidateSpec:
    return CandidateSpec(
        label="Human baseline",
        rationale="A simple reference candidate.",
        estimator=EstimatorSpec(name="logistic_regression"),
    )


def test_roundtrip_shared_and_candidate_refs() -> None:
    nodes = (node(), node("beta", candidate=True))
    state = RunState.initial(
        run_id="r_one",
        spine_id="synthetic",
        nodes=nodes,
        mode="autonomous",
        candidates=(candidate_spec(),),
    )
    assert RunState.model_validate_json(state.model_dump_json(), strict=True) == state
    assert state.candidate is not None
    assert state.candidate.public_id == "c_01"
    assert state.candidate.database_id("r_one") != state.candidate.database_id("r_two")
    assert state.candidate.spec.label == "Human baseline"
    shared, fitted = new_artifact_ref(), new_artifact_ref()
    boundary = BoundaryResult(
        node_name="alpha",
        execution_id="ne_one",
        result=NodeResult(status="completed", artifacts=(shared,)),
    )
    other = BoundaryResult(
        node_name="beta",
        execution_id="ne_two",
        result=NodeResult(status="completed", artifacts=(fitted,)),
    )
    committed = RunState(
        run_id=state.run_id,
        spine_id=state.spine_id,
        spine=state.spine,
        cursor=2,
        status="completed",
        mode=state.mode,
        shared_results=(boundary,),
        candidate=CandidateProgress(
            public_id=state.candidate.public_id, spec=state.candidate.spec, results=(other,)
        ),
    )
    assert RunState.model_validate_json(committed.model_dump_json(), strict=True) == committed
    assert committed.retained_refs == {shared, fitted}
    assert state.retained_refs == frozenset()
    assert (
        RunState.initial(run_id="r", spine_id="s", nodes=(), mode="guided").retained_refs
        == frozenset()
    )


def test_invalid_topology_and_multiple_candidates() -> None:
    state = RunState.initial(run_id="r", spine_id="s", nodes=(node(),), mode="guided")
    payload = state.model_dump(mode="json")
    with pytest.raises(ValidationError, match="beyond"):
        RunState.model_validate(payload | {"cursor": 2})
    with pytest.raises(ValidationError, match="unique"):
        RunState.model_validate(payload | {"spine": [payload["spine"][0], payload["spine"][0]]})
    with pytest.raises(EngineError, match="fan-out"):
        RunState.initial(
            run_id="r",
            spine_id="s",
            nodes=(node(),),
            mode="guided",
            candidates=(candidate_spec(), candidate_spec()),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"cursor": 1},
        {"status": "completed"},
        {"status": "awaiting_user"},
    ],
)
def test_inconsistent_durable_progress_is_rejected(change: dict[str, object]) -> None:
    state = RunState.initial(run_id="r", spine_id="s", nodes=(node(),), mode="guided")
    with pytest.raises(ValidationError):
        RunState.model_validate(state.model_dump() | change)


def test_candidate_cursor_and_committed_status_validation() -> None:
    state = RunState.initial(
        run_id="r",
        spine_id="s",
        nodes=(node(candidate=True),),
        mode="guided",
        candidates=(candidate_spec(),),
    )
    with pytest.raises(ValidationError, match="candidate boundaries"):
        RunState.model_validate(state.model_dump() | {"cursor": 1})
    state = RunState.initial(run_id="r", spine_id="s", nodes=(node(),), mode="guided")
    boundary = BoundaryResult(
        node_name="alpha", execution_id="ne", result=NodeResult(status="awaiting_user")
    )
    with pytest.raises(ValidationError, match="completed or skipped"):
        RunState.model_validate(state.model_dump() | {"cursor": 1, "shared_results": (boundary,)})
    for name, status, cursor in (
        ("other", "awaiting_user", 0),
        ("alpha", "completed", 0),
        ("alpha", "awaiting_user", 1),
    ):
        pending = BoundaryResult(
            node_name=name, execution_id="ne", result=NodeResult.model_validate({"status": status})
        )
        shared = (
            (
                BoundaryResult(
                    node_name="alpha", execution_id="prior", result=NodeResult(status="completed")
                ),
            )
            if cursor
            else ()
        )
        with pytest.raises(ValidationError, match="pending boundary"):
            RunState.model_validate(
                state.model_dump()
                | {
                    "cursor": cursor,
                    "status": "awaiting_user",
                    "pending_result": pending,
                    "shared_results": shared,
                }
            )
