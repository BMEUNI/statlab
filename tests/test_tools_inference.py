"""Chunk 15 §4: the nine statsmodels inference tools, the registry, and the end-to-end path.

The suite's spine is the NIST StRD Longley known-answer test (P1): `fit_ols` must match the
**certified** coefficients and SEs hard-coded below, never statsmodels' own output. The other
non-vacuous checks: perfect separation handled on both paths (the warning raised under pytest, and
`converged=False` via a monkeypatched result); the joint-Wald/Holm ROLE logic checked against
hand-built expectations while the arithmetic is compared to statsmodels' own `wald_test`/
`multipletests` as oracles; and the DoD mutations (reference kept, per-term p, Holm->raw,
converged check removed, exploratory->confirmatory) each failing a named test.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract, InferenceGoal
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_json, load_model, save_dataframe, save_json
from statlab_tools.registry import SPECS
from statlab_tools.tool import ToolDataError, ToolParamError, server
from statlab_tools.tools import inference as inf
from statlab_tools.tools.inference import (
    FittedInference,
    assemble_inference_matrix,
    coefficient_table,
    compute_influence,
    compute_vif,
    fit_glm,
    fit_logit,
    fit_ols,
    inferential_fit_summary,
    plot_residuals,
)
from statlab_tools.tools.inference import (
    test_residuals as run_residual_tests,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]


class _ModelProto(Protocol):
    """The one statsmodels `model` attribute the tests read."""

    exog: object


class _WaldProto(Protocol):
    """The joint-Wald result attributes the oracle test reads."""

    statistic: object
    pvalue: object


class _ResultProto(Protocol):
    """The statsmodels results attributes the known-answer and diagnostic tests read. A `Protocol`
    rather than the untyped `Results` class so the casts type-check without an `Any`."""

    params: object
    bse: object
    rsquared: float
    scale: float
    resid: object
    model: _ModelProto

    def wald_test(self, restriction: object, *, scalar: bool) -> _WaldProto: ...


# --- NIST StRD "Longley" certified values (hard-coded, never statsmodels' output) ---------------
# Column order: const, GNPDEFL, GNP, UNEMP, ARMED, POP, YEAR.
_NIST_COEF = (
    -3482258.63459582,
    15.0618722713733,
    -0.0358191792925910,
    -2.02022980381683,
    -1.03322686717359,
    -0.0511041056535807,
    1829.15146461355,
)
_NIST_SE = (
    890420.383607373,
    84.9149257747669,
    0.0334910077722432,
    0.488399681651699,
    0.214274163161675,
    0.226073200069370,
    455.478499142212,
)
_NIST_R2 = 0.995479004577296
_NIST_RESID_SD = 304.854073561965
_LONGLEY_PREDICTORS = ("GNPDEFL", "GNP", "UNEMP", "ARMED", "POP", "YEAR")


def _d(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return cast("dict[str, JsonValue]", value)


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


def _manifest_passthrough(paths: RunPaths, predictors: tuple[str, ...]) -> ArtifactRef:
    """A manifest mapping each predictor term to itself (numeric passthrough), for
    coefficient_table tests that fit numeric columns directly."""
    man: dict[str, JsonValue] = {
        "terms": {p: p for p in predictors},
        "references": {},
        "imputed": [],
        "indicators": [],
        "dropped": [],
    }
    ref = new_artifact_ref()
    save_json(paths.artifact(ref, "table"), man, ref=ref, kind="table", label="m")
    return ref


def _longley_frame() -> pd.DataFrame:
    import statsmodels.api as sm

    d = sm.datasets.longley.load_pandas()
    frame = d.exog.copy()
    frame["TOTEMP"] = d.endog.to_numpy()
    return cast("pd.DataFrame", frame)


def _fit(paths: RunPaths, ref: ArtifactRef) -> FittedInference:
    obj = load_model(paths.artifact(ref, "inferential_fit"))
    assert isinstance(obj, FittedInference)
    return obj


def _res(fitted: FittedInference) -> _ResultProto:
    """The fitted statsmodels results object, narrowed to a small `Protocol` of the attributes the
    tests read. statsmodels is untyped (`ignore_missing_imports`), so `cast`-ing to a typed
    Protocol lets a test read `.params`/`.resid`/`.wald_test` without an `object`-has-no-attribute
    error and without an `Any` -- the same narrow-at-the-boundary posture as the production
    module."""
    return cast("_ResultProto", fitted.result)


# --- known answer: NIST Longley (P1) -------------------------------------------------------------


def test_fit_ols_matches_nist_longley(run_paths: RunPaths) -> None:
    """fit_ols on statsmodels' Longley data matches the NIST **certified** coefficients, SEs and
    R² to relative 1e-8. If this fails beyond 1e-8, STOP -- do not edit the certified values."""
    ref = _put(run_paths, _longley_frame())
    res = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS)
    fitted = _fit(run_paths, res.artifacts[0].ref)
    result = _res(fitted)
    params = np.asarray(result.params, dtype="float64")
    bse = np.asarray(result.bse, dtype="float64")
    for got, cert in zip(params, _NIST_COEF, strict=True):
        assert abs((got - cert) / cert) < 1e-8
    for got, cert in zip(bse, _NIST_SE, strict=True):
        assert abs((got - cert) / cert) < 1e-8
    assert abs((float(result.rsquared) - _NIST_R2) / _NIST_R2) < 1e-8
    assert abs((float(result.scale) ** 0.5 - _NIST_RESID_SD) / _NIST_RESID_SD) < 1e-8


def test_fit_ols_summary_shape(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _longley_frame())
    res = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS)
    assert res.summary["model"] == "ols"
    assert res.summary["n_params"] == 7  # const + 6
    assert res.summary["converged"] is True
    assert res.artifacts[0].kind == "inferential_fit"


# --- known answer: Logit 2x2 (P2) ---------------------------------------------------------------


def _logit_2x2_frame() -> pd.DataFrame:
    # x=0: 10 events / 30 non-events; x=1: 30 events / 10 non-events.
    y = [1.0] * 10 + [0.0] * 30 + [1.0] * 30 + [0.0] * 10
    x = [0.0] * 40 + [1.0] * 40
    return pd.DataFrame({"x": x, "y": y})


def test_fit_logit_known_log_odds_and_odds_ratio(run_paths: RunPaths) -> None:
    """P2: a hand-built 2x2 reproduces the exact log-odds (const) and log odds-ratio (slope)."""
    ref = _put(run_paths, _logit_2x2_frame())
    res = fit_logit(run_paths, ref, "y", ("x",))
    fitted = _fit(run_paths, res.artifacts[0].ref)
    params = np.asarray(_res(fitted).params, dtype="float64")
    assert abs(params[0] - math.log(10 / 30)) < 1e-9  # log odds at x=0
    assert abs(params[1] - math.log((30 / 10) / (10 / 30))) < 1e-9  # log OR = log(9)


# --- known answer: Poisson GLM ------------------------------------------------------------------


def test_fit_glm_poisson_recovers_coefficients(run_paths: RunPaths) -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=4000)
    y = rng.poisson(np.exp(0.5 + 0.3 * x)).astype(float)
    ref = _put(run_paths, pd.DataFrame({"x": x, "y": y}))
    res = fit_glm(run_paths, ref, "y", "poisson", ("x",))
    fitted = _fit(run_paths, res.artifacts[0].ref)
    result = _res(fitted)
    params = np.asarray(result.params, dtype="float64")
    bse = np.asarray(result.bse, dtype="float64")
    assert abs(params[0] - 0.5) < 3 * bse[0]
    assert abs(params[1] - 0.3) < 3 * bse[1]
    assert res.summary["model"] == "glm_poisson"


def test_fit_glm_poisson_rejects_non_count(run_paths: RunPaths) -> None:
    ref = _put(run_paths, pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [0.5, 1.5, 2.0, 3.0]}))
    with pytest.raises(ToolParamError, match="non-negative integer"):
        fit_glm(run_paths, ref, "y", "poisson", ("x",))


def test_fit_glm_gamma_requires_positive(run_paths: RunPaths) -> None:
    ref = _put(run_paths, pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [1.0, -2.0, 3.0, 4.0]}))
    with pytest.raises(ToolParamError, match="strictly positive"):
        fit_glm(run_paths, ref, "y", "gamma", ("x",))


def test_fit_glm_gamma_fits_without_warning(run_paths: RunPaths) -> None:
    """Gamma's canonical InversePower link raises DomainWarning under -W error; the log link does
    not. This fit must succeed (if the canonical link were used it would raise)."""
    rng = np.random.default_rng(1)
    x = rng.normal(size=500)
    y = np.exp(0.5 + 0.3 * x + rng.normal(scale=0.1, size=500))
    ref = _put(run_paths, pd.DataFrame({"x": x, "y": y}))
    res = fit_glm(run_paths, ref, "y", "gamma", ("x",))
    assert res.summary["model"] == "glm_gamma"
    assert res.summary["converged"] is True


# --- fit guards ---------------------------------------------------------------------------------


def test_fit_ols_too_few_observations_is_data(run_paths: RunPaths) -> None:
    # 3 rows, 2 predictors + const = 3 params; n <= n_params + 1 -> ToolDataError.
    ref = _put(
        run_paths, pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0], "y": [1.0, 2.0, 3.0]})
    )
    with pytest.raises(ToolDataError, match="too few observations"):
        fit_ols(run_paths, ref, "y", ("a", "b"))


def test_fit_ols_non_numeric_predictor_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths,
        pd.DataFrame({"g": ["a", "b", "c", "d", "e"], "y": [1.0, 2.0, 3.0, 4.0, 5.0]}),
    )
    with pytest.raises(ToolParamError, match="assemble_inference_matrix"):
        fit_ols(run_paths, ref, "y", ("g",))


def test_fit_ols_nan_predictor_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths,
        pd.DataFrame({"a": [1.0, np.nan, 3.0, 4.0, 5.0], "y": [1.0, 2.0, 3.0, 4.0, 5.0]}),
    )
    with pytest.raises(ToolParamError, match="missing values"):
        fit_ols(run_paths, ref, "y", ("a",))


def test_fit_logit_requires_two_levels(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths,
        pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": [0, 1, 2, 0, 1, 2]}),
    )
    with pytest.raises(ToolParamError, match="exactly two levels"):
        fit_logit(run_paths, ref, "y", ("x",))


def test_fit_logit_positive_class_from_snapshot(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], "y": ["no", "yes"] * 4})
    ref = _put(run_paths, frame)
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": "no"}}
    )
    run_paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")
    res = fit_logit(run_paths, ref, "y", ("x",))
    assert res.summary["positive_class"] == "no"  # snapshot overrides the default "yes"
    assert res.summary["positive_class_source"] == "contract"


def test_fit_logit_default_positive_is_second_string_level(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], "y": ["no", "yes"] * 4})
    ref = _put(run_paths, frame)
    res = fit_logit(run_paths, ref, "y", ("x",))
    assert res.summary["positive_class"] == "yes"  # second in string order
    assert res.summary["positive_class_source"] == "default"


# --- perfect separation (P3): both paths ---------------------------------------------------------


def _separated_frame() -> pd.DataFrame:
    x = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
    y = [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
    return pd.DataFrame({"x": x, "y": y})


def test_perfect_separation_raises_under_pytest(run_paths: RunPaths) -> None:
    """Under pytest's filterwarnings=error, statsmodels' PerfectSeparationWarning is raised as an
    exception; fit_logit catches it and maps it to a ToolDataError."""
    ref = _put(run_paths, _separated_frame())
    with pytest.raises(ToolDataError, match="perfect separation or non-convergence"):
        fit_logit(run_paths, ref, "y", ("x",))


def test_non_convergence_production_path(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production path (no warning raised, converged=False): monkeypatch Logit.fit to return a
    non-converged result, proving the explicit `converged` check -- not only the warning catch --
    maps to ToolDataError. This is the path a non-pytest process (statsmodels' own "always"
    filter) takes."""
    import statsmodels.api as sm

    class _FakeResult:
        mle_retvals: ClassVar[dict[str, bool]] = {"converged": False}

    class _FakeModel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def fit(self, *args: object, **kwargs: object) -> _FakeResult:
            return _FakeResult()

    monkeypatch.setattr(sm, "Logit", _FakeModel)
    ref = _put(run_paths, _separated_frame())
    with pytest.raises(ToolDataError, match="perfect separation or non-convergence"):
        fit_logit(run_paths, ref, "y", ("x",))


