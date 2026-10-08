"""Chunk 18 §4: the four node-11 interpretation tools.

The suite's spine is oracle-checked numbers, not eyeballed ones:
- permutation importance equals `sklearn.inspection.permutation_importance` called directly with
  the same arguments (its own name shadows sklearn's inside the tool, so the oracle is aliased
  here too);
- standardised coefficients equal `coef_ x population sd` computed by hand, including the sign
  flip when the contract's positive class is `classes_[0]` (P4);
- a linear-regression partial dependence has the known answer: consecutive PD differences over
  grid differences equal the fitted coefficient to 1e-9;
- error analysis plants an all-wrong segment and shuffles the prediction order.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactKind, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, load_json, load_model, save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools._frame import as_str_scalar
from statlab_tools.tools.encoding import fit_scaler
from statlab_tools.tools.evaluation import plot_curve
from statlab_tools.tools.interpretation import (
    analyze_errors,
    coefficient_importance,
    partial_dependence,
    permutation_importance,
)
from statlab_tools.tools.training import FittedModel, predict, predict_proba, train_estimator

# --- helpers -------------------------------------------------------------------------------------


def _d(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _l(value: object) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _f(value: object) -> float:
    assert isinstance(value, (int, float))
    return float(value)


def _save(paths: RunPaths, frame: pd.DataFrame, kind: ArtifactKind = "dataframe") -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, kind), frame, ref=ref, kind=kind, label="x")
    return ref


def _snapshot(paths: RunPaths, positive_class: object) -> None:
    paths.contract_snapshot_json.write_text(
        DataContract.model_validate(
            {"target": {"column": "y", "type": "binary", "positive_class": positive_class}}
        ).model_dump_json(),
        encoding="utf-8",
    )


def _table(paths: RunPaths, res: ToolSuccess) -> dict[str, JsonValue]:
    return _d(load_json(paths.artifact(res.artifacts[0].ref, "table")))


def _curve(paths: RunPaths, res: ToolSuccess) -> dict[str, JsonValue]:
    return _d(load_json(paths.artifact(res.artifacts[0].ref, "curve")))


def _coef_map(paths: RunPaths, res: ToolSuccess, key: str) -> dict[str, float]:
    """`{feature: value}` for a coefficient table's `key` (coef or std_coef)."""
    return {str(_d(r)["feature"]): _f(_d(r)[key]) for r in _l(_table(paths, res)["coefficients"])}


def _fit(paths: RunPaths, frame: pd.DataFrame, name: str, calibrate: bool = False) -> ArtifactRef:
    ref = _save(paths, frame)
    res = train_estimator(paths, ref, target="y", name=name, seed=0, calibrate=calibrate)
    return res.artifacts[0].ref


def _loaded_model(paths: RunPaths, model: ArtifactRef) -> FittedModel:
    obj = load_model(paths.artifact(model, "model"))
    assert isinstance(obj, FittedModel)
    return obj


# --- data builders -------------------------------------------------------------------------------


def _regression_frame(n: int = 300, noise: float = 0.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 3.0 * x1 - 2.0 * x2 + rng.normal(scale=noise, size=n)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y})


