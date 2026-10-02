"""Chunk 20r §4: `fit_mnlogit` and multinomial `coefficient_table`.

The index order is the one thing most likely to go wrong (spec): `params`/`bse`/`pvalues` are
`(p, K-1)`, `conf_int()` is `(K-1, p, 2)`, and `cov_params()` is ordered `params.ravel(order="F")`
(equation-major). Each is proved against an oracle built from statsmodels' own `cov_params()` /
`conf_int()`, so a transposed read or a C-order restriction fails a named test (the DoD
mutations). Separation surfaces as a numpy overflow under `np.errstate`, not a
`PerfectSeparationWarning`, and is caught both under pytest's `filterwarnings=error` and in a
worker thread.
"""

from __future__ import annotations

import threading
from typing import ClassVar, Protocol, cast

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract, InferenceGoal
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe, save_json
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.inference import (
    FittedInference,
    coefficient_table,
    compute_influence,
    fit_mnlogit,
    inferential_fit_summary,
    plot_residuals,
)
from statlab_tools.tools.inference import (
    test_residuals as run_residual_tests,
)


def _d(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return cast("dict[str, JsonValue]", value)


class _ResultProto(Protocol):
    """The MNLogit results attributes the oracle tests read, as a typed `Protocol` so the casts
    check without an `Any` (statsmodels is untyped). Same narrow-at-the-boundary posture as the
    production module and `test_tools_inference.py`'s `_ResultProto`."""

    params: object

    def cov_params(self) -> object: ...
    def conf_int(self, alpha: float) -> object: ...


def _res(fitted: FittedInference) -> _ResultProto:
    return cast("_ResultProto", fitted.result)


def _l(value: object) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _f(value: object) -> float:
    assert isinstance(value, (int, float))
    return float(value)


def _put(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="x")
    return ref


def _manifest(paths: RunPaths, terms: dict[str, str]) -> ArtifactRef:
    """A manifest mapping each matrix term to its source column (for coefficient_table)."""
    term_map: dict[str, JsonValue] = dict(terms)
    man: dict[str, JsonValue] = {
        "terms": term_map,
        "references": {},
        "imputed": [],
        "indicators": [],
        "dropped": [],
    }
    ref = new_artifact_ref()
    save_json(paths.artifact(ref, "table"), man, ref=ref, kind="table", label="m")
    return ref


def _planted_frame(n: int = 5000, seed: int = 7) -> pd.DataFrame:
    """A three-class frame (base 'a') with planted effects (spec §4):
    x1 is +0.8 in equation b and -0.6 in equation c; the group dummy g_b is +1.0 in equation b;
    g_c is +1.2 in equation c; plus one pure-noise column. Classes drawn from the softmax so the
    MLE recovers the planted coefficients. 'a' is the most frequent class by construction (its
    equation is the zero reference)."""
    rng = np.random.default_rng(seed)
    x1 = rng.normal(size=n)
    g = rng.choice(["x", "y", "z"], size=n)
    noise = rng.normal(size=n)
    g_b = (g == "y").astype(float)
    g_c = (g == "z").astype(float)
    eta_a = np.zeros(n)
    eta_b = 0.8 * x1 + 1.0 * g_b
    eta_c = -0.6 * x1 + 1.2 * g_c
    eta = np.column_stack([eta_a, eta_b, eta_c])
    probs = np.exp(eta)
    probs /= probs.sum(axis=1, keepdims=True)
    labels = np.array(["a", "b", "c"])
    draw = np.array([rng.choice(3, p=probs[i]) for i in range(n)])
    y = labels[draw]
    return pd.DataFrame(
        {
            "x1": x1,
            "g_b": g_b,
            "g_c": g_c,
            "noise": noise,
            "y": y,
        }
    )


_PREDICTORS = ("x1", "g_b", "g_c", "noise")
_TERMS = {"x1": "x1", "g_b": "g", "g_c": "g", "noise": "noise"}


def _base_a_snapshot(paths: RunPaths) -> None:
    """Pin the base class to 'a' so the equations are b and c, where the coefficients are planted
    (otherwise the default base is the most frequent class, which is not 'a' here)."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "multiclass", "class_order": ["a", "b", "c"]}}
    )
    paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")


def _fit(paths: RunPaths, ref: ArtifactRef) -> FittedInference:
    from statlab_tools.artifacts import load_model

    obj = load_model(paths.artifact(ref, "inferential_fit"))
    assert isinstance(obj, FittedInference)
    return obj


# --- known answer: planted coefficients ---------------------------------------------------------


def test_mnlogit_recovers_planted_coefficients(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _planted_frame())
    _base_a_snapshot(run_paths)
    res = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    assert res.summary["base_class"] == "a"
    classes = res.summary["classes"]
    assert isinstance(classes, list)
    assert classes[0] == "a"
    assert set(classes) == {"a", "b", "c"}
    man = _manifest(run_paths, _TERMS)
    table = coefficient_table(run_paths, _fit_ref(res), man)
    rows = _l(_d(_loaded(run_paths, table))["terms"])
    by_eq_term = {(_d(r)["equation"], _d(r)["term"]): _f(_d(r)["coef"]) for r in rows}
    planted = {
        ("b", "x1"): 0.8,
        ("c", "x1"): -0.6,
        ("b", "g_b"): 1.0,
        ("c", "g_c"): 1.2,
    }
    for key, want in planted.items():
        assert abs(by_eq_term[key] - want) < 0.15, f"{key}: {by_eq_term[key]} vs {want}"


def test_mnlogit_goal_on_group_rejects_and_df_is_terms_times_k_minus_1(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _planted_frame())
    res = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    man = _manifest(run_paths, _TERMS)
    goal_g = InferenceGoal(question="does g matter?", predictors=("g",), alpha=0.05)
    goal_noise = InferenceGoal(question="does noise matter?", predictors=("noise",), alpha=0.05)
    out = coefficient_table(run_paths, _fit_ref(res), man, (goal_g, goal_noise))
    goals = {_d(g)["question"]: _d(g) for g in _l(out.summary["goals"])}
    g_row = goals["does g matter?"]
    assert g_row["df"] == 4  # 2 terms (g_b, g_c) x 2 equations
    assert g_row["reject"] is True
    noise_row = goals["does noise matter?"]
    assert noise_row["df"] == 2  # 1 term x 2 equations
    assert noise_row["reject"] is False


def test_mnlogit_goal_statistic_equals_hand_built_chi2(run_paths: RunPaths) -> None:
    """The goal χ² equals a hand-built b' V^-1 b from cov_params() (F-order) to 1e-9 (spec)."""
    ref = _put(run_paths, _planted_frame())
    _base_a_snapshot(run_paths)
    res = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    fitted = _fit(run_paths, _fit_ref(res))
    man = _manifest(run_paths, _TERMS)
    goal_g = InferenceGoal(question="g?", predictors=("g",), alpha=0.05)
    out = coefficient_table(run_paths, _fit_ref(res), man, (goal_g,))
    stat = _f(_d(_l(out.summary["goals"])[0])["stat"])

    result = _res(fitted)
    params = np.asarray(result.params, dtype="float64")  # (p, K-1)
    cov = np.asarray(result.cov_params(), dtype="float64")  # F-order of params
    p = params.shape[0]
    k1 = params.shape[1]
    # terms whose source column is 'g' (g_b, g_c), in both equations, F-order flat = t + j*p
    terms = list(fitted.terms)
    g_terms = [terms.index("g_b"), terms.index("g_c")]
    idx = [t + j * p for j in range(k1) for t in g_terms]
    restriction = np.zeros((len(idx), p * k1), dtype="float64")
    for row, col in enumerate(idx):
        restriction[row, col] = 1.0
    b = params.ravel(order="F")
    rb = restriction @ b
    v = restriction @ cov @ restriction.T
    hand_chi2 = float(rb @ np.linalg.inv(v) @ rb)
    assert abs(stat - hand_chi2) < 1e-9


def test_mnlogit_ci_equals_conf_int_row(run_paths: RunPaths) -> None:
    """Each row's CI equals conf_int()[j, t, :] (NOT the transpose) to 1e-9 (spec oracle)."""
    ref = _put(run_paths, _planted_frame())
    res = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    fitted = _fit(run_paths, _fit_ref(res))
    man = _manifest(run_paths, _TERMS)
    rows = _l(_d(_loaded(run_paths, coefficient_table(run_paths, _fit_ref(res), man)))["terms"])

    conf = np.asarray(_res(fitted).conf_int(alpha=0.05), dtype="float64")  # (K-1, p, 2)
    equations = list(fitted.classes[1:])
    terms = list(fitted.terms)
    for r in rows:
        rd = _d(r)
        j = equations.index(cast(str, rd["equation"]))
        t = terms.index(cast(str, rd["term"]))
        assert abs(_f(rd["ci_low"]) - conf[j, t, 0]) < 1e-9
        assert abs(_f(rd["ci_high"]) - conf[j, t, 1]) < 1e-9


# --- base class ---------------------------------------------------------------------------------


def test_default_base_is_most_frequent_class(run_paths: RunPaths) -> None:
    # 'b' dominates; it must be the base, not alphabetical 'a'. x is pure noise (no separation).
    rng = np.random.default_rng(5)
    y = ["b"] * 150 + ["c"] * 60 + ["a"] * 30
    frame = pd.DataFrame({"x": rng.normal(size=len(y)), "y": y})
    ref = _put(run_paths, frame)
    res = fit_mnlogit(run_paths, ref, "y", ("x",))
    assert res.summary["base_class"] == "b"  # most frequent, not alphabetical 'a'


def test_class_order_sets_base_and_flips_equation_signs(run_paths: RunPaths) -> None:
    frame = _planted_frame()
    ref = _put(run_paths, frame)
    man = _manifest(run_paths, _TERMS)

    # base 'a' (via class_order): equation b has the planted x1 ~ +0.8.
    _base_a_snapshot(run_paths)
    res_a = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    assert res_a.summary["base_class"] == "a"
    rows_a = _l(_d(_loaded(run_paths, coefficient_table(run_paths, _fit_ref(res_a), man)))["terms"])
    x1_b_base_a = next(
        _f(_d(r)["coef"]) for r in rows_a if _d(r)["equation"] == "b" and _d(r)["term"] == "x1"
    )
    assert x1_b_base_a > 0.5

    # base 'b' via class_order: equation 'a' relative to base 'b' is the sign-flip of the old
    # equation 'b' relative to base 'a' (a-vs-b flips with b-vs-a).
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "multiclass", "class_order": ["b", "a", "c"]}}
    )
    run_paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")
    res_b = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    assert res_b.summary["base_class"] == "b"
    rows_b = _l(_d(_loaded(run_paths, coefficient_table(run_paths, _fit_ref(res_b), man)))["terms"])
    x1_a_base_b = next(
        _f(_d(r)["coef"]) for r in rows_b if _d(r)["equation"] == "a" and _d(r)["term"] == "x1"
    )
    assert x1_a_base_b < -0.5