def test_remove_converged_check_mutation_guard(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: remove the `converged` check. With the monkeypatched non-converged result and
    no warning, only the explicit check catches it -- this test fails if the check is removed."""
    import statsmodels.api as sm

    class _FakeResult:
        mle_retvals: ClassVar[dict[str, bool]] = {"converged": False}

    class _FakeModel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def fit(self, *args: object, **kwargs: object) -> _FakeResult:
            return _FakeResult()

    monkeypatch.setattr(sm, "Logit", _FakeModel)
    ref = _put(run_paths, _separated_frame())
    with pytest.raises(ToolDataError):
        fit_logit(run_paths, ref, "y", ("x",))


# --- coefficient_table: roles, joint Wald, Holm (D-e, D35) ---------------------------------------


def _three_level_frame(n: int = 120) -> tuple[pd.DataFrame, ArtifactRef]:
    """A train/valid assembled matrix with a numeric predictor `age` and a 3-level `plan`, so a
    goal on `plan` is a joint test over two dummies (df == 2)."""
    rng = np.random.default_rng(7)
    plan = rng.choice(["basic", "standard", "premium"], size=n)
    age = rng.normal(50, 10, size=n)
    churn = (age > 50).astype(float) + rng.normal(0, 0.1, size=n)
    frame = pd.DataFrame({"plan": plan, "age": age, "churn": churn})
    return frame, ArtifactRef("")  # placeholder


def _assembled(
    run_paths: RunPaths, frame: pd.DataFrame, predictors: tuple[str, ...]
) -> tuple[ArtifactRef, ArtifactRef]:
    """Split a frame into disjoint train/valid and assemble a matrix; return (matrix, manifest)."""
    half = len(frame) // 2
    train = frame.iloc[:half].copy()
    valid = frame.iloc[half:].copy()
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), frame.columns[-1], predictors
    )
    return res.artifacts[0].ref, res.artifacts[1].ref


def test_roles_confirmatory_exploratory_intercept(run_paths: RunPaths) -> None:
    frame, _ = _three_level_frame()
    matrix, manifest = _assembled(run_paths, frame, ("plan", "age"))
    fit_res = fit_ols(run_paths, matrix, "churn")
    goal = InferenceGoal(question="does plan affect churn", predictors=("plan",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_res.artifacts[0].ref, manifest, (goal,))
    payload = _d(load_json(run_paths.artifact(ct.artifacts[0].ref, "table")))
    roles = {_d(r)["term"]: _d(r)["role"] for r in _l(payload["terms"])}
    assert roles["const"] == "intercept"
    # plan dummies are confirmatory (source column `plan` is in the goal)
    assert all(roles[t] == "confirmatory" for t in roles if str(t).startswith("plan__"))
    # age is exploratory
    assert roles["age"] == "exploratory"


def test_exploratory_terms_carry_p_note(run_paths: RunPaths) -> None:
    frame, _ = _three_level_frame()
    matrix, manifest = _assembled(run_paths, frame, ("plan", "age"))
    fit_res = fit_ols(run_paths, matrix, "churn")
    goal = InferenceGoal(question="plan?", predictors=("plan",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_res.artifacts[0].ref, manifest, (goal,))
    payload = _d(load_json(run_paths.artifact(ct.artifacts[0].ref, "table")))
    age_row = next(_d(r) for r in _l(payload["terms"]) if _d(r)["term"] == "age")
    assert age_row["p_note"] == "not pre-specified"


def test_exploratory_labelled_confirmatory_mutation_guard(run_paths: RunPaths) -> None:
    """DoD mutation: exploratory terms are labelled confirmatory. `age` is NOT in the goal, so it
    must be exploratory; a mutation labelling everything confirmatory fails this."""
    frame, _ = _three_level_frame()
    matrix, manifest = _assembled(run_paths, frame, ("plan", "age"))
    fit_res = fit_ols(run_paths, matrix, "churn")
    goal = InferenceGoal(question="plan?", predictors=("plan",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_res.artifacts[0].ref, manifest, (goal,))
    assert _f(ct.summary["n_confirmatory"]) == 2  # the two plan dummies only, not age


def test_three_level_goal_is_one_joint_test_df_two(run_paths: RunPaths) -> None:
    """A 3-level predictor is two dummies; the goal is ONE joint Wald test with df == 2, and its p
    equals statsmodels' wald_test on the same restriction (oracle for the arithmetic)."""
    frame, _ = _three_level_frame()
    matrix, manifest = _assembled(run_paths, frame, ("plan", "age"))
    fit_ref = fit_ols(run_paths, matrix, "churn").artifacts[0].ref
    fitted = _fit(run_paths, fit_ref)
    goal = InferenceGoal(question="plan?", predictors=("plan",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_ref, manifest, (goal,))
    row = _d(_l(ct.summary["goals"])[0])
    assert row["df"] == 2
    assert len(_l(row["terms"])) == 2
    # oracle: build the same restriction and call wald_test directly
    plan_idx = [i for i, t in enumerate(fitted.terms) if t.startswith("plan__")]
    restriction = np.zeros((len(plan_idx), len(fitted.terms)))
    for r, c in enumerate(plan_idx):
        restriction[r, c] = 1.0
    oracle = _res(fitted).wald_test(restriction, scalar=True)
    assert abs(_f(row["p_raw"]) - _f(oracle.pvalue)) < 1e-10


def test_per_term_p_mutation_guard(run_paths: RunPaths) -> None:
    """DoD mutation: per-term p-values used instead of a joint Wald test. A single per-term p would
    have df 1; the joint test has df 2 for a 3-level predictor. This asserts df == 2."""
    frame, _ = _three_level_frame()
    matrix, manifest = _assembled(run_paths, frame, ("plan", "age"))
    fit_ref = fit_ols(run_paths, matrix, "churn").artifacts[0].ref
    goal = InferenceGoal(question="plan?", predictors=("plan",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_ref, manifest, (goal,))
    assert _d(_l(ct.summary["goals"])[0])["df"] == 2


def _multi_goal_frame(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    a = rng.normal(size=n)
    b = rng.normal(size=n)
    c = rng.normal(size=n)
    y = 2.0 * a + 0.0 * b + 0.5 * c + rng.normal(scale=0.5, size=n)
    return pd.DataFrame({"a": a, "b": b, "c": c, "y": y})


def test_holm_across_three_goals_matches_multipletests(run_paths: RunPaths) -> None:
    """Holm-adjusted p across 3 goals equals statsmodels' multipletests(method='holm') on the raw
    p-values (oracle for the arithmetic)."""
    frame = _multi_goal_frame()
    matrix, manifest = _assembled(run_paths, frame, ("a", "b", "c"))
    fit_ref = fit_ols(run_paths, matrix, "y").artifacts[0].ref
    goals = (
        InferenceGoal(question="a?", predictors=("a",), alpha=0.05),
        InferenceGoal(question="b?", predictors=("b",), alpha=0.05),
        InferenceGoal(question="c?", predictors=("c",), alpha=0.05),
    )
    ct = coefficient_table(run_paths, fit_ref, manifest, goals)
    rows = [_d(r) for r in _l(ct.summary["goals"])]
    raw = [_f(r["p_raw"]) for r in rows]
    from statsmodels.stats.multitest import multipletests

    _, adjusted, _, _ = multipletests(raw, method="holm")
    for row, adj in zip(rows, adjusted, strict=True):
        assert abs(_f(row["p_holm"]) - float(adj)) < 1e-12


def test_reject_uses_adjusted_p(run_paths: RunPaths) -> None:
    """`reject` uses the Holm-adjusted p, not the raw p (D35). Construct a case where a goal's raw
    p < alpha but its adjusted p >= alpha, and assert `reject` is False."""
    frame = _multi_goal_frame()
    matrix, manifest = _assembled(run_paths, frame, ("a", "b", "c"))
    fit_ref = fit_ols(run_paths, matrix, "y").artifacts[0].ref
    goals = (
        InferenceGoal(question="a?", predictors=("a",), alpha=0.05),
        InferenceGoal(question="b?", predictors=("b",), alpha=0.05),
        InferenceGoal(question="c?", predictors=("c",), alpha=0.05),
    )
    ct = coefficient_table(run_paths, fit_ref, manifest, goals)
    for row in (_d(r) for r in _l(ct.summary["goals"])):
        reject = row["reject"]
        p_holm = _f(row["p_holm"])
        alpha = _f(row["alpha"])
        assert reject == (p_holm < alpha)


def _holm_divergent_frame() -> pd.DataFrame:
    """Three weak effects (seed 3, n=120) engineered so goal `c` has raw p < 0.05 <= Holm p: its
    correct `reject` is False (on the adjusted p) but a raw-p implementation would set it True."""
    rng = np.random.default_rng(3)
    n = 120
    a = rng.normal(size=n)
    b = rng.normal(size=n)
    c = rng.normal(size=n)
    y = 0.35 * a + 0.3 * b + 0.25 * c + rng.normal(scale=1.0, size=n)
    return pd.DataFrame({"a": a, "b": b, "c": c, "y": y})


def test_holm_to_raw_mutation_guard(run_paths: RunPaths) -> None:
    """DoD mutation: Holm replaced by raw p for `reject`. On the seed-3 frame, goal `c` has
    raw p < 0.05 <= Holm p, so the correct `reject` is False; a raw-p implementation would report
    True. This asserts the goal with raw<alpha<=holm rejects False (fails under the mutation)."""
    frame = _holm_divergent_frame()
    matrix, manifest = _assembled(run_paths, frame, ("a", "b", "c"))
    fit_ref = fit_ols(run_paths, matrix, "y").artifacts[0].ref
    goals = (
        InferenceGoal(question="a?", predictors=("a",), alpha=0.05),
        InferenceGoal(question="b?", predictors=("b",), alpha=0.05),
        InferenceGoal(question="c?", predictors=("c",), alpha=0.05),
    )
    ct = coefficient_table(run_paths, fit_ref, manifest, goals)
    rows = {_d(r)["question"]: _d(r) for r in _l(ct.summary["goals"])}
    c_row = rows["c?"]
    assert _f(c_row["p_raw"]) < 0.05  # raw says reject
    assert _f(c_row["p_holm"]) >= 0.05  # Holm says do not
    assert c_row["reject"] is False  # decision is on Holm; the mutation would make this True


def test_single_goal_has_no_holm(run_paths: RunPaths) -> None:
    frame = _multi_goal_frame()
    matrix, manifest = _assembled(run_paths, frame, ("a", "b", "c"))
    fit_ref = fit_ols(run_paths, matrix, "y").artifacts[0].ref
    goal = InferenceGoal(question="a?", predictors=("a",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_ref, manifest, (goal,))
    row = _d(_l(ct.summary["goals"])[0])
    assert row["p_holm"] is None
    assert row["reject"] == (_f(row["p_raw"]) < _f(row["alpha"]))  # falls back to raw


def test_goal_on_dropped_column_is_untestable(run_paths: RunPaths) -> None:
    """A goal whose predictor was dropped (collinear/constant) or never a predictor is
    `untestable` and excluded from Holm."""
    frame = _multi_goal_frame()
    matrix, manifest = _assembled(run_paths, frame, ("a", "b"))  # c is not a predictor
    fit_ref = fit_ols(run_paths, matrix, "y").artifacts[0].ref
    goals = (
        InferenceGoal(question="a?", predictors=("a",), alpha=0.05),
        InferenceGoal(question="c (absent)?", predictors=("c",), alpha=0.05),
    )
    ct = coefficient_table(run_paths, fit_ref, manifest, goals)
    rows = {_d(r)["question"]: _d(r) for r in _l(ct.summary["goals"])}
    assert rows["c (absent)?"]["status"] == "untestable"
    assert rows["c (absent)?"]["p_holm"] is None
    assert rows["c (absent)?"]["reject"] is False
    # the one testable goal gets NO holm column (only one testable goal -> no correction)
    assert rows["a?"]["p_holm"] is None


# --- diagnostics --------------------------------------------------------------------------------


def test_vif_flags_longley_collinear_columns(run_paths: RunPaths) -> None:
    """VIF on Longley flags GNP, POP and YEAR (all above 10)."""
    ref = _put(run_paths, _longley_frame())
    res = compute_vif(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS)
    flagged = {str(x) for x in _l(res.summary["flagged"])}
    assert {"GNP", "POP", "YEAR"} <= flagged
    assert "const" not in _d(res.summary["vif"])  # const excluded from output


def test_test_residuals_non_ols_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _logit_2x2_frame())
    fit_ref = fit_logit(run_paths, ref, "y", ("x",)).artifacts[0].ref
    with pytest.raises(ToolParamError, match="OLS only"):
        run_residual_tests(run_paths, fit_ref)


def test_test_residuals_match_statsmodels(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _longley_frame())
    fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    fitted = _fit(run_paths, fit_ref)
    res = run_residual_tests(run_paths, fit_ref)
    tests = _d(res.summary["tests"])
    from statsmodels.stats.diagnostic import het_breuschpagan
    from statsmodels.stats.stattools import durbin_watson, jarque_bera

    result = _res(fitted)
    resid = np.asarray(result.resid, dtype="float64")
    jb = jarque_bera(resid)
    assert abs(_f(_d(tests["normality"])["statistic"]) - float(jb[0])) < 1e-9
    bp = het_breuschpagan(resid, np.asarray(result.model.exog, dtype="float64"))
    assert abs(_f(_d(tests["heteroskedasticity"])["lm"]) - float(bp[0])) < 1e-9
    assert abs(_f(_d(tests["autocorrelation"])["statistic"]) - float(durbin_watson(resid))) < 1e-9
    assert "p" not in _d(tests["autocorrelation"])  # durbin-watson has no p


def test_influence_top_sorted_by_cooks(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _longley_frame())
    fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    res = compute_influence(run_paths, fit_ref)
    top = [_d(r) for r in _l(res.summary["top"])]
    cooks = [_f(r["cooks"]) for r in top]
    assert cooks == sorted(cooks, reverse=True)
    assert len(top) <= inf.INFLUENCE_TOP_K
    # labels are the matrix row positions
    assert all(isinstance(r["label"], int) for r in top)


def test_influence_has_dffits_for_ols_not_logit(run_paths: RunPaths) -> None:
    ols_ref = _put(run_paths, _longley_frame())
    ols_fit = fit_ols(run_paths, ols_ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    ols_res = compute_influence(run_paths, ols_fit)
    ols_table = _d(load_json(run_paths.artifact(ols_res.artifacts[0].ref, "table")))
    assert "dffits" in _d(_l(ols_table["rows"])[0])

    logit_ref = _put(run_paths, _logit_2x2_frame())
    logit_fit = fit_logit(run_paths, logit_ref, "y", ("x",)).artifacts[0].ref
    logit_res = compute_influence(run_paths, logit_fit)
    logit_table = _d(load_json(run_paths.artifact(logit_res.artifacts[0].ref, "table")))
    assert "dffits" not in _d(_l(logit_table["rows"])[0])


@pytest.mark.parametrize("model", ["ols", "logit", "poisson"])
def test_plot_residuals_renders_for_every_model(run_paths: RunPaths, model: str) -> None:
    if model == "ols":
        ref = _put(run_paths, _longley_frame())
        fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    elif model == "logit":
        ref = _put(run_paths, _logit_2x2_frame())
        fit_ref = fit_logit(run_paths, ref, "y", ("x",)).artifacts[0].ref
    else:
        rng = np.random.default_rng(0)
        x = rng.normal(size=200)
        y = rng.poisson(np.exp(0.5 + 0.3 * x)).astype(float)
        ref = _put(run_paths, pd.DataFrame({"x": x, "y": y}))
        fit_ref = fit_glm(run_paths, ref, "y", "poisson", ("x",)).artifacts[0].ref
    res = plot_residuals(run_paths, fit_ref)
    assert res.artifacts[0].kind == "plot"
    assert run_paths.artifact(res.artifacts[0].ref, "plot").exists()


# --- inferential_fit_summary --------------------------------------------------------------------


def test_fit_summary_ols_fields(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _longley_frame())
    fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    res = inferential_fit_summary(run_paths, fit_ref)
    assert abs(_f(res.summary["rsquared"]) - _NIST_R2) < 1e-8
    assert "rsquared_adj" in res.summary
    assert "f_statistic" in res.summary and "f_pvalue" in res.summary


def test_fit_summary_logit_fields(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _logit_2x2_frame())
    fit_ref = fit_logit(run_paths, ref, "y", ("x",)).artifacts[0].ref
    res = inferential_fit_summary(run_paths, fit_ref)
    assert "pseudo_rsquared" in res.summary
    assert "llr" in res.summary and "llr_pvalue" in res.summary


def test_fit_summary_glm_fields(run_paths: RunPaths) -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = rng.poisson(np.exp(0.5 + 0.3 * x)).astype(float)
    ref = _put(run_paths, pd.DataFrame({"x": x, "y": y}))
    fit_ref = fit_glm(run_paths, ref, "y", "poisson", ("x",)).artifacts[0].ref
    res = inferential_fit_summary(run_paths, fit_ref)
    assert "pseudo_rsquared" in res.summary and "deviance" in res.summary


# --- registry -----------------------------------------------------------------------------------


def test_specs_names_and_target_types() -> None:
    assert set(SPECS) == {"ols", "logit", "glm_poisson", "glm_gamma"}
    assert SPECS["ols"].target_types == ("continuous",)
    assert SPECS["logit"].target_types == ("binary",)
    assert SPECS["glm_poisson"].target_types == ("count",)
    assert SPECS["glm_gamma"].target_types == ("continuous",)
    assert SPECS["glm_poisson"].family == "poisson"


def test_every_spec_tool_is_registered() -> None:
    registered = {t.name for t in server._tool_manager.list_tools()}
    for spec in SPECS.values():
        assert spec.tool in registered


def test_list_estimators_reports_inferential_models(run_paths: RunPaths) -> None:
    from statlab_tools.tools.training import list_estimators

    res = list_estimators()
    inferential = _d(res.summary["inferential_models"])
    assert set(inferential) == {"ols", "logit", "glm_poisson", "glm_gamma"}
    assert _d(inferential["ols"])["tool"] == "fit_ols"


# --- strict JSON + end to end -------------------------------------------------------------------


def test_results_survive_strict_json(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _longley_frame())
    fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    manifest = _manifest_passthrough(run_paths, _LONGLEY_PREDICTORS)
    goal = InferenceGoal(question="GNP?", predictors=("GNP",), alpha=0.05)
    import json

    for res in (
        coefficient_table(run_paths, fit_ref, manifest, (goal,)),
        inferential_fit_summary(run_paths, fit_ref),
        compute_vif(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS),
        run_residual_tests(run_paths, fit_ref),
        compute_influence(run_paths, fit_ref),
    ):
        json.dumps(res.summary, allow_nan=False)  # raises if any NaN/inf leaked


def test_end_to_end_churn_plan_to_churned(run_paths: RunPaths) -> None:
    """End-to-end: assemble_inference_matrix(train, valid) -> fit_logit -> coefficient_table with a
    goal `plan -> churned` gives one confirmatory joint test with df == 2."""
    rng = np.random.default_rng(42)
    n = 160
    plan = rng.choice(["basic", "standard", "premium"], size=n)
    age = rng.normal(50, 10, size=n)
    churned = rng.integers(0, 2, size=n)
    frame = pd.DataFrame({"plan": plan, "age": age, "churned": churned})
    train = frame.iloc[:120].copy()
    valid = frame.iloc[120:].copy()
    asm = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "churned", ("plan", "age")
    )
    matrix, manifest = asm.artifacts[0].ref, asm.artifacts[1].ref
    fit_ref = fit_logit(run_paths, matrix, "churned").artifacts[0].ref
    goal = InferenceGoal(question="does plan affect churn", predictors=("plan",), alpha=0.05)
    ct = coefficient_table(run_paths, fit_ref, manifest, (goal,))
    row = _d(_l(ct.summary["goals"])[0])
    assert row["status"] == "tested"
    assert row["df"] == 2
    assert _f(ct.summary["n_confirmatory"]) == 2  # the two plan dummies


# --- error-branch coverage ----------------------------------------------------------------------


def test_coefficient_table_rejects_non_manifest(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _longley_frame())
    fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    bad = new_artifact_ref()
    save_json(run_paths.artifact(bad, "table"), {"not": "a manifest"}, ref=bad, kind="table")
    with pytest.raises(ToolParamError, match="not an inference matrix manifest"):
        coefficient_table(run_paths, fit_ref, bad)


def test_fit_target_in_predictors_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths, pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [1.0, 2.0, 3.0, 4.0, 5.0]})
    )
    with pytest.raises(ToolParamError, match="must not be a predictor"):
        fit_ols(run_paths, ref, "y", ("y",))


def test_fit_target_not_in_matrix_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths, pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [1.0, 2.0, 3.0, 4.0, 5.0]})
    )
    with pytest.raises(ToolParamError, match="not in the matrix"):
        fit_ols(run_paths, ref, "missing_target", ("a",))


