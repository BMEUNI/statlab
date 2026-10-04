"""Typed state and declared topology identity."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError
from statlab.domains.base import NodeResult, NodeSpec
from statlab.graph import BoundaryResult, CandidateProgress, RunState, TraversalPlan
from statlab_contracts.artifacts import new_artifact_ref
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.envelope import Failure


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
        branches=(
            CandidateProgress(
                public_id=state.candidate.public_id, spec=state.candidate.spec, results=(other,)
            ),
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
    with pytest.raises(ValidationError, match="specifications must be unique"):
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


@pytest.mark.parametrize("field", ["training_node", "evaluation_node"])
def test_traversal_requires_paired_training_and_evaluation(field: str) -> None:
    with pytest.raises(ValidationError, match="paired"):
        TraversalPlan.model_validate({field: "fit"})


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate_id",
        "bad_traversal",
        "shared_history",
        "design_wave",
        "branch_wave",
        "branch_position",
        "undeclared_retune",
        "completed_position",
        "halt_missing",
        "halt_at_end",
        "halt_node",
        "halt_status",
        "premature_join",
    ],
)
def test_invalid_persisted_fanout_topology(corruption: str) -> None:
    state = RunState.initial(
        run_id="r",
        spine_id="s",
        mode="autonomous",
        nodes=(
            node("design"),
            node("fit", candidate=True),
            node("evaluate", candidate=True),
            node("tail"),
        ),
        candidates=tuple(
            CandidateSpec(
                label=f"C{i}",
                rationale="A distinct deterministic candidate.",
                estimator=EstimatorSpec(name="fixture", params={"choice": i}),
            )
            for i in range(2)
        ),
    )
    data = state.model_dump(mode="json")
    data["traversal"] = TraversalPlan(design_node="design").model_dump(mode="json")
    branches = data["branches"]
    assert isinstance(branches, list) and isinstance(branches[0], dict)
    branch = branches[0]
    completed = BoundaryResult(
        node_name="design", execution_id="ne_design", result=NodeResult(status="completed")
    ).model_dump(mode="json")
    failed = BoundaryResult(
        node_name="fit",
        execution_id="ne_fit",
        result=NodeResult(status="failed", error=Failure(error_class="data", message="Bad fit")),
    ).model_dump(mode="json")
    if corruption == "duplicate_id":
        branches[1]["public_id"] = branch["public_id"]
    elif corruption == "bad_traversal":
        data["traversal"] = {"design_node": "absent"}
    elif corruption == "shared_history":
        data["shared_results"] = [completed]
    elif corruption == "design_wave":
        data["cursor"], data["shared_results"] = 1, [completed, completed]
    elif corruption == "branch_wave":
        branch["wave"] = 2
    elif corruption == "branch_position":
        branch["position"] = 4
    elif corruption == "undeclared_retune":
        branch["retune_count"] = 1
    elif corruption == "completed_position":
        branch["status"] = "completed"
    elif corruption == "premature_join":
        data["cursor"], data["shared_results"] = 3, [completed]
    else:
        branch["status"] = "failed"
        if corruption != "halt_missing":
            branch["pending_result"] = failed
        if corruption == "halt_at_end":
            branch["position"] = 3
            branch["results"] = [
                BoundaryResult(
                    node_name=name, execution_id=f"ne_{name}", result=NodeResult(status="completed")
                ).model_dump(mode="json")
                for name in ("fit", "evaluate")
            ]
        elif corruption == "halt_node":
            failed["node_name"] = "evaluate"
        elif corruption == "halt_status":
            branch["status"] = "awaiting_user"
    with pytest.raises(ValidationError):
        RunState.model_validate_json(json.dumps(data), strict=True)


def test_completed_overlap_is_valid_before_candidate_join() -> None:
    state = RunState.initial(
        run_id="r",
        spine_id="s",
        mode="autonomous",
        nodes=(node("design"), node("companion"), node("fit", candidate=True)),
        candidates=(candidate_spec(),),
    )
    data = state.model_dump(mode="json")
    data["traversal"] = {"overlap_shared": "companion"}
    data["cursor"] = 1
    data["shared_results"] = [
        BoundaryResult(
            node_name=name, execution_id=f"ne_{name}", result=NodeResult(status="completed")
        ).model_dump(mode="json")
        for name in ("design", "companion")
    ]
    restored = RunState.model_validate_json(json.dumps(data), strict=True)
    assert len(restored.shared_results) == 2 and not restored.branches[0].results


def test_legacy_null_candidate_and_missing_position_json() -> None:
    state = RunState.initial(run_id="r", spine_id="s", mode="autonomous", nodes=(node(),))
    data = state.model_dump(mode="json")
    data.pop("branches")
    data["candidate"] = None
    assert RunState.model_validate_json(json.dumps(data), strict=True).branches == ()
    state = RunState.initial(
        run_id="r",
        spine_id="s",
        mode="autonomous",
        nodes=(node("fit", candidate=True),),
        candidates=(candidate_spec(),),
    )
    data = state.model_dump(mode="json")
    data["candidate"] = data.pop("branches")[0]
    data["candidate"].pop("position")
    data["cursor"] = 1
    data["candidate"]["results"] = [
        BoundaryResult(
            node_name="fit", execution_id="ne_fit", result=NodeResult(status="completed")
        ).model_dump(mode="json")
    ]
    restored = RunState.model_validate_json(json.dumps(data), strict=True)
    assert restored.candidate is not None and restored.candidate.position == 1