def test_float_coded_target_handled_through_as_str(run_paths: RunPaths) -> None:
    # B2: a 0.0/1.0/2.0 float-coded target must be named "0"/"1"/"2", not "0.0"/... .
    rng = np.random.default_rng(3)
    n = 900
    x = rng.normal(size=n)
    cls = rng.integers(0, 3, size=n).astype("float64")
    frame = pd.DataFrame({"x": x, "y": cls})
    ref = _put(run_paths, frame)
    res = fit_mnlogit(run_paths, ref, "y", ("x",))
    assert set(_l(res.summary["classes"])) == {"0", "1", "2"}
    assert res.summary["base_class"] in {"0", "1", "2"}


# --- errors -------------------------------------------------------------------------------------


def test_two_class_target_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [float(i) for i in range(10)], "y": ["a", "b"] * 5})
    ref = _put(run_paths, frame)
    with pytest.raises(ToolParamError):
        fit_mnlogit(run_paths, ref, "y", ("x",))


def _separated_frame() -> pd.DataFrame:
    # Three classes split cleanly along a continuous x: the MLE pushes coefficients to infinity
    # and `exp` overflows. Under np.errstate this is a FloatingPointError (caught -> ToolDataError);
    # with the guard removed the raw numpy RuntimeWarning (overflow) surfaces instead, which under
    # pytest's filterwarnings=error is a different, uncaught exception -- so the errstate-removal
    # DoD mutation makes these tests fail (they expect ToolDataError).
    rng = np.random.default_rng(0)
    n = 400
    x = rng.normal(size=n)
    y = np.where(x < -0.5, "a", np.where(x < 0.5, "b", "c"))
    return pd.DataFrame({"x": x, "y": y})