def _binary_frame(n: int = 200, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = ((f1 + f2 + rng.normal(scale=0.3, size=n)) > 0).astype(int)
    return pd.DataFrame({"f1": f1, "f2": f2, "y": y})


def _informative_noise_frame(n: int = 300, seed: int = 2) -> pd.DataFrame:
    """`good` drives y; `noise` is independent."""
    rng = np.random.default_rng(seed)
    good = rng.normal(size=n)
    noise = rng.normal(size=n)
    y = (good + rng.normal(scale=0.2, size=n) > 0).astype(int)
    return pd.DataFrame({"good": good, "noise": noise, "y": y})


def _multiclass_frame(n: int = 240, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = np.asarray(pd.Series(pd.cut(f1, bins=3, labels=["low", "mid", "high"])).astype(str))
    return pd.DataFrame({"f1": f1, "f2": f2, "y": y})


# --- permutation importance ----------------------------------------------------------------------


def test_permutation_equals_sklearn_oracle(run_paths: RunPaths) -> None:
    from sklearn.inspection import permutation_importance as sk_pi
    from statlab_tools.registry import scorer_for
    from statlab_tools.tools.training import predict_matrix

    frame = _regression_frame(noise=0.01)
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "linear_regression")
    fitted = _loaded_model(run_paths, model)
    x = predict_matrix(frame, fitted)
    oracle = sk_pi(
        fitted.estimator,
        x,
        frame["y"].to_numpy(dtype="float64"),
        scoring=scorer_for("r2", multiclass=False),
        n_repeats=5,
        random_state=42,
        n_jobs=1,
    )
    res = permutation_importance(run_paths, model, ref, metric="r2", seed=42, n_repeats=5)
    table = {str(_d(r)["feature"]): _d(r) for r in _l(_table(run_paths, res)["features"])}
    assert np.allclose(
        [_f(table["x1"]["importance_mean"]), _f(table["x2"]["importance_mean"])],
        oracle.importances_mean,
        atol=1e-12,
    )


def test_permutation_repeats_run_on_threads(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D17 addendum 42: the repeats run on joblib's threading backend with `parallel_jobs`
    threads, and the numbers equal the sequential (`n_jobs=1`) oracle."""
    import joblib.parallel
    import sklearn.inspection as insp
    import statlab_tools.tools.interpretation as interp
    from statlab_tools.registry import scorer_for
    from statlab_tools.tools.training import predict_matrix

    frame = _regression_frame(noise=0.01)
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "linear_regression")
    fitted = _loaded_model(run_paths, model)
    x = predict_matrix(frame, fitted)
    oracle = insp.permutation_importance(
        fitted.estimator,
        x,
        frame["y"].to_numpy(dtype="float64"),
        scoring=scorer_for("r2", multiclass=False),
        n_repeats=5,
        random_state=42,
        n_jobs=1,
    )

    real = insp.permutation_importance
    seen: list[tuple[object, str]] = []

    def spy(*args: object, **kwargs: object) -> object:
        backend = type(joblib.parallel.get_active_backend()[0]).__name__
        seen.append((kwargs.get("n_jobs"), backend))
        return real(*args, **kwargs)

    monkeypatch.setattr(insp, "permutation_importance", spy)
    monkeypatch.setattr(interp, "parallel_jobs", lambda name: 3)
    res = permutation_importance(run_paths, model, ref, metric="r2", seed=42, n_repeats=5)
    assert seen == [(3, "ThreadingBackend")]
    table = {str(_d(r)["feature"]): _d(r) for r in _l(_table(run_paths, res)["features"])}
    assert np.allclose(
        [_f(table["x1"]["importance_mean"]), _f(table["x2"]["importance_mean"])],
        oracle.importances_mean,
        atol=1e-12,
    )


def test_permutation_informative_ranks_first(run_paths: RunPaths) -> None:
    frame = _informative_noise_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    res = permutation_importance(run_paths, model, ref, metric="accuracy", seed=0)
    rows = [_d(r) for r in _l(res.summary["top"])]
    assert rows[0]["feature"] == "good"
    noise_row = next(r for r in rows if r["feature"] == "noise")
    assert _f(rows[0]["importance_mean"]) > _f(noise_row["importance_mean"])
    assert abs(_f(noise_row["importance_mean"])) < 0.05


def test_permutation_same_seed_same_result(run_paths: RunPaths) -> None:
    frame = _informative_noise_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    a = permutation_importance(run_paths, model, ref, metric="accuracy", seed=7)
    b = permutation_importance(run_paths, model, ref, metric="accuracy", seed=7)
    assert _table(run_paths, a)["features"] == _table(run_paths, b)["features"]


def test_permutation_different_seed_differs(run_paths: RunPaths) -> None:
    frame = _informative_noise_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    a = permutation_importance(run_paths, model, ref, metric="accuracy", seed=1)
    b = permutation_importance(run_paths, model, ref, metric="accuracy", seed=2)
    assert _table(run_paths, a)["features"] != _table(run_paths, b)["features"]


def test_permutation_regression_metric_on_classifier_is_tool_param(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    with pytest.raises(ToolParamError, match="not a classification metric"):
        permutation_importance(run_paths, model, ref, metric="rmse", seed=0)


def test_permutation_multiclass_brier_is_tool_param(run_paths: RunPaths) -> None:
    frame = _multiclass_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "hist_gb_classifier")
    with pytest.raises(ToolParamError, match="no multiclass scorer"):
        permutation_importance(run_paths, model, ref, metric="brier", seed=0)


def test_permutation_missing_feature_is_tool_param(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    model = _fit(run_paths, frame, "logistic_regression")
    short = _save(run_paths, frame.drop(columns=["f2"]))
    with pytest.raises(ToolParamError, match="missing fitted feature"):
        permutation_importance(run_paths, model, short, metric="accuracy", seed=0)


def test_permutation_calibrated_model_works(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression", calibrate=True)
    res = permutation_importance(run_paths, model, ref, metric="accuracy", seed=0)
    assert len(_l(_table(run_paths, res)["features"])) == 2


# --- coefficient importance ----------------------------------------------------------------------


def test_coef_std_coef_equals_coef_times_sd(run_paths: RunPaths) -> None:
    frame = _regression_frame(noise=0.01)
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "linear_regression")
    res = coefficient_importance(run_paths, model, ref)
    for row in _l(_table(run_paths, res)["coefficients"]):
        r = _d(row)
        col = str(r["feature"])
        sd = float(frame[col].std(ddof=0))
        assert _f(r["std_coef"]) == pytest.approx(_f(r["coef"]) * sd, abs=1e-9)


def test_coef_sign_flips_for_classes0_positive(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    default = coefficient_importance(run_paths, model, ref)
    base = _coef_map(run_paths, default, "coef")
    _snapshot(run_paths, 0)  # classes_[0]
    flipped = coefficient_importance(run_paths, model, ref)
    flip = _coef_map(run_paths, flipped, "coef")
    for feat in base:
        assert flip[feat] == pytest.approx(-base[feat], abs=1e-12)


def test_coef_multiclass_gives_k_times_p_rows(run_paths: RunPaths) -> None:
    frame = _multiclass_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    res = coefficient_importance(run_paths, model, ref)
    rows = _l(_table(run_paths, res)["coefficients"])
    fitted = _loaded_model(run_paths, model)
    assert len(rows) == len(fitted.classes) * len(fitted.features)


def test_coef_random_forest_is_tool_param(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "random_forest_classifier")
    with pytest.raises(ToolParamError, match="not interpretable"):
        coefficient_importance(run_paths, model, ref)


def test_coef_calibrated_is_tool_param(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression", calibrate=True)
    with pytest.raises(ToolParamError, match="calibrated"):
        coefficient_importance(run_paths, model, ref)


def test_coef_on_scaled_matrix_std_coef_equals_coef(run_paths: RunPaths) -> None:
    """A standard-scaled column has sd 1, so std_coef == coef to 1e-12."""
    frame = _regression_frame(noise=0.01)
    raw_ref = _save(run_paths, frame)
    scaler = fit_scaler(run_paths, raw_ref, target="y", method="standard")
    from statlab_tools.tools.encoding import apply_scaler

    scaled = load_dataframe(
        run_paths.artifact(
            apply_scaler(run_paths, scaler.artifacts[0].ref, raw_ref).artifacts[0].ref, "dataframe"
        )
    )
    scaled_ref = _save(run_paths, scaled)
    model = _fit(run_paths, scaled, "linear_regression")
    res = coefficient_importance(run_paths, model, scaled_ref)
    for row in _l(_table(run_paths, res)["coefficients"]):
        r = _d(row)
        assert _f(r["std_coef"]) == pytest.approx(_f(r["coef"]), abs=1e-12)


# --- partial dependence --------------------------------------------------------------------------


def test_pd_linear_regression_known_answer(run_paths: RunPaths) -> None:
    """Noiseless linear fit: consecutive PD slopes equal the coefficient (3.0) to 1e-9."""
    frame = _regression_frame(noise=0.0)
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "linear_regression")
    res = partial_dependence(run_paths, model, ref, column="x1", grid_resolution=10)
    curve = _curve(run_paths, res)
    xs = np.asarray([_f(v) for v in _l(curve["x"])])
    ys = np.asarray([_f(v) for v in _l(_d(curve["series"])["prediction"])])
    slopes = np.diff(ys) / np.diff(xs)
    assert np.abs(slopes - 3.0).max() < 1e-9


def test_pd_binary_monotone_in_coef_sign(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    res = partial_dependence(run_paths, model, ref, column="f1", grid_resolution=15)
    curve = _curve(run_paths, res)
    fitted = _loaded_model(run_paths, model)
    pos = [as_str_scalar(c) for c in fitted.classes][1]
    ys = np.asarray([_f(v) for v in _l(_d(curve["series"])[pos])])
    assert np.all(np.diff(ys) > 0)  # positive coefficient -> increasing P(positive)


def test_pd_scaler_maps_x_original(run_paths: RunPaths) -> None:
    frame = _regression_frame(noise=0.01)
    raw_ref = _save(run_paths, frame)
    scaler = fit_scaler(run_paths, raw_ref, target="y", method="standard")
    from statlab_tools.tools.encoding import apply_scaler

    scaled = load_dataframe(
        run_paths.artifact(
            apply_scaler(run_paths, scaler.artifacts[0].ref, raw_ref).artifacts[0].ref, "dataframe"
        )
    )
    scaled_ref = _save(run_paths, scaled)
    model = _fit(run_paths, scaled, "linear_regression")
    res = partial_dependence(
        run_paths, model, scaled_ref, column="x1", grid_resolution=8, scaler=scaler.artifacts[0].ref
    )
    curve = _curve(run_paths, res)
    from statlab_tools.tools.encoding import FittedScaler

    fs = load_model(run_paths.artifact(scaler.artifacts[0].ref, "transformer"))
    assert isinstance(fs, FittedScaler)
    center, scale = fs.params["x1"]
    xs = np.asarray([_f(v) for v in _l(curve["x"])])
    xo = np.asarray([_f(v) for v in _l(curve["x_original"])])
    assert np.allclose(xo, xs * scale + center, atol=1e-12)
    # render it so plot_curve draws over x_original (the scaler-present axis branch).
    rendered = plot_curve(run_paths, res.artifacts[0].ref)
    out = run_paths.artifact(rendered.artifacts[0].ref, "plot")
    assert out.exists() and out.stat().st_size > 0


def test_pd_dummy_column_gets_x_original_equal_x_and_grid_01(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    frame["dummy"] = (frame["f1"] > 0).astype(bool)
    raw_ref = _save(run_paths, frame)
    scaler = fit_scaler(run_paths, raw_ref, target="y", method="standard")
    model = _fit(run_paths, frame, "logistic_regression")
    res = partial_dependence(
        run_paths, model, raw_ref, column="dummy", scaler=scaler.artifacts[0].ref
    )
    curve = _curve(run_paths, res)
    xs = [_f(v) for v in _l(curve["x"])]
    xo = [_f(v) for v in _l(curve["x_original"])]
    assert xs == [0.0, 1.0]
    assert xo == xs


def test_pd_low_cardinality_grid_is_unique_values(run_paths: RunPaths) -> None:
    rng = np.random.default_rng(0)
    n = 120
    cat = rng.choice([1.0, 2.0, 3.0], size=n)
    y = (cat + rng.normal(scale=0.3, size=n) > 2).astype(int)
    frame = pd.DataFrame({"cat": cat, "f2": rng.normal(size=n), "y": y})
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    res = partial_dependence(run_paths, model, ref, column="cat", grid_resolution=20)
    curve = _curve(run_paths, res)
    assert [_f(v) for v in _l(curve["x"])] == [1.0, 2.0, 3.0]


def test_pd_non_feature_is_tool_param(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    with pytest.raises(ToolParamError, match="not a model feature"):
        partial_dependence(run_paths, model, ref, column="nope")


def test_pd_multiclass_gives_k_series(run_paths: RunPaths) -> None:
    frame = _multiclass_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    res = partial_dependence(run_paths, model, ref, column="f1", grid_resolution=6)
    curve = _curve(run_paths, res)
    assert set(_d(curve["series"])) == {"low", "mid", "high"}


def test_pd_scaler_wrong_artifact_is_tool_param(run_paths: RunPaths) -> None:
    frame = _regression_frame(noise=0.01)
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "linear_regression")
    with pytest.raises(ToolParamError, match="not a FittedScaler"):
        partial_dependence(run_paths, model, ref, column="x1", scaler=model)


def test_plot_curve_renders_partial_dependence(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    pd_res = partial_dependence(run_paths, model, ref, column="f1", grid_resolution=6)
    # no scaler -> x_original is null, so plot_curve draws over x (the "(scaled)" axis label path).
    assert _curve(run_paths, pd_res)["x_original"] is None
    res = plot_curve(run_paths, pd_res.artifacts[0].ref, title="PD of $cost$")
    out = run_paths.artifact(res.artifacts[0].ref, "plot")
    assert out.exists() and out.stat().st_size > 0


def test_plot_curve_renders_multiclass_partial_dependence(run_paths: RunPaths) -> None:
    """Multiple series exercise the legend branch of the partial-dependence renderer."""
    frame = _multiclass_frame()
    ref = _save(run_paths, frame)
    model = _fit(run_paths, frame, "logistic_regression")
    pd_res = partial_dependence(run_paths, model, ref, column="f1", grid_resolution=6)
    assert len(_d(_curve(run_paths, pd_res)["series"])) == 3
    res = plot_curve(run_paths, pd_res.artifacts[0].ref)
    out = run_paths.artifact(res.artifacts[0].ref, "plot")
    assert out.exists() and out.stat().st_size > 0


# --- error analysis ------------------------------------------------------------------------------


def _proba_truth(run_paths: RunPaths) -> tuple[ArtifactRef, pd.DataFrame, ArtifactRef]:
    frame = _binary_frame()
    model = _fit(run_paths, frame, "logistic_regression")
    pr = predict_proba(run_paths, model, _save(run_paths, frame)).artifacts[0].ref
    return pr, frame, model


def test_errors_planted_all_wrong_segment_ranks_first(run_paths: RunPaths) -> None:
    frame = _binary_frame(n=120)
    model = _fit(run_paths, frame, "logistic_regression")
    frame_ref = _save(run_paths, frame)
    preds = load_dataframe(
        run_paths.artifact(predict(run_paths, model, frame_ref).artifacts[0].ref, "predictions")
    )
    # plant a segment where every prediction is wrong by flipping both truth and group.
    truth = frame.copy()
    truth["grp"] = "ok"
    bad = preds.index[:20]
    truth.loc[bad, "grp"] = "bad"
    truth.loc[bad, "y"] = 1 - preds.loc[bad, "prediction"].to_numpy()
    pr = predict_proba(run_paths, model, frame_ref).artifacts[0].ref
    res = analyze_errors(run_paths, pr, _save(run_paths, truth), target="y", by=("grp",))
    worst = [_d(w) for w in _l(res.summary["worst"])]
    assert worst[0]["segment"] == "bad"
    assert _f(worst[0]["error"]) == pytest.approx(1.0)


def test_errors_small_segment_excluded_from_worst(run_paths: RunPaths) -> None:
    frame = _binary_frame(n=120)
    model = _fit(run_paths, frame, "logistic_regression")
    frame_ref = _save(run_paths, frame)
    preds = load_dataframe(
        run_paths.artifact(predict(run_paths, model, frame_ref).artifacts[0].ref, "predictions")
    )
    truth = frame.copy()
    truth["grp"] = "big"
    small = preds.index[:5]
    truth.loc[small, "grp"] = "tiny"
    truth.loc[small, "y"] = 1 - preds.loc[small, "prediction"].to_numpy()
    pr = predict_proba(run_paths, model, frame_ref).artifacts[0].ref
    res = analyze_errors(run_paths, pr, _save(run_paths, truth), target="y", by=("grp",))
    table = {_d(r)["segment"]: _d(r) for r in _l(_table(run_paths, res)["segments"])}
    assert table["tiny"]["small"] is True
    assert "tiny" not in {_d(w)["segment"] for w in _l(res.summary["worst"])}


def test_errors_numeric_by_quartiles_and_missing(run_paths: RunPaths) -> None:
    frame = _binary_frame(n=200)
    model = _fit(run_paths, frame, "logistic_regression")
    frame_ref = _save(run_paths, frame)
    pr = predict_proba(run_paths, model, frame_ref).artifacts[0].ref
    truth = frame.copy()
    vals = np.arange(len(truth), dtype="float64")
    vals[:10] = np.nan
    truth["score"] = vals  # 50 distinct non-null -> quartiles
    res = analyze_errors(run_paths, pr, _save(run_paths, truth), target="y", by=("score",))
    segs = {str(_d(r)["segment"]) for r in _l(_table(run_paths, res)["segments"])}
    non_missing = [s for s in segs if s != "<missing>"]
    assert "<missing>" in segs
    assert len(non_missing) <= 4


def test_errors_shuffled_prediction_order_identical_table(run_paths: RunPaths) -> None:
    pr, frame, _model = _proba_truth(run_paths)
    truth = frame.copy()
    truth["grp"] = (["a"] * (len(truth) // 2)) + (["b"] * (len(truth) - len(truth) // 2))
    truth_ref = _save(run_paths, truth)
    straight = analyze_errors(run_paths, pr, truth_ref, target="y", by=("grp",))
    preds = load_dataframe(run_paths.artifact(pr, "predictions"))
    shuffled = _save(run_paths, preds.sample(frac=1.0, random_state=13), "predictions")
    shuf = analyze_errors(run_paths, shuffled, truth_ref, target="y", by=("grp",))
    assert _table(run_paths, straight)["segments"] == _table(run_paths, shuf)["segments"]


def test_errors_regression_bias_sign(run_paths: RunPaths) -> None:
    rng = np.random.default_rng(0)
    n = 120
    x = rng.normal(size=n)
    frame = pd.DataFrame({"x": x, "y": 2.0 * x})
    model = _fit(run_paths, frame, "linear_regression")
    frame_ref = _save(run_paths, frame)
    preds = load_dataframe(
        run_paths.artifact(predict(run_paths, model, frame_ref).artifacts[0].ref, "predictions")
    )
    # over-predict by a constant +5.
    preds["prediction"] = preds["prediction"] + 5.0
    over = _save(run_paths, preds, "predictions")
    truth = frame.copy()
    truth["grp"] = "all"
    res = analyze_errors(run_paths, over, _save(run_paths, truth), target="y", by=("grp",))
    row = _d(_l(_table(run_paths, res)["segments"])[0])
    assert _f(row["bias"]) == pytest.approx(5.0, abs=1e-9)


def test_errors_by_target_is_tool_param(run_paths: RunPaths) -> None:
    pr, frame, _model = _proba_truth(run_paths)
    with pytest.raises(ToolParamError, match="cannot segment its own error"):
        analyze_errors(run_paths, pr, _save(run_paths, frame), target="y", by=("y",))


def test_errors_unknown_by_is_tool_param(run_paths: RunPaths) -> None:
    pr, frame, _model = _proba_truth(run_paths)
    with pytest.raises(ToolParamError, match="are not in truth"):
        analyze_errors(run_paths, pr, _save(run_paths, frame), target="y", by=("nope",))


def test_errors_neither_proba_nor_prediction_is_tool_param(run_paths: RunPaths) -> None:
    bogus = _save(run_paths, pd.DataFrame({"other": [1, 2, 3]}), "predictions")
    truth = _save(run_paths, pd.DataFrame({"y": [0, 1, 0], "grp": ["a", "a", "b"]}))
    with pytest.raises(ToolParamError, match="neither proba__"):
        analyze_errors(run_paths, bogus, truth, target="y", by=("grp",))


def test_permutation_frame_missing_target_is_tool_param(run_paths: RunPaths) -> None:
    frame = _binary_frame()
    model = _fit(run_paths, frame, "logistic_regression")
    no_target = _save(run_paths, frame.drop(columns=["y"]))
    with pytest.raises(ToolParamError, match="missing the model's target"):
        permutation_importance(run_paths, model, no_target, metric="accuracy", seed=0)


def test_coef_train_missing_feature_is_tool_param(run_paths: RunPaths) -> None:
    frame = _regression_frame(noise=0.01)
    model = _fit(run_paths, frame, "linear_regression")
    short = _save(run_paths, frame.drop(columns=["x2"]))
    with pytest.raises(ToolParamError, match="missing fitted feature"):
        coefficient_importance(run_paths, model, short)


def test_errors_truth_missing_target_is_tool_param(run_paths: RunPaths) -> None:
    pr, frame, _model = _proba_truth(run_paths)
    truth = _save(run_paths, frame.drop(columns=["y"]).assign(grp="a"))
    with pytest.raises(ToolParamError, match="not a column in truth"):
        analyze_errors(run_paths, pr, truth, target="y", by=("grp",))


def test_errors_threshold_multiclass_is_tool_param(run_paths: RunPaths) -> None:
    frame = _multiclass_frame()
    model = _fit(run_paths, frame, "logistic_regression")
    frame_ref = _save(run_paths, frame)
    pr = predict_proba(run_paths, model, frame_ref).artifacts[0].ref
    truth = _save(run_paths, frame.assign(grp="a"))
    with pytest.raises(ToolParamError, match="binary only"):
        analyze_errors(run_paths, pr, truth, target="y", by=("grp",), threshold=0.5)


# --- end to end ----------------------------------------------------------------------------------


def test_end_to_end_node10_on_the_skeleton_pipeline(run_paths: RunPaths) -> None:
    """Spec §4: the skeleton's `subscribers` through split, impute, one-hot, scale and a logistic
    model, then the four tools on **valid**, and `analyze_errors` on the **raw** valid split."""
    from statlab_tools.tools.cleaning import apply_imputer, fit_imputer
    from statlab_tools.tools.encoding import apply_onehot_encoder, apply_scaler, fit_onehot_encoder
    from statlab_tools.tools.splitting import split_stratified

    from tests.test_skeleton import subscribers

    def ref(result: ToolSuccess) -> ArtifactRef:
        return result.artifacts[0].ref

    frame = subscribers().drop(columns=["subscriber_id"])
    run_paths.contract_snapshot_json.write_text(
        DataContract.model_validate(
            {"target": {"column": "churned", "type": "binary", "positive_class": 1}}
        ).model_dump_json()
    )
    split = split_stratified(run_paths, _save(run_paths, frame), target="churned", seed=42)
    by_role = {a.split: a.ref for a in split.artifacts}
    raw_train, raw_valid = by_role["train"], by_role["valid"]
    imputer = ref(fit_imputer(run_paths, raw_train, target="churned"))
    train, valid = (ref(apply_imputer(run_paths, imputer, f)) for f in (raw_train, raw_valid))
    encoder = ref(fit_onehot_encoder(run_paths, train, target="churned"))
    train, valid = (ref(apply_onehot_encoder(run_paths, encoder, f)) for f in (train, valid))
    scaler = ref(fit_scaler(run_paths, train, target="churned", method="standard"))
    train, valid = (ref(apply_scaler(run_paths, scaler, f)) for f in (train, valid))
    model = ref(
        train_estimator(run_paths, train, target="churned", name="logistic_regression", seed=42)
    )

    # tenure_months is planted; every region dummy is noise.
    pi = permutation_importance(run_paths, model, valid, metric="roc_auc", seed=42)
    order = [str(_d(r)["feature"]) for r in _l(_table(run_paths, pi)["features"])]
    regions = [f for f in order if f.startswith("region__")]
    assert regions
    assert all(order.index("tenure_months") < order.index(f) for f in regions)

    # planted: basic +0.9, standard 0, premium -1.0, towards the positive class 1.
    coef = _coef_map(run_paths, coefficient_importance(run_paths, model, train), "std_coef")
    assert coef["plan__premium"] < 0 < coef["plan__basic"]

    # planted tenure effect is negative; x_original is in the raw column's units.
    pdp = partial_dependence(run_paths, model, valid, column="tenure_months", scaler=scaler)
    curve = _curve(run_paths, pdp)
    ys = np.asarray([_f(v) for v in _l(_d(curve["series"])["1"])])
    assert np.all(np.diff(ys) < 0)
    xo = np.asarray([_f(v) for v in _l(curve["x_original"])])
    raw_tenure = load_dataframe(run_paths.artifact(raw_valid, "dataframe"))["tenure_months"]
    assert raw_tenure.min() - 1e-9 <= xo.min() and xo.max() <= raw_tenure.max() + 1e-9

    # predictions on the transformed valid align by label with the raw valid split.
    proba = ref(predict_proba(run_paths, model, valid))
    ae = analyze_errors(run_paths, proba, raw_valid, target="churned", by=("plan", "region"))
    rows = [_d(r) for r in _l(_table(run_paths, ae)["segments"])]
    raw = load_dataframe(run_paths.artifact(raw_valid, "dataframe"))
    for column in ("plan", "region"):
        expected = {str(v) for v in raw[column].dropna().unique()}
        if raw[column].isna().any():
            expected.add("<missing>")
        assert {str(r["segment"]) for r in rows if r["column"] == column} == expected