def test_fit_ols_non_numeric_target_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths,
        pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "y": ["p", "q", "p", "q", "p"]}),
    )
    with pytest.raises(ToolParamError, match="OLS needs a numeric target"):
        fit_ols(run_paths, ref, "y", ("a",))


def test_fit_glm_non_numeric_target_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(
        run_paths,
        pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0, 5.0], "y": ["p", "q", "p", "q", "p"]}),
    )
    with pytest.raises(ToolParamError, match="GLM needs a numeric target"):
        fit_glm(run_paths, ref, "y", "poisson", ("a",))


def test_fit_logit_snapshot_positive_class_unmatched_is_tool_data(run_paths: RunPaths) -> None:
    """D-f (review correction d): when the snapshot's positive_class is neither observed level,
    fit_logit raises a data error naming the value -- never a silent fallback to the second string
    level, which would record the wrong positive class."""
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], "y": ["no", "yes"] * 4})
    ref = _put(run_paths, frame)
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": "maybe"}}
    )
    run_paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")
    with pytest.raises(ToolDataError, match="maybe"):
        fit_logit(run_paths, ref, "y", ("x",))


def test_load_fit_rejects_wrong_artifact(run_paths: RunPaths) -> None:
    """A non-FittedInference artifact passed where a fit is expected is tool_param."""
    from statlab_tools.artifacts import save_model

    bad = new_artifact_ref()
    save_model(
        run_paths.artifact(bad, "inferential_fit"), [1, 2, 3], ref=bad, kind="inferential_fit"
    )
    with pytest.raises(ToolParamError, match="not a fitted inference model"):
        inferential_fit_summary(run_paths, bad)


