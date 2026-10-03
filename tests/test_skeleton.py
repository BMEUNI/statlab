"""Chunk 16 -- the walking-skeleton gate (M6), driven through the production MCP client.

The 33 `[v1]` tools (PLAN.md §6.4) run end to end over a real MCP session, with zero LLM, on
classification and regression fixtures. The production client owns discovery, authorization,
lineage checks, and the durable call audit.

The gate's value is what it finds. If a tool's wire contract, `allow_in` or the lineage rule
makes the spine impossible as written, that surfaces here as a failure, not as something to work
around in the test (spec §2).

Read `tests/test_server_mcp_session.py` for how a session calls a tool and validates the
envelope with `TOOL_RESULT_ADAPTER`; the `mcp_session` factory's docstring in `conftest.py` for
why each test opens its own session inside its own task.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab.config import Settings
from statlab.mcp_client import CallContext, McpClient
from statlab.persistence.db import connect, migrate
from statlab.persistence.repository import (
    create_contract,
    create_node_execution,
    create_project,
    create_run,
    get_project_by_slug,
    get_run,
)
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import ProjectPaths, RunPaths

if TYPE_CHECKING:
    from statlab_tools.settings import ToolSettings

# --- The 33 [v1] tools (spec §3.3, D-g) ----------------------------------------------------------

SKELETON_TOOLS: frozenset[str] = frozenset(
    {
        # intake
        "load_csv",
        "infer_column_roles",
        "validate_contract",
        # data_profiling
        "profile_schema",
        "summarize_numeric",
        "summarize_categorical",
        "report_missingness",
        "summarize_target",
        # data_splitting
        "drop_columns",
        "split_stratified",
        "split_random",
        "verify_split",
        # experiment_design
        "list_estimators",
        # inferential_analysis
        "assemble_inference_matrix",
        "fit_logit",
        "fit_ols",
        "coefficient_table",
        # preprocessing
        "fit_imputer",
        "apply_imputer",
        # feature_engineering
        "fit_onehot_encoder",
        "apply_onehot_encoder",
        "fit_scaler",
        "apply_scaler",
        # model_training
        "train_estimator",
        # model_evaluation
        "predict_proba",
        "predict",
        "score_classification",
        "score_regression",
        "compute_confusion_matrix",
        "compare_models",
        # champion_selection
        "rank_models",
        # artifact_persistence
        "list_artifacts",
        "describe_artifact",
    }
)

CLASSIFICATION_ONLY: frozenset[str] = frozenset(
    {
        "split_stratified",
        "predict_proba",
        "score_classification",
        "compute_confusion_matrix",
        "fit_logit",
    }
)
REGRESSION_ONLY: frozenset[str] = frozenset(
    {"split_random", "predict", "score_regression", "fit_ols"}
)


# --- 3.1 Fixtures (the §1 numbers depend on the exact rng call order) -----------------------------


def subscribers() -> pd.DataFrame:
    rng = np.random.default_rng(20261002)
    n = 400
    age = rng.integers(18, 80, n).astype(float)
    tenure = rng.integers(1, 72, n)
    plan = rng.choice(["basic", "standard", "premium"], n, p=[0.5, 0.3, 0.2])
    region = rng.choice(["north", "south", "east", "west"], n).astype(object)
    effect = pd.Series(plan).map({"basic": 0.9, "standard": 0.0, "premium": -1.0}).to_numpy()
    lin = -0.6 - 0.05 * (tenure - 36) + effect + 0.02 * (age - 48)
    y = (rng.random(n) < 1 / (1 + np.exp(-lin))).astype(int)
    age[rng.random(n) < 0.10] = np.nan
    region[rng.random(n) < 0.06] = None
    return pd.DataFrame(
        {
            "subscriber_id": np.arange(1, n + 1),
            "age": age,
            "tenure_months": tenure,
            "plan": plan,
            "region": region,
            "churned": y,
        }
    )


def houses() -> pd.DataFrame:
    rng = np.random.default_rng(20261003)
    n = 300
    area = rng.uniform(40, 200, n).round(1)
    rooms = rng.integers(1, 7, n).astype(float)
    nb = rng.choice(["centre", "suburb", "rural"], n, p=[0.3, 0.5, 0.2])
    cond = rng.choice(["good", "fair", "poor"], n)
    effect = pd.Series(nb).map({"centre": 40, "suburb": 0, "rural": -25}).to_numpy()
    price = 50 + 1.2 * area + 8 * rooms + effect + rng.normal(0, 15, n)
    rooms[rng.random(n) < 0.08] = np.nan
    return pd.DataFrame(
        {
            "house_id": np.arange(1, n + 1),
            "area_m2": area,
            "rooms": rooms,
            "neighbourhood": nb,
            "condition": cond,
            "price": price.round(2),
        }
    )


def _subscribers_contract() -> DataContract:
    return DataContract.model_validate(
        {
            "target": {"column": "churned", "type": "binary", "positive_class": 1},
            "features": {"id_columns": ["subscriber_id"]},
            "objective": {
                "task": "classification",
                "primary_metric": "roc_auc",
                "inference_goals": [
                    {"question": "Does plan affect churn?", "predictors": ["plan"]},
                    {"question": "Does region affect churn?", "predictors": ["region"]},
                ],
            },
            "split": {"strategy": "stratified"},
            "modelling": {
                "candidates": [
                    "logistic_regression",
                    "random_forest_classifier",
                    "hist_gb_classifier",
                ],
                "inferential_model": "logit",
            },
        }
    )


def _houses_contract() -> DataContract:
    return DataContract.model_validate(
        {
            "target": {"column": "price", "type": "continuous"},
            "features": {"id_columns": ["house_id"]},
            "objective": {
                "task": "regression",
                "primary_metric": "rmse",
                "inference_goals": [
                    {
                        "question": "Does neighbourhood affect price?",
                        "predictors": ["neighbourhood"],
                    },
                    {"question": "Does condition affect price?", "predictors": ["condition"]},
                ],
            },
            "split": {"strategy": "random"},
            "modelling": {
                "candidates": [
                    "linear_regression",
                    "random_forest_regressor",
                    "hist_gb_regressor",
                ],
                "inferential_model": "ols",
            },
        }
    )


# --- 3.2 / 3.3 Thin driver over the production client ---------------------------------------------


@dataclass
class Driver:
    """Thin test convenience over the production client and persisted audit log."""

    client: McpClient
    conn: sqlite3.Connection
    paths: RunPaths
    contract: DataContract
    dataset: str
    produced: set[str] = field(default_factory=set)
    called: list[tuple[str, str]] = field(default_factory=list)
    executions: dict[str, str] = field(default_factory=dict)
    champion_selected: bool = False

    @property
    def run(self) -> str:
        return self.paths.run_id

    @property
    def test_scores(self) -> int:
        run = get_run(self.conn, self.run)
        return int(run is not None and run.test_scored_at is not None)

    async def call(
        self,
        node: str,
        tool: str,
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        **arguments: JsonValue,
    ) -> ToolSuccess:
        execution_id = self.executions.get(node)
        if execution_id is None:
            from statlab.domains.lineage import SPINE_TOOLS_NODES

            execution = create_node_execution(
                self.conn,
                run_id=self.run,
                node_name=node,
                seq=SPINE_TOOLS_NODES.index(node),
            )
            execution_id = execution.id
            self.executions[node] = execution_id
        context = CallContext(
            self.paths,
            node,
            execution_id,
            self.client.allowed_tools(node),
            self.contract,
            self.dataset,
            self.champion_selected,
        )
        result = await self.client.call(context, tool, arguments, trusted=trusted)
        self.produced.update(meta.ref for meta in result.artifacts)
        self.called.append((node, tool))
        if tool == "select_champion":
            self.champion_selected = True
        return result


# --- Orchestrator setup: write the CSV and the contract snapshot (spec D-b) -----------------------


def _write_run(
    settings: ToolSettings, run: str, frame: pd.DataFrame, contract: DataContract
) -> str:
    """Write the frame's CSV under `datasets/<sha256>.csv` and the contract snapshot, exactly
    what the backend does at nodes 1-2. Returns the dataset sha256."""
    import hashlib

    from statlab.storage import ensure_run_layout

    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run)
    ensure_run_layout(paths)
    project = ProjectPaths(data_dir=settings.data_dir, slug="proj")
    project.datasets.mkdir(parents=True, exist_ok=True)
    csv_bytes = frame.to_csv(index=False).encode()
    digest = hashlib.sha256(csv_bytes).hexdigest()
    project.dataset_csv(digest).write_bytes(csv_bytes)
    paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")
    return digest


# --- Spine helpers reading results by meta, never by position -------------------------------------


def _split_refs(success: ToolSuccess) -> dict[str, str]:
    """Map each split role to its ref via `ArtifactMeta.split`, never by position (spec §3.2)."""
    out: dict[str, str] = {}
    for meta in success.artifacts:
        if meta.split is not None:
            out[meta.split] = meta.ref
    assert set(out) == {"train", "valid", "test"}, f"split roles missing: {out!r}"
    return out


def _ref_by_label(success: ToolSuccess, label: str) -> str:
    """The single artifact with `label`, told apart by label (spec §3.2)."""
    refs = [m.ref for m in success.artifacts if m.label == label]
    assert len(refs) == 1, f"expected one artifact labelled {label!r}, got {refs!r}"
    return refs[0]


def _only_ref(success: ToolSuccess) -> str:
    assert len(success.artifacts) == 1, f"expected one artifact, got {success.artifacts!r}"
    return success.artifacts[0].ref


def _summary_number(value: JsonValue) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


# --- The spine (spec §3.3), driven node by node ---------------------------------------------------


@dataclass(frozen=True)
class TaskConfig:
    """Everything the spine needs that differs by task, all read from the contract."""

    frame: pd.DataFrame
    contract: DataContract
    target: str
    id_columns: tuple[str, ...]
    predictors: tuple[str, ...]
    candidates: tuple[str, ...]
    primary_metric: str
    goals: list[dict[str, JsonValue]]
    split_tool: str
    fit_tool: str
    is_classification: bool
    higher_is_better: bool


def _config(frame: pd.DataFrame, contract: DataContract) -> TaskConfig:
    target = contract.target.column
    id_columns = contract.features.id_columns
    predictors = tuple(c for c in frame.columns if c != target and c not in id_columns)
    goals: list[dict[str, JsonValue]] = [
        {"question": g.question, "predictors": list(g.predictors), "alpha": g.alpha}
        for g in contract.objective.inference_goals
    ]
    is_classification = contract.objective.task == "classification"
    return TaskConfig(
        frame=frame,
        contract=contract,
        target=target,
        id_columns=id_columns,
        predictors=predictors,
        candidates=contract.modelling.candidates,
        primary_metric=cast(str, contract.objective.primary_metric),
        goals=goals,
        split_tool=f"split_{contract.split.strategy}",
        fit_tool="fit_logit" if is_classification else "fit_ols",
        is_classification=is_classification,
        # roc_auc higher is better; rmse lower is better (spec G5: stated by the test).
        higher_is_better=is_classification,
    )


@dataclass
class SpineResult:
    """What the gate assertions read from one spine run.

    `ranking_ref`, `models` and `valid_frame` were added for chunk 19/20's reporting flow
    (`tests/test_spine_reporting.py`), which reuses `_open_driver`/`_run_spine` and then continues
    the same driver: `select_champion` needs the ranking **table** ref, and rendering the
    champion's validation curve needs the champion model ref plus the scaled valid frame. They are
    refs the gate already produces internally; exposing them avoids copying the spine."""

    driver: Driver
    score_tables: dict[str, str]
    comparison_ref: str
    comparison_summary: dict[str, JsonValue]
    ranking_summary: dict[str, JsonValue]
    ranking_ref: str
    champion: str
    champion_table_ref: str
    champion_test_table: str
    split_rows: dict[str, int]
    inference_summary: dict[str, JsonValue]
    coefficient_summary: dict[str, JsonValue]
    coefficient_ref: str
    listed_present: set[str]
    models: dict[str, str]
    valid_frame: str


async def _run_spine(driver: Driver, cfg: TaskConfig) -> SpineResult:
    target = cfg.target

    # --- intake ---------------------------------------------------------------------------------
    raw_success = await driver.call("intake", "load_csv", dataset=driver.dataset)
    raw = _only_ref(raw_success)
    await driver.call("intake", "infer_column_roles", frame=raw)
    await driver.call("intake", "validate_contract", frame=raw)

    # --- data_profiling -------------------------------------------------------------------------
    await driver.call("data_profiling", "profile_schema", frame=raw)
    await driver.call("data_profiling", "summarize_numeric", frame=raw)
    await driver.call("data_profiling", "summarize_categorical", frame=raw)
    await driver.call("data_profiling", "report_missingness", frame=raw)
    await driver.call("data_profiling", "summarize_target", frame=raw, target=target)

    # --- data_splitting -------------------------------------------------------------------------
    dropped = await driver.call(
        "data_splitting", "drop_columns", frame=raw, columns=list(cfg.id_columns)
    )
    without_id = _only_ref(dropped)
    split_success = await driver.call(
        "data_splitting", cfg.split_tool, frame=without_id, target=target
    )
    splits = _split_refs(split_success)
    split_rows = {
        role: int(
            _summary_number(cast("dict[str, JsonValue]", split_success.summary["rows"])[role])
        )
        for role in ("train", "valid", "test")
    }
    await driver.call(
        "data_splitting",
        "verify_split",
        train=splits["train"],
        valid=splits["valid"],
        test=splits["test"],
        target=target,
    )

    # --- experiment_design ----------------------------------------------------------------------
    estimators_success = await driver.call(
        "experiment_design", "list_estimators", task=cfg.contract.objective.task
    )
    listed = cast("dict[str, JsonValue]", estimators_success.summary["estimators"])
    for name in cfg.candidates:
        assert name in listed, f"candidate {name!r} not listed by list_estimators"
    inferential = cast("dict[str, JsonValue]", estimators_success.summary["inferential_models"])
    inf_name = cast(str, cfg.contract.modelling.inferential_model)
    inf_tool = cast(str, cast("dict[str, JsonValue]", inferential[inf_name])["tool"])
    assert inf_tool == cfg.fit_tool, f"inferential tool {inf_tool!r} != expected {cfg.fit_tool!r}"

    # --- inferential_analysis -------------------------------------------------------------------
    matrix_success = await driver.call(
        "inferential_analysis",
        "assemble_inference_matrix",
        train=splits["train"],
        valid=splits["valid"],
        target=target,
        predictors=list(cfg.predictors),
    )
    matrix = _ref_by_label(matrix_success, "inference_matrix")
    manifest = _ref_by_label(matrix_success, "inference_manifest")
    inference_summary = dict(matrix_success.summary)
    fit_success = await driver.call("inferential_analysis", inf_tool, matrix=matrix, target=target)
    fit = _only_ref(fit_success)
    coef_success = await driver.call(
        "inferential_analysis",
        "coefficient_table",
        fit=fit,
        manifest=manifest,
        goals=cast("JsonValue", cfg.goals),
    )
    coefficient_ref = _only_ref(coef_success)
    coefficient_summary = dict(coef_success.summary)

    # --- preprocessing --------------------------------------------------------------------------
    imputer = _only_ref(
        await driver.call(
            "preprocessing",
            "fit_imputer",
            train=splits["train"],
            target=target,
            default=cfg.contract.preprocessing.missing.default,
        )
    )
    train_imp = _only_ref(
        await driver.call("preprocessing", "apply_imputer", imputer=imputer, frame=splits["train"])
    )
    valid_imp = _only_ref(
        await driver.call("preprocessing", "apply_imputer", imputer=imputer, frame=splits["valid"])
    )

    # --- feature_engineering --------------------------------------------------------------------
    encoder = _only_ref(
        await driver.call(
            "feature_engineering",
            "fit_onehot_encoder",
            train=train_imp,
            target=target,
            max_cardinality=cfg.contract.preprocessing.max_cardinality,
        )
    )
    train_oh = _only_ref(
        await driver.call(
            "feature_engineering", "apply_onehot_encoder", encoder=encoder, frame=train_imp
        )
    )
    valid_oh = _only_ref(
        await driver.call(
            "feature_engineering", "apply_onehot_encoder", encoder=encoder, frame=valid_imp
        )
    )
    scaler = _only_ref(
        await driver.call(
            "feature_engineering",
            "fit_scaler",
            train=train_oh,
            target=target,
            method=cfg.contract.preprocessing.scaling,
        )
    )
    train_sc = _only_ref(
        await driver.call("feature_engineering", "apply_scaler", scaler=scaler, frame=train_oh)
    )
    valid_sc = _only_ref(
        await driver.call("feature_engineering", "apply_scaler", scaler=scaler, frame=valid_oh)
    )

    # --- model_training -------------------------------------------------------------------------
    models: dict[str, str] = {}
    for name in cfg.candidates:
        models[name] = _only_ref(
            await driver.call(
                "model_training", "train_estimator", train=train_sc, target=target, name=name
            )
        )

    # --- model_evaluation -----------------------------------------------------------------------
    score_tables: dict[str, str] = {}
    for name in cfg.candidates:
        if cfg.is_classification:
            proba = _only_ref(
                await driver.call(
                    "model_evaluation", "predict_proba", model=models[name], frame=valid_sc
                )
            )
            score = await driver.call(
                "model_evaluation",
                "score_classification",
                probabilities=proba,
                truth=valid_sc,
                target=target,
            )
            await driver.call(
                "model_evaluation",
                "compute_confusion_matrix",
                probabilities=proba,
                truth=valid_sc,
                target=target,
            )
        else:
            pred = _only_ref(
                await driver.call("model_evaluation", "predict", model=models[name], frame=valid_sc)
            )
            score = await driver.call(
                "model_evaluation",
                "score_regression",
                predictions=pred,
                truth=valid_sc,
                target=target,
            )
        score_tables[name] = _only_ref(score)

    comparison_success = await driver.call(
        "model_evaluation",
        "compare_models",
        candidates=dict(score_tables),
        metric=cfg.primary_metric,
    )
    comparison_ref = _only_ref(comparison_success)
    comparison_summary = dict(comparison_success.summary)

    # --- champion_selection ---------------------------------------------------------------------
    ranking_success = await driver.call(
        "champion_selection",
        "rank_models",
        comparison=comparison_ref,
        estimators={name: name for name in cfg.candidates},
    )
    ranking_summary = dict(ranking_success.summary)
    ranking_ref = _only_ref(ranking_success)
    champion = cast(str, ranking_summary["top_eligible"])
    champion_table_ref = _only_ref(
        await driver.call(
            "champion_selection",
            "select_champion",
            ranking=ranking_ref,
            candidate=champion,
            rationale="the top eligible candidate by the primary metric",
        )
    )

    test_imp = _only_ref(
        await driver.call(
            "champion_selection", "apply_imputer", imputer=imputer, frame=splits["test"]
        )
    )
    test_oh = _only_ref(
        await driver.call(
            "champion_selection", "apply_onehot_encoder", encoder=encoder, frame=test_imp
        )
    )
    test_sc = _only_ref(
        await driver.call("champion_selection", "apply_scaler", scaler=scaler, frame=test_oh)
    )
    if cfg.is_classification:
        champ_proba = _only_ref(
            await driver.call(
                "champion_selection", "predict_proba", model=models[champion], frame=test_sc
            )
        )
        champ_score = await driver.call(
            "champion_selection",
            "score_classification",
            probabilities=champ_proba,
            truth=test_sc,
            target=target,
        )
    else:
        champ_pred = _only_ref(
            await driver.call(
                "champion_selection", "predict", model=models[champion], frame=test_sc
            )
        )
        champ_score = await driver.call(
            "champion_selection",
            "score_regression",
            predictions=champ_pred,
            truth=test_sc,
            target=target,
        )
    champion_test_table = _only_ref(champ_score)

    # --- artifact_persistence -------------------------------------------------------------------
    listed_artifacts = await driver.call("artifact_persistence", "list_artifacts")
    present = {
        str(cast("dict[str, JsonValue]", e)["ref"])
        for e in cast("list[JsonValue]", listed_artifacts.summary["artifacts"])
    }
    await driver.call(
        "artifact_persistence",
        "describe_artifact",
        artifact=comparison_ref,
        kind="table",
    )
    await driver.call(
        "artifact_persistence",
        "describe_artifact",
        artifact=test_sc,
        kind="dataframe",
    )

    return SpineResult(
        driver=driver,
        score_tables=score_tables,
        comparison_ref=comparison_ref,
        comparison_summary=comparison_summary,
        ranking_summary=ranking_summary,
        ranking_ref=ranking_ref,
        champion=champion,
        champion_table_ref=champion_table_ref,
        champion_test_table=champion_test_table,
        split_rows=split_rows,
        inference_summary=inference_summary,
        coefficient_summary=coefficient_summary,
        coefficient_ref=coefficient_ref,
        listed_present=present,
        models=models,
        valid_frame=valid_sc,
    )


# --- Reading metric values from the wire results --------------------------------------------------


def _metric_entry(summary: Mapping[str, JsonValue], metric: str) -> dict[str, JsonValue]:
    metrics = cast("dict[str, JsonValue]", summary["metrics"])
    return cast("dict[str, JsonValue]", metrics[metric])


def _read_table_json(settings: ToolSettings, run: str, ref: str) -> dict[str, JsonValue]:
    """Read a `table` artifact's JSON straight off disk. `describe_artifact` reports a table's
    shape, not its values, so the coefficient terms (spec G6) are read from the file."""
    import json

    from statlab_contracts.artifacts import ArtifactRef

    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run)
    text = paths.artifact(ArtifactRef(ref), "table").read_text(encoding="utf-8")
    return cast("dict[str, JsonValue]", json.loads(text))


# --- G4-G7 assertions, shared by both tasks -------------------------------------------------------


def _assert_g4_g5_g6_g7(
    result: SpineResult,
    cfg: TaskConfig,
    settings: ToolSettings,
    run: str,
    expected_coeffs: dict[str, float],
) -> None:
    # G4: every candidate's valid primary metric is finite and inside its own CI.
    for name in cfg.candidates:
        summary = _read_table_json(settings, run, result.score_tables[name])
        entry = _metric_entry(summary, cfg.primary_metric)
        value = _summary_number(entry["value"])
        low = _summary_number(entry["ci_low"])
        high = _summary_number(entry["ci_high"])
        assert np.isfinite(value)
        assert low <= value <= high, f"{name} {cfg.primary_metric} {value} outside [{low},{high}]"
        if cfg.is_classification:
            assert value >= 0.65, f"{name} valid roc_auc {value} < 0.65"
        else:
            r2 = _summary_number(_metric_entry(summary, "r2")["value"])
            assert r2 >= 0.85, f"{name} valid r2 {r2} < 0.85"

    # G4 champion test floor.
    champ = _read_table_json(settings, run, result.champion_test_table)
    if cfg.is_classification:
        test_auc = _summary_number(_metric_entry(champ, "roc_auc")["value"])
        assert test_auc >= 0.65, f"champion test roc_auc {test_auc} < 0.65"
    else:
        test_r2 = _summary_number(_metric_entry(champ, "r2")["value"])
        assert test_r2 >= 0.85, f"champion test r2 {test_r2} < 0.85"

    # G5: ranking, best first in the metric's own direction (test states the direction).
    ranking = cast("list[JsonValue]", result.comparison_summary["ranking"])
    assert len(ranking) == 3
    values = [_summary_number(cast("dict[str, JsonValue]", r)["value"]) for r in ranking]
    ordered = sorted(values, reverse=cfg.higher_is_better)
    assert values == ordered, f"ranking not best-first: {values!r}"
    top_candidate = str(cast("dict[str, JsonValue]", ranking[0])["candidate"])
    assert result.champion == top_candidate, "top_eligible is not the rank-1 candidate"
    assert result.ranking_summary["selection_note"]

    # G6: inference.
    assert int(_summary_number(result.inference_summary["n_obs"])) == (
        result.split_rows["train"] + result.split_rows["valid"]
    )
    goals = cast("list[JsonValue]", result.coefficient_summary["goals"])
    by_question = {
        str(cast("dict[str, JsonValue]", g)["question"]): cast("dict[str, JsonValue]", g)
        for g in goals
    }
    planted = by_question[str(cfg.goals[0]["question"])]
    null_goal = by_question[str(cfg.goals[1]["question"])]
    assert int(_summary_number(planted["df"])) == _planted_df(cfg)
    assert planted["reject"] is True, f"planted goal not rejected: {planted!r}"
    assert null_goal["reject"] is False, f"null goal rejected: {null_goal!r}"
    for g in (planted, null_goal):
        p_raw = _summary_number(g["p_raw"])
        p_holm = _summary_number(g["p_holm"])
        assert p_holm >= p_raw, f"p_holm {p_holm} < p_raw {p_raw}"

    coeff_table = _read_table_json(settings, run, result.coefficient_ref)
    terms = {
        str(cast("dict[str, JsonValue]", t)["term"]): cast("dict[str, JsonValue]", t)
        for t in cast("list[JsonValue]", coeff_table["terms"])
    }
    for term, expected in expected_coeffs.items():
        assert term in terms, f"term {term!r} absent from coefficient table: {sorted(terms)!r}"
        low = _summary_number(terms[term]["ci_low"])
        high = _summary_number(terms[term]["ci_high"])
        assert low <= expected <= high, f"planted {term}={expected} outside CI [{low}, {high}]"

    # G7: every produced ref appears in list_artifacts.
    missing = result.driver.produced - result.listed_present
    assert not missing, f"produced refs absent from list_artifacts: {sorted(missing)!r}"


def _planted_df(cfg: TaskConfig) -> int:
    # plan: basic/standard/premium -> 2 dummies; neighbourhood: centre/suburb/rural -> 2 dummies.
    return 2


# --- The two gate runs (G1-G7) --------------------------------------------------------------------


@asynccontextmanager
async def _open_driver(
    server_url: str,
    settings: ToolSettings,
    run: str,
    frame: pd.DataFrame,
    contract: DataContract,
) -> AsyncIterator[tuple[Driver, TaskConfig]]:
    from statlab.storage import ensure_run_layout

    db_path = settings.data_dir / "backend.sqlite"
    conn = connect(db_path)
    migrate(conn)
    project = get_project_by_slug(conn, "proj")
    if project is None:
        project = create_project(conn, slug="proj", name="Skeleton fixture")
    contract_row = create_contract(
        conn,
        project_id=project.id,
        path=settings.data_dir / "contract.json",
        contract=contract,
    )
    run_row = create_run(
        conn,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_row.id)
    ensure_run_layout(paths)
    digest = _write_run(settings, run_row.id, frame, contract)
    cfg = _config(frame, contract)
    backend_settings = Settings(
        mcp_token=settings.mcp_token,
        data_dir=settings.data_dir,
        db_path=db_path,
        mcp_url=f"{server_url}/mcp",
    )
    client = McpClient(backend_settings, conn)
    driver = Driver(
        client=client,
        conn=conn,
        paths=paths,
        contract=contract,
        dataset=digest,
    )
    try:
        async with client:
            assert set(client.tools) >= SKELETON_TOOLS, (
                f"missing tools: {sorted(SKELETON_TOOLS - set(client.tools))!r}"
            )
            yield driver, cfg
    finally:
        driver.conn.close()


def _assert_g1_g2_g3(result: SpineResult, cfg: TaskConfig) -> None:
    driver = result.driver
    # G1 is enforced inside Driver.call: every envelope is validated with TOOL_RESULT_ADAPTER
    # and `ok` is asserted true, with the failure message in the assertion text.
    # G2: production-client lineage guard permits exactly one test scoring.
    assert driver.test_scores == 1, f"test_scores == {driver.test_scores}, expected 1"
    # G3: the set of tools called is SKELETON_TOOLS minus the other task's tools.
    called = {tool for _node, tool in driver.called}
    other = REGRESSION_ONLY if cfg.is_classification else CLASSIFICATION_ONLY
    expected = (SKELETON_TOOLS - other) | {"select_champion"}
    assert called == expected, (
        f"called set mismatch: unexpected={sorted(called - expected)!r}, "
        f"missing={sorted(expected - called)!r}"
    )


@pytest.fixture
def _patch_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-f: the live server is a uvicorn thread in this process, so patching the module constant
    reaches it. Set before the session opens."""
    import statlab_tools.tools.evaluation as evaluation

    monkeypatch.setattr(evaluation, "BOOTSTRAP_RESAMPLES", 200)