def test_separated_target_is_tool_data_error(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _separated_frame())
    with pytest.raises(ToolDataError, match="separation or non-convergence"):
        fit_mnlogit(run_paths, ref, "y", ("x",))


def test_mnlogit_non_convergence_production_path(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production path (no warning, converged=False): monkeypatch MNLogit.fit to return a
    non-converged result, proving the explicit `converged` check (not only the errstate guard)
    maps to ToolDataError."""
    import statsmodels.api as sm

    class _FakeResult:
        mle_retvals: ClassVar[dict[str, bool]] = {"converged": False}

    class _FakeModel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def fit(self, *args: object, **kwargs: object) -> _FakeResult:
            return _FakeResult()

    monkeypatch.setattr(sm, "MNLogit", _FakeModel)
    ref = _put(run_paths, _planted_frame(n=300))
    with pytest.raises(ToolDataError, match="separation or non-convergence"):
        fit_mnlogit(run_paths, ref, "y", _PREDICTORS)


def test_class_order_first_not_observed_is_tool_data_error(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _planted_frame(n=300))
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "multiclass", "class_order": ["zzz", "a", "b"]}}
    )
    run_paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")
    with pytest.raises(ToolDataError, match="class_order"):
        fit_mnlogit(run_paths, ref, "y", _PREDICTORS)


def test_separated_target_in_worker_thread(run_paths: RunPaths) -> None:
    """P4: np.errstate is thread-local, so the overflow guard fires in a worker thread too."""
    ref = _put(run_paths, _separated_frame())
    captured: list[Exception] = []

    def run() -> None:
        try:
            fit_mnlogit(run_paths, ref, "y", ("x",))
        except Exception as exc:  # keep the thread alive; assert on the main thread
            captured.append(exc)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert len(captured) == 1
    assert isinstance(captured[0], ToolDataError)


def test_residual_tools_refuse_mnlogit(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _planted_frame())
    res = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    fit_ref = _fit_ref(res)
    with pytest.raises(ToolParamError):
        compute_influence(run_paths, fit_ref)
    with pytest.raises(ToolParamError):
        plot_residuals(run_paths, fit_ref)
    with pytest.raises(ToolParamError):
        run_residual_tests(run_paths, fit_ref)


# --- summary ------------------------------------------------------------------------------------


def test_inferential_fit_summary_reports_pseudo_r2_and_base_class(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _planted_frame())
    _base_a_snapshot(run_paths)
    res = fit_mnlogit(run_paths, ref, "y", _PREDICTORS)
    out = inferential_fit_summary(run_paths, _fit_ref(res))
    assert "pseudo_rsquared" in out.summary
    assert "llr_pvalue" in out.summary
    assert out.summary["base_class"] == "a"


# --- helpers that touch artifacts ---------------------------------------------------------------


def _fit_ref(res: object) -> ArtifactRef:
    from statlab_contracts.envelope import ToolSuccess

    assert isinstance(res, ToolSuccess)
    return res.artifacts[0].ref


def _loaded(paths: RunPaths, res: object) -> dict[str, JsonValue]:
    from statlab_contracts.envelope import ToolSuccess
    from statlab_tools.artifacts import load_json

    assert isinstance(res, ToolSuccess)
    loaded = load_json(paths.artifact(res.artifacts[0].ref, "table"))
    return _d(loaded)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_separated_target_is_tool_data_error_in_production(run_paths: RunPaths) -> None:
    """P3, production path: outside pytest a numpy overflow is a plain warning, and without the
    `np.errstate` guard MNLogit silently returns `converged=True` with diverged coefficients.
    Ignoring `RuntimeWarning` here reproduces production, so only the guard can raise. The other
    two separation tests catch a removed guard only because `filterwarnings=error` turns the
    overflow itself into an exception; this one catches it the way production would see it."""
    ref = _put(run_paths, _separated_frame())
    with pytest.raises(ToolDataError):
        fit_mnlogit(run_paths, ref, "y", ("x",))