def test_test_residuals_subset(run_paths: RunPaths) -> None:
    """Residual-test subsets exercise every skip branch: an empty request skips all three blocks,
    and a single-test request runs exactly one."""
    ref = _put(run_paths, _longley_frame())
    fit_ref = fit_ols(run_paths, ref, "TOTEMP", _LONGLEY_PREDICTORS).artifacts[0].ref
    empty = run_residual_tests(run_paths, fit_ref, ())
    assert _d(empty.summary["tests"]) == {}  # all three skipped
    one = run_residual_tests(run_paths, fit_ref, ("heteroskedasticity",))
    assert set(_d(one.summary["tests"])) == {"heteroskedasticity"}


# --- MCP round-trip (heavy) ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fit_ols_over_mcp(
    mcp_session: SessionFactory, run_paths_over_mcp: str, tool_settings: object
) -> None:
    """One MCP round-trip of a heavy inference tool: fit_ols on a frame staged under the run."""
    from statlab_tools.settings import get_tool_settings

    settings = get_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    ref = _put(paths, _longley_frame())
    async with mcp_session() as session:
        result = await session.call_tool(
            "fit_ols",
            {
                "project": "proj",
                "run": run_paths_over_mcp,
                "matrix": ref,
                "target": "TOTEMP",
                "predictors": list(_LONGLEY_PREDICTORS),
            },
        )
    assert not result.is_error
    assert result.structured_content is not None
    envelope = result.structured_content["result"]
    assert envelope["ok"] is True
    assert envelope["summary"]["model"] == "ols"
