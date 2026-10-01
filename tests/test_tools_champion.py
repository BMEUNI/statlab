"""Chunk 14 §4 (champion): `rank_models`.

`rank_models` applies the contract to a `compare_models` table; these tests build a comparison
artifact directly (the shape compare_models writes) so the contract logic is tested in isolation
from scoring. The key mutation is `rank_models` ignoring `interpretability_required`: with it set,
the forest must be ineligible and `top_eligible` the logistic model.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_json, save_json
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.champion import rank_models


def _d(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return cast("dict[str, JsonValue]", value)


def _pl(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _ranking(res: ToolSuccess) -> list[dict[str, JsonValue]]:
    rows = res.summary["ranking"]
    assert isinstance(rows, list)
    return [_d(row) for row in rows]


def _rows_by_candidate(res: ToolSuccess) -> dict[str, dict[str, JsonValue]]:
    return {str(row["candidate"]): row for row in _ranking(res)}


def _comparison(
    run_paths: RunPaths,
    rows: list[tuple[int, str, float, float, float]],
    metric: str = "roc_auc",
) -> ArtifactRef:
    """A compare_models-shaped `table` artifact: rows of (rank, candidate, value, ci_low,
    ci_high)."""
    ranking: list[JsonValue] = [
        {"rank": r, "candidate": c, "value": v, "ci_low": lo, "ci_high": hi}
        for (r, c, v, lo, hi) in rows
    ]
    payload: dict[str, JsonValue] = {"metric": metric, "ranking": ranking}
    ref = new_artifact_ref()
    save_json(
        run_paths.artifact(ref, "table"),
        payload,
        ref=ref,
        kind="table",
        label="comparison",
    )
    return ref


def _write_snapshot(run_paths: RunPaths, contract: DataContract) -> None:
    run_paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")


def test_interpretability_required_makes_forest_ineligible(run_paths: RunPaths) -> None:
    comp = _comparison(
        run_paths,
        [(1, "forest", 0.90, 0.85, 0.95), (2, "logit", 0.80, 0.75, 0.85)],
    )
    _write_snapshot(
        run_paths,
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "binary"},
                "constraints": {"interpretability_required": True},
            }
        ),
    )
    res = rank_models(
        run_paths,
        comp,
        estimators={"forest": "random_forest_classifier", "logit": "logistic_regression"},
    )
    rows = _rows_by_candidate(res)
    assert rows["forest"]["eligible"] is False
    assert rows["forest"]["ineligible_reason"] is not None
    assert rows["logit"]["eligible"] is True
    assert res.summary["top_eligible"] == "logit"


def test_mutation_ignore_interpretability_required(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: `rank_models` ignores `interpretability_required`. Simulated by rebuilding the
    registry so every entry is `interpretable=True` -- then the forest (rank 1) is eligible and
    `top_eligible` becomes `forest`, not `logit`."""
    import dataclasses

    from statlab_tools import registry as reg_mod

    comp = _comparison(
        run_paths,
        [(1, "forest", 0.90, 0.85, 0.95), (2, "logit", 0.80, 0.75, 0.85)],
    )
    _write_snapshot(
        run_paths,
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "binary"},
                "constraints": {"interpretability_required": True},
            }
        ),
    )
    honoured = rank_models(
        run_paths,
        comp,
        estimators={"forest": "random_forest_classifier", "logit": "logistic_regression"},
    )
    assert honoured.summary["top_eligible"] == "logit"

    all_interpretable = {
        name: dataclasses.replace(entry, interpretable=True)
        for name, entry in reg_mod.estimators().items()
    }
    monkeypatch.setattr(reg_mod, "estimators", lambda: all_interpretable)
    mutated = rank_models(
        run_paths,
        comp,
        estimators={"forest": "random_forest_classifier", "logit": "logistic_regression"},
    )
    assert mutated.summary["top_eligible"] == "forest"
    assert honoured.summary["top_eligible"] != mutated.summary["top_eligible"]


def test_success_threshold_direction_aware_for_rmse(run_paths: RunPaths) -> None:
    comp = _comparison(
        run_paths,
        [(1, "good", 0.5, 0.4, 0.6), (2, "bad", 2.0, 1.8, 2.2)],
        metric="rmse",
    )
    _write_snapshot(
        run_paths,
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "continuous"},
                "objective": {
                    "task": "regression",
                    "primary_metric": "rmse",
                    "success_threshold": 1.0,
                },
            }
        ),
    )
    res = rank_models(
        run_paths, comp, estimators={"good": "linear_regression", "bad": "random_forest_regressor"}
    )
    rows = _rows_by_candidate(res)
    # lower is better for rmse: 0.5 <= 1.0 meets, 2.0 > 1.0 does not.
    assert rows["good"]["meets_success_threshold"] is True
    assert rows["bad"]["meets_success_threshold"] is False


