"""Chunk 20r §4 (Ridge): the second linear, interpretable regressor (D-d).

Ridge trains/predicts/tunes over its `alpha` grid, degenerates to OLS as alpha -> 0, shrinks
coefficients as alpha grows, and `coefficient_importance` works on it unchanged (it is
`interpretable` and has a `coef_`).
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, load_model, save_dataframe
from statlab_tools.tools.interpretation import coefficient_importance
from statlab_tools.tools.training import FittedModel, predict, train_estimator, tune_estimator


def _d(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _regression_frame(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = 3.0 * f1 - 2.0 * f2 + rng.normal(scale=0.1, size=n)
    return pd.DataFrame({"f1": f1, "f2": f2, "y": y})


def _fitted(paths: RunPaths, res: ToolSuccess) -> FittedModel:
    obj = load_model(paths.artifact(res.artifacts[0].ref, "model"))
    assert isinstance(obj, FittedModel)
    return obj


def _coef(estimator: object) -> np.ndarray:
    from sklearn.base import BaseEstimator

    est = cast("BaseEstimator", estimator)
    return np.asarray(est.coef_, dtype="float64")


def test_ridge_trains_and_predicts(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="ridge", seed=1)
    out = load_dataframe(
        run_paths.artifact(
            predict(run_paths, res.artifacts[0].ref, ref).artifacts[0].ref, "predictions"
        )
    )
    pred = out["prediction"].to_numpy()
    truth = frame["y"].to_numpy()
    ss_res = float(((truth - pred) ** 2).sum())
    ss_tot = float(((truth - truth.mean()) ** 2).sum())
    assert 1 - ss_res / ss_tot > 0.9


def test_ridge_tunes_over_its_alpha_grid(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    res = tune_estimator(run_paths, ref, target="y", name="ridge", metric="r2", seed=1)
    best = _d(res.summary["best_params"])
    assert "alpha" in best
    assert best["alpha"] in (0.1, 1.0, 10.0, 100.0)


def test_ridge_near_zero_alpha_matches_linear_regression(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    ridge = train_estimator(
        run_paths, ref, target="y", name="ridge", seed=1, params={"alpha": 1e-10}
    )
    ols = train_estimator(run_paths, ref, target="y", name="linear_regression", seed=1)
    ridge_coef = _coef(_fitted(run_paths, ridge).estimator)
    ols_coef = _coef(_fitted(run_paths, ols).estimator)
    assert np.allclose(ridge_coef, ols_coef, atol=1e-6)


def test_ridge_larger_alpha_shrinks_coefficients(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    small = train_estimator(run_paths, ref, target="y", name="ridge", seed=1, params={"alpha": 0.1})
    large = train_estimator(
        run_paths, ref, target="y", name="ridge", seed=1, params={"alpha": 100.0}
    )
    small_norm = float(np.linalg.norm(_coef(_fitted(run_paths, small).estimator)))
    large_norm = float(np.linalg.norm(_coef(_fitted(run_paths, large).estimator)))
    assert large_norm < small_norm


def test_coefficient_importance_works_on_ridge(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="ridge", seed=1)
    out = coefficient_importance(run_paths, res.artifacts[0].ref, ref)
    top = out.summary["top"]
    assert isinstance(top, list)
    features = {_d(r)["feature"] for r in top}
    assert features == {"f1", "f2"}
