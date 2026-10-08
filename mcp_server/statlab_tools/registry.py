"""The predictive model registry (D6, D25): `ESTIMATORS` -- six estimators, three per task --
and `SCORERS`, the map from our metric vocabulary to sklearn scorers. `PLAN.md` §6.4 names it
`registry.ESTIMATORS`; adding an estimator is one `EstimatorEntry` (§6.2).

sklearn is untyped here (`ignore_missing_imports`), so every estimator and scorer it produces is
an implicit dynamic value. Two rules keep that dynamic type out of the module's typed surface (P6):
every `build` returns `object`, and every `SCORERS` value is `str | object`. A caller narrows at
the boundary -- the dynamic type never escapes. The import is lazy, inside the functions, for the
same reason `encoding.py` defers it: `tools/__init__.py` imports every tool module at package init,
and sklearn pulls scipy; building the registry only when a training tool actually runs keeps chunk
09's scipy-free spearman invariant (`test_s11`).

sklearn facts this module encodes (verified under `-W error`, the spec's §1):
`LogisticRegression(max_iter=1000)` so it converges without a `ConvergenceWarning` (D-e, P4);
the forests are built with `n_jobs=1` (D17); tool loops use `parallel_jobs` threads under
joblib's threading backend, and `train_estimator` fits an uncalibrated forest on `fit_threads()`
threads (addendum 50); `LinearRegression`
has no `random_state` (P5), so its `build` ignores the seed; `make_scorer(..., zero_division=0)`
on precision/recall/f1 so a zero-division `UndefinedMetricWarning` does not fail scoring (P4);
`neg_brier_score` is binary-only, so a multiclass `brier` is refused by the caller.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from statlab_contracts.vocabulary import ClassWeight, Metric, ParamValue, TargetType, Task

# A grid key maps to the tuple of values to try for it. Values are `ParamValue` (the contract's
# hyperparameter type): scalars or a flat tuple, which is all sklearn params ever are.
Grid = dict[str, tuple[ParamValue, ...]]


@dataclass(frozen=True)
class EstimatorEntry:
    """One registry line (§3.1). `build(seed, class_weight)` returns an unfitted estimator typed
    `object`: sklearn's constructor result is a dynamic value, and returning it as `object` keeps
    that dynamic type from leaking out -- the training tool narrows it only enough to call
    documented methods (`fit`, `predict`, `get_params`), never widening it. `handles_missing` and
    `supports_class_weight` gate the two P2/§3.1 pre-checks the training tool runs before a fit.
    `interpretable` is true only for the two linear models (`logistic_regression`,
    `linear_regression`); `rank_models` (chunk 14 §3.5) reads it to flag a non-interpretable
    estimator ineligible when the contract requires interpretability (D-g)."""

    task: Task
    family: Literal["linear", "random_forest", "hist_gradient_boosting"]
    build: Callable[[int, ClassWeight], object]
    default_grid: Grid
    handles_missing: bool
    supports_class_weight: bool
    interpretable: bool


def _class_weight_arg(class_weight: ClassWeight) -> str | None:
    """`"none"` -> sklearn's `None`; `"balanced"` -> `"balanced"` (§3.1). The caller has already
    refused `"balanced"` on an entry without `supports_class_weight`, so this is a pure mapping."""
    return None if class_weight == "none" else "balanced"


def _build_estimators() -> dict[str, EstimatorEntry]:
    """Construct the six-entry registry. Lazy sklearn import (see the module docstring)."""
    from sklearn.ensemble import (
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge

    _grid_rf: Grid = {"max_depth": (None, 5, 10), "min_samples_leaf": (1, 5)}
    _grid_hgb: Grid = {"learning_rate": (0.05, 0.1), "max_leaf_nodes": (15, 31)}

    def logistic_regression(seed: int, class_weight: ClassWeight) -> object:
        return LogisticRegression(
            max_iter=1000, random_state=seed, class_weight=_class_weight_arg(class_weight)
        )

    def random_forest_classifier(seed: int, class_weight: ClassWeight) -> object:
        return RandomForestClassifier(
            n_estimators=200,
            n_jobs=1,
            random_state=seed,
            class_weight=_class_weight_arg(class_weight),
        )

    def hist_gb_classifier(seed: int, class_weight: ClassWeight) -> object:
        return HistGradientBoostingClassifier(
            random_state=seed, class_weight=_class_weight_arg(class_weight)
        )

    def linear_regression(seed: int, class_weight: ClassWeight) -> object:
        # LinearRegression has no random_state (P5): the seed is accepted and ignored, so the
        # build signature stays uniform across the registry.
        return LinearRegression()

    def ridge(seed: int, class_weight: ClassWeight) -> object:
        # A second linear, interpretable regressor (D-d, chunk 20r). Ridge takes random_state
        # (used only by the "sag"/"saga" solvers; harmless and seeded for uniformity) and
        # supports no class_weight. coefficient_importance reads its coef_ unchanged.
        return Ridge(alpha=1.0, random_state=seed)

    def random_forest_regressor(seed: int, class_weight: ClassWeight) -> object:
        return RandomForestRegressor(n_estimators=200, n_jobs=1, random_state=seed)

    def hist_gb_regressor(seed: int, class_weight: ClassWeight) -> object:
        return HistGradientBoostingRegressor(random_state=seed)

    return {
        "logistic_regression": EstimatorEntry(
            task="classification",
            family="linear",
            build=logistic_regression,
            default_grid={"C": (0.1, 1.0, 10.0)},
            handles_missing=False,
            supports_class_weight=True,
            interpretable=True,
        ),
        "random_forest_classifier": EstimatorEntry(
            task="classification",
            family="random_forest",
            build=random_forest_classifier,
            default_grid=_grid_rf,
            handles_missing=True,
            supports_class_weight=True,
            interpretable=False,
        ),
        "hist_gb_classifier": EstimatorEntry(
            task="classification",
            family="hist_gradient_boosting",
            build=hist_gb_classifier,
            default_grid=_grid_hgb,
            handles_missing=True,
            supports_class_weight=True,
            interpretable=False,
        ),
        "linear_regression": EstimatorEntry(
            task="regression",
            family="linear",
            build=linear_regression,
            default_grid={},
            handles_missing=False,
            supports_class_weight=False,
            interpretable=True,
        ),
        "ridge": EstimatorEntry(
            task="regression",
            family="linear",
            build=ridge,
            default_grid={"alpha": (0.1, 1.0, 10.0, 100.0)},
            handles_missing=False,
            supports_class_weight=False,
            interpretable=True,
        ),
        "random_forest_regressor": EstimatorEntry(
            task="regression",
            family="random_forest",
            build=random_forest_regressor,
            default_grid=_grid_rf,
            handles_missing=True,
            supports_class_weight=False,
            interpretable=False,
        ),
        "hist_gb_regressor": EstimatorEntry(
            task="regression",
            family="hist_gradient_boosting",
            build=hist_gb_regressor,
            default_grid=_grid_hgb,
            handles_missing=True,
            supports_class_weight=False,
            interpretable=False,
        ),
    }


def estimators() -> dict[str, EstimatorEntry]:
    """The six-entry registry, built on first use and cached. Not a module-level dict: building it
    imports sklearn, and that import must stay lazy (module docstring)."""
    if not _ESTIMATORS:
        _ESTIMATORS.update(_build_estimators())
    return _ESTIMATORS


_ESTIMATORS: dict[str, EstimatorEntry] = {}
"""Cache for `estimators()`. Empty until the first training tool runs, so importing this module
(which `tools/__init__.py` does at package init) stays scipy-free."""


# `greater_is_better` per metric. A `neg_*` sklearn scorer and `r2`/`roc_auc`/... are all
# greater-is-better *as sklearn reports them*; the training tool sign-corrects the `neg_*` ones to
# the metric's natural direction (an RMSE is positive) and states this flag in the summary (§3.1).
_GREATER_IS_BETTER: dict[Metric, bool] = {
    "accuracy": True,
    "balanced_accuracy": True,
    "precision": True,
    "recall": True,
    "f1": True,
    "roc_auc": True,
    "pr_auc": True,
    "brier": False,
    "log_loss": False,
    "rmse": False,
    "mae": False,
    "mape": False,
    "medae": False,
    "r2": True,
}


def fit_threads() -> int:
    """Threads one heavy tool may use for its parallel loop (CV folds, permutation repeats, a
    forest's final fit): a third of the cores, so on 16 cores the default 5 folds run in one
    round; threads only, loky processes deadlock (D17, addendum 50)."""
    return max(1, (os.cpu_count() or 1) // 3)


def parallel_jobs(name: str) -> int:
    """`1` for histogram gradient boosting (already OpenMP-parallel on every core), else
    `fit_threads()`."""
    if estimators()[name].family == "hist_gradient_boosting":
        return 1
    return fit_threads()


def greater_is_better(metric: Metric) -> bool:
    """Whether a higher value of `metric` is better, in the metric's natural direction (not
    sklearn's always-greater-is-better scorer convention). Stated in `tune_estimator`'s summary."""
    return _GREATER_IS_BETTER[metric]


# A `neg_*` sklearn scorer reports the negated metric (greater-is-better); its natural value is the
# negation. These are the metrics whose CV scores the training tool sign-flips back (§3.1).
_NEGATED_METRICS: frozenset[Metric] = frozenset(
    {"log_loss", "brier", "rmse", "mae", "mape", "medae"}
)


def is_negated(metric: Metric) -> bool:
    """Whether `metric`'s sklearn scorer is a `neg_*` one, so its CV mean must be sign-flipped to
    the metric's natural direction before reporting."""
    return metric in _NEGATED_METRICS


# `brier` has no multiclass sklearn scorer (`neg_brier_score` is binary-only, P-note §3.1), so the
# training tool refuses a multiclass `brier` as `tool_param`.
_BINARY_ONLY_METRICS: frozenset[Metric] = frozenset({"brier"})


def is_binary_only(metric: Metric) -> bool:
    """Whether `metric` has no multiclass scorer, so a multiclass target makes it `tool_param`."""
    return metric in _BINARY_ONLY_METRICS


def scorer_for(metric: Metric, *, multiclass: bool) -> str | object:
    """Map our `metric` to a sklearn scorer (§3.1's `SCORERS`): a built-in scorer name (`str`) or
    a `make_scorer(...)` object, returned as `str | object` so sklearn's dynamic value never
    escapes the typed surface (P6). `multiclass` selects the OVR/macro variants. The caller has
    already
    refused a multiclass `brier` (`is_binary_only`), so that case cannot reach here.

    Lazy sklearn import for the same scipy reason as `_build_estimators`.
    """
    from sklearn.metrics import f1_score, make_scorer, precision_score, recall_score

    simple: dict[Metric, str] = {
        "pr_auc": "average_precision",
        "log_loss": "neg_log_loss",
        "brier": "neg_brier_score",
        "accuracy": "accuracy",
        "balanced_accuracy": "balanced_accuracy",
        "rmse": "neg_root_mean_squared_error",
        "mae": "neg_mean_absolute_error",
        "mape": "neg_mean_absolute_percentage_error",
        "medae": "neg_median_absolute_error",
        "r2": "r2",
    }
    if metric == "roc_auc":
        return "roc_auc_ovr" if multiclass else "roc_auc"
    if metric in ("precision", "recall", "f1"):
        func = {"precision": precision_score, "recall": recall_score, "f1": f1_score}[metric]
        made: object = (
            make_scorer(func, average="macro", zero_division=0)
            if multiclass
            else make_scorer(func, zero_division=0)
        )
        return made
    return simple[metric]


# --- Inferential model registry (D7, chunk 15 §3.1) ---------------------------------------------
# The D7 second track: the four classical-inference models node 6 may fit, each pointing at the
# `inference` tool that fits it and the target types it is valid for. Pure data (no statsmodels
# import), so `list_estimators` can report it and node 1 can validate
# `modelling.inferential_model` without importing server code or pulling scipy -- the same
# scipy-free constraint `_build_estimators` keeps for the predictive registry.


@dataclass(frozen=True)
class InferentialEntry:
    """One inferential-model registry line (§3.1). `tool` is the `inference`-phase tool that fits
    it; `target_types` is the closed set of `TargetType`s it is valid for; `family` is the GLM
    family (`None` for OLS and Logit, which are not GLMs in this registry's shape)."""

    tool: str
    target_types: tuple[TargetType, ...]
    family: Literal["poisson", "gamma"] | None = None


SPECS: dict[str, InferentialEntry] = {
    "ols": InferentialEntry("fit_ols", ("continuous",)),
    "logit": InferentialEntry("fit_logit", ("binary",)),
    "mnlogit": InferentialEntry("fit_mnlogit", ("multiclass",)),
    "glm_poisson": InferentialEntry("fit_glm", ("count",), "poisson"),
    "glm_gamma": InferentialEntry("fit_glm", ("continuous",), "gamma"),
}
"""The five inferential models (§3.1, chunk 20r adds `mnlogit`). Reported by `list_estimators` as
`inferential_models` so node 1 can validate `modelling.inferential_model` against it without
importing server code. Every `.tool` is a registered `inference`-phase tool name (checked by a
chunk 15 registry test)."""