def test_success_threshold_only_when_metric_is_primary(run_paths: RunPaths) -> None:
    # ranking metric roc_auc but primary metric is f1 -> threshold does not apply.
    comp = _comparison(run_paths, [(1, "a", 0.9, 0.85, 0.95), (2, "b", 0.8, 0.75, 0.85)])
    _write_snapshot(
        run_paths,
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "binary"},
                "objective": {"primary_metric": "f1", "success_threshold": 0.99},
            }
        ),
    )
    res = rank_models(
        run_paths, comp, estimators={"a": "logistic_regression", "b": "random_forest_classifier"}
    )
    for row in _ranking(res):
        assert row["meets_success_threshold"] is None


def test_no_eligible_candidate_gives_null_with_warning(run_paths: RunPaths) -> None:
    comp = _comparison(run_paths, [(1, "forest", 0.9, 0.85, 0.95), (2, "hgb", 0.8, 0.75, 0.85)])
    _write_snapshot(
        run_paths,
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "binary"},
                "constraints": {"interpretability_required": True},
            }
        ),
    )
    res = rank_models(
        run_paths,
        comp,
        estimators={"forest": "random_forest_classifier", "hgb": "hist_gb_classifier"},
    )
    assert res.summary["top_eligible"] is None
    assert any("eligible" in w for w in res.warnings)


def test_unknown_label_is_tool_param(run_paths: RunPaths) -> None:
    comp = _comparison(run_paths, [(1, "a", 0.9, 0.85, 0.95), (2, "b", 0.8, 0.75, 0.85)])
    with pytest.raises(ToolParamError, match="missing registry names"):
        rank_models(run_paths, comp, estimators={"a": "logistic_regression"})


def test_unknown_estimator_is_tool_param(run_paths: RunPaths) -> None:
    comp = _comparison(run_paths, [(1, "a", 0.9, 0.85, 0.95), (2, "b", 0.8, 0.75, 0.85)])
    with pytest.raises(ToolParamError, match="not in the registry"):
        rank_models(run_paths, comp, estimators={"a": "logistic_regression", "b": "nope"})


def test_selection_note_names_n(run_paths: RunPaths) -> None:
    comp = _comparison(
        run_paths,
        [(1, "a", 0.9, 0.85, 0.95), (2, "b", 0.8, 0.75, 0.85), (3, "c", 0.7, 0.65, 0.75)],
    )
    res = rank_models(
        run_paths,
        comp,
        estimators={
            "a": "logistic_regression",
            "b": "random_forest_classifier",
            "c": "hist_gb_classifier",
        },
    )
    assert "over 3 candidates" in str(res.summary["selection_note"])
    assert res.summary["n_candidates"] == 3


def test_top_two_eligible_distinguishable_overlap(run_paths: RunPaths) -> None:
    comp = _comparison(run_paths, [(1, "a", 0.9, 0.80, 0.95), (2, "b", 0.85, 0.78, 0.92)])
    res = rank_models(
        run_paths, comp, estimators={"a": "logistic_regression", "b": "linear_regression"}
    )
    # overlapping CIs -> not distinguishable.
    assert res.summary["top_two_eligible_distinguishable"] is False


def test_rank_models_writes_table_artifact(run_paths: RunPaths) -> None:
    comp = _comparison(run_paths, [(1, "a", 0.9, 0.85, 0.95), (2, "b", 0.8, 0.75, 0.85)])
    res = rank_models(
        run_paths, comp, estimators={"a": "logistic_regression", "b": "random_forest_classifier"}
    )
    assert res.artifacts[0].kind == "table"
    payload = load_json(run_paths.artifact(res.artifacts[0].ref, "table"))
    assert _d(payload)["metric"] == "roc_auc"
    assert len(_pl(_d(payload)["ranking"])) == 2


def test_comparison_not_a_table_is_tool_param(run_paths: RunPaths) -> None:
    ref = new_artifact_ref()
    save_json(
        run_paths.artifact(ref, "table"), {"not": "a comparison"}, ref=ref, kind="table", label="x"
    )
    with pytest.raises(ToolParamError, match="not a compare_models table"):
        rank_models(run_paths, ref, estimators={"a": "logistic_regression"})


def test_success_threshold_direction_aware_for_roc_auc(run_paths: RunPaths) -> None:
    """greater-is-better metric: a value at or above the floor meets the threshold, below it does
    not (covers `_meets_threshold`'s greater-is-better branch)."""
    comp = _comparison(run_paths, [(1, "a", 0.9, 0.85, 0.95), (2, "b", 0.7, 0.65, 0.75)])
    _write_snapshot(
        run_paths,
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "binary"},
                "objective": {"primary_metric": "roc_auc", "success_threshold": 0.8},
            }
        ),
    )
    res = rank_models(
        run_paths, comp, estimators={"a": "logistic_regression", "b": "random_forest_classifier"}
    )
    rows = _rows_by_candidate(res)
    assert rows["a"]["meets_success_threshold"] is True
    assert rows["b"]["meets_success_threshold"] is False