@pytest.mark.asyncio
async def test_skeleton_classification_end_to_end(
    live_server: str,
    tool_settings: ToolSettings,
    _patch_bootstrap: None,
) -> None:
    """The classification spine (subscribers), G1-G7, over one live session."""
    frame = subscribers()
    contract = _subscribers_contract()
    async with _open_driver(live_server, tool_settings, "classification", frame, contract) as (
        driver,
        cfg,
    ):
        result = await _run_spine(driver, cfg)
        _assert_g1_g2_g3(result, cfg)
    _assert_g4_g5_g6_g7(
        result,
        cfg,
        tool_settings,
        result.driver.run,
        expected_coeffs={
            "tenure_months": -0.05,
            "plan__premium": -1.9,
            "plan__standard": -0.9,
        },
    )


@pytest.mark.asyncio
async def test_skeleton_regression_end_to_end(
    live_server: str,
    tool_settings: ToolSettings,
    _patch_bootstrap: None,
) -> None:
    """The regression spine (houses), G1-G7, over one live session."""
    frame = houses()
    contract = _houses_contract()
    async with _open_driver(live_server, tool_settings, "regression", frame, contract) as (
        driver,
        cfg,
    ):
        result = await _run_spine(driver, cfg)
        _assert_g1_g2_g3(result, cfg)
    _assert_g4_g5_g6_g7(
        result,
        cfg,
        tool_settings,
        result.driver.run,
        expected_coeffs={
            "neighbourhood__centre": 40.0,
            "neighbourhood__rural": -25.0,
            "rooms": 8.0,
        },
    )


# --- 4. SKELETON_TOOLS coverage (pure, no server) -------------------------------------------------


def test_skeleton_tools_cover_the_33() -> None:
    """The union of the two task sets is SKELETON_TOOLS, the task-specific sets are disjoint, and
    there are exactly 33 tools."""
    assert len(SKELETON_TOOLS) == 33
    assert CLASSIFICATION_ONLY <= SKELETON_TOOLS
    assert REGRESSION_ONLY <= SKELETON_TOOLS
    assert not (CLASSIFICATION_ONLY & REGRESSION_ONLY)
    common = SKELETON_TOOLS - CLASSIFICATION_ONLY - REGRESSION_ONLY
    assert common | CLASSIFICATION_ONLY | REGRESSION_ONLY == SKELETON_TOOLS
