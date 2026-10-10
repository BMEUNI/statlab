"""Validation-only selection, durable champion identity and one held-out score."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import JsonValue, TypeAdapter, ValidationError
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, Failure, ToolSuccess
from statlab_contracts.reporting import ReportSection

from statlab.domains.base import NodeResult
from statlab.domains.candidate_models import SELECTION_CAVEAT
from statlab.domains.candidate_pipeline import candidate_result, pipeline_from_state
from statlab.domains.decisions import ChampionChoice, PipelineState
from statlab.domains.design_inference import _artifact
from statlab.domains.foundation import (
    _completed,
    _Evidence,
    _frame,
    _object,
    _ref,
    _RefusalError,
    _strings,
    problem_from_state,
    shared_result,
)
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError, PersistenceError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import CandidateProgress, RunState
from statlab.persistence.repository import (
    count_prior_test_scorings,
    get_champion_model,
    get_run,
    require_run,
    save_champion_model,
)
from statlab.storage import get_artifact, list_artifacts

TIMING_CAVEAT = "Concurrent candidate fit timings compete for permits and are not comparable."
_QUALITY_LABELS: dict[str, str] = {
    "disguised_missing": "Placeholder values for missing data",
    "mixed_types": "Numbers and text mixed in a column",
    "category_variants": "Same category spelled differently",
    "duplicate_rows": "Duplicate rows",
    "conflicting_labels": "Identical rows with different targets",
    "near_constant": "Almost constant columns",
    "rare_categories": "Very rare categories",
    "target_health": "Target problems",
    "duplicate_columns": "Duplicate columns",
    "missing_without_target": "Columns empty where the target is missing",
    "target_follows_row_order": "Target follows the row order",
}
_QUALITY_RESULTS: dict[str, str] = {
    "pass": "passed",
    "warn": "warning",
    "fail": "failed",
    "skipped": "skipped",
}


def _short(text: str) -> str:
    """Cap a column name for a report cell."""
    return text if len(text) <= 40 else text[:39] + "…"


def _names(columns: Sequence[str]) -> str:
    """List at most five capped names, counting the rest."""
    names = ", ".join(_short(column) for column in columns[:5])
    if len(columns) > 5:
        names += f" and {len(columns) - 5} more"
    return names


def _cell(text: str) -> str:
    """Make text safe for one Markdown table cell."""
    return " ".join(text.splitlines()).replace("|", "\\|")


def _quality_sections(state: RunState) -> tuple[ReportSection, ...]:
    """Build the data quality report section when the step ran."""
    result = _completed(state, "data_quality")
    if result is None:
        return ()
    checks = _object(result.summary.get("checks"), "quality checks")
    lines = [
        "The checks ran on the whole dataset before the split.",
        "",
        "| Check | Result | Rows affected | Columns |",
        "| --- | --- | --- | --- |",
    ]
    for name, value in checks.items():
        entry = _object(value, "quality check")
        columns = _strings(entry.get("columns"), "quality check columns")
        status = str(entry.get("status"))
        cells = _cell(_names(columns)) if columns else "—"
        lines.append(
            f"| {_QUALITY_LABELS.get(name, name)} | {_QUALITY_RESULTS.get(status, status)} | "
            f"{entry.get('rows')} | {cells} |"
        )
    decision = _object(result.summary.get("decision"), "quality decision")
    marked = _object(decision.get("mark_missing"), "quality mark_missing")
    merged = _strings(decision.get("merge_variants"), "quality merge_variants")
    fixes: list[str] = []
    if marked:
        fixes.append(f"Placeholder values were marked as missing in: {_names(list(marked))}.")
    if merged:
        fixes.append(f"Spelling variants were merged in: {_names(merged)}.")
    lines += ["", " ".join(fixes) if fixes else "No fixes were applied."]
    return (ReportSection(heading="Data quality", body="\n".join(lines)),)


@dataclass(frozen=True)
class _Audit:
    tool: str
    arguments: dict[str, JsonValue]
    result: ToolSuccess


def champion_audits(runtime: DomainRuntime) -> tuple[_Audit, ...]:
    """Read successes across interrupted attempts, in durable dispatch order."""
    try:
        rows = runtime.conn.execute(
            "SELECT tc.* FROM tool_calls tc JOIN node_executions ne "
            "ON ne.id = tc.node_execution_id WHERE tc.run_id = ? "
            "AND ne.node_name = 'champion_selection' ORDER BY tc.rowid",
            (runtime.paths.run_id,),
        ).fetchall()
        audits: list[_Audit] = []
        for row in rows:
            result = TOOL_RESULT_ADAPTER.validate_json(row["result_json"])
            arguments = TypeAdapter(dict[str, JsonValue]).validate_json(row["args_json"])
            if result.ok:
                audits.append(_Audit(row["tool_name"], arguments, result))
        return tuple(audits)
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot recover champion audit", cause=exc) from exc


def eligible_candidates(
    state: RunState,
) -> tuple[tuple[CandidateProgress, ...], dict[str, JsonValue]]:
    if any(branch.status in ("pending", "running", "awaiting_user") for branch in state.branches):
        raise EngineError("champion selection requires joined candidates")
    eligible: list[CandidateProgress] = []
    excluded: dict[str, JsonValue] = {}
    for branch in state.branches:
        if branch.status != "completed":
            excluded[branch.public_id] = branch.status
            continue
        training = candidate_result(branch, "model_training").summary
        evaluation = candidate_result(branch, "model_evaluation").summary
        pipeline = pipeline_from_state(state, branch, require="feature_engineering")
        if (
            _ref(training, "model") != _ref(evaluation, "model")
            or _ref(training, "valid") != pipeline.valid
            or _ref(training, "train") != pipeline.train
            or training.get("retune_count") != branch.retune_count
            or evaluation.get("retune_count") != branch.retune_count
        ):
            excluded[branch.public_id] = "latest training/evaluation/replay are not aligned"
            continue
        eligible.append(branch)
    return tuple(eligible), excluded


class ChampionExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        if invocation.spec.name != "champion_selection" or invocation.candidate is not None:
            raise EngineError("champion executor requires shared champion selection")
        evidence = _Evidence()
        try:
            self.runtime.context(invocation)
            return await self._execute(invocation, evidence)
        except (_RefusalError, ValidationError) as exc:
            return evidence.result(
                "failed", {}, error=Failure(error_class="data", message=str(exc))
            )
        except ToolCallError as exc:
            return evidence.result("failed", {}, error=exc.failure)
        except (PersistenceError, sqlite3.Error, OSError) as exc:
            raise EngineError("champion persistence failed", cause=exc) from exc

    async def _call(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        tool: str,
        arguments: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
    ) -> ToolSuccess:
        result = await self.runtime.call(invocation, tool, arguments, trusted=trusted)
        evidence.add(tool, result)
        return result

    async def _execute(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        state = invocation.state
        problem = problem_from_state(state)
        branches, excluded = eligible_candidates(state)
        if len(branches) < 2:
            raise _RefusalError("comparison requires at least two eligible validation score tables")
        scores: dict[str, JsonValue] = {
            branch.public_id: str(
                _ref(candidate_result(branch, "model_evaluation").summary, "score")
            )
            for branch in branches
        }
        audits = champion_audits(self.runtime)
        selections = tuple(audit for audit in audits if audit.tool == "select_champion")
        run = require_run(self.runtime.conn, state.run_id)
        pointer = get_champion_model(self.runtime.conn, state.run_id)
        comparison_summary: dict[str, JsonValue]
        if selections:
            if len(selections) != 1:
                raise _RefusalError("interrupted selection has multiple successful audits")
            selection = selections[0]
            candidate = selection.arguments.get("candidate")
            rationale = selection.arguments.get("rationale")
            if not isinstance(candidate, str) or not isinstance(rationale, str):
                raise EngineError("malformed successful selection audit")
            if selection.result.summary.get("candidate") != candidate:
                raise EngineError("successful selection audit disagrees with its choice")
            choice = ChampionChoice(candidate_id=candidate, rationale=rationale)
            ranking_ref = _ref(selection.arguments, "ranking")
            rankings = tuple(
                audit
                for audit in audits
                if audit.tool == "rank_models" and _artifact(audit.result, "table") == ranking_ref
            )
            if len(rankings) != 1:
                raise _RefusalError("interrupted selection lacks matching validation ranking audit")
            ranked = rankings[0].result
            comparison_ref = _ref(rankings[0].arguments, "comparison")
            comparisons = tuple(
                audit
                for audit in audits
                if audit.tool == "compare_models"
                and _artifact(audit.result, "table") == comparison_ref
                and audit.arguments.get("candidates") == scores
                and audit.arguments.get("metric") == problem.primary_metric
            )
            if len(comparisons) != 1:
                raise _RefusalError(
                    "interrupted selection disagrees with latest validation evidence"
                )
            comparison_summary = comparisons[0].result.summary
            evidence.add("compare_models", comparisons[0].result)
            evidence.add("rank_models", ranked)
            evidence.add("select_champion", selection.result)
            selected = selection.result
        else:
            if pointer is not None or run.test_scored_at is not None:
                raise _RefusalError("interrupted champion has no successful selection audit")
            comparison = await self._call(
                invocation,
                evidence,
                "compare_models",
                {"candidates": scores, "metric": problem.primary_metric},
            )
            ranked = await self._call(
                invocation,
                evidence,
                "rank_models",
                {
                    "comparison": str(_artifact(comparison, "table")),
                    "estimators": {
                        branch.public_id: branch.spec.estimator.name for branch in branches
                    },
                },
            )
            comparison_summary = comparison.summary
            choice = await self.runtime.decide(
                invocation,
                ChampionChoice,
                evidence=(
                    comparison.summary,
                    ranked.summary,
                    {
                        "candidates": [
                            {
                                "id": branch.public_id,
                                "methodology": branch.spec.model_dump(mode="json"),
                                "wave": branch.wave,
                                "retune_count": branch.retune_count,
                                "validation": candidate_result(branch, "model_evaluation").summary,
                            }
                            for branch in branches
                        ],
                        "excluded": excluded,
                        "n_waves": state.wave,
                        "selection_bias": SELECTION_CAVEAT,
                        "fit_timing": TIMING_CAVEAT,
                    },
                ),
                question="Choose a checkpointed public candidate ID from eligible "
                "validation ranking. "
                "Use observed validation/CIs, complexity and interpretability only. Never use test "
                "or fit timing. Narrative sections explain methodology and caveats; do not invent "
                "numbers, a test outcome or a different winner.",
            )
            self._chosen(branches, choice, ranked)
            approved = await self.runtime.approve(invocation, choice, tuple(evidence.tools))
            if approved is None:
                return evidence.result(
                    "awaiting_user",
                    {"decision": choice.model_dump(mode="json"), "excluded": excluded},
                )
            choice = approved
            self._chosen(branches, choice, ranked)
            selected = await self._call(
                invocation,
                evidence,
                "select_champion",
                {
                    "ranking": str(_artifact(ranked, "table")),
                    "candidate": choice.candidate_id,
                    "rationale": choice.rationale,
                },
            )
        branch = self._chosen(branches, choice, ranked)
        trained = candidate_result(branch, "model_training").summary
        evaluation = candidate_result(branch, "model_evaluation").summary
        model = _ref(trained, "model")
        pipeline = pipeline_from_state(state, branch, require="feature_engineering")
        if pointer is not None and (
            pointer.artifact_id != str(model)
            or pointer.candidate_id != branch.database_id(state.run_id)
        ):
            raise _RefusalError("durable champion disagrees with frozen latest model")
        saved = save_champion_model(
            self.runtime.conn,
            run_id=state.run_id,
            candidate_id=branch.database_id(state.run_id),
            artifact_id=str(model),
            name=branch.spec.estimator.name,
            parameters=_object(trained.get("effective_params"), "model parameters"),
        )
        test = _ref(shared_result(state, "data_splitting").summary, "test")
        threshold = evaluation.get("threshold")
        score_tool = (
            "score_classification" if problem.task == "classification" else "score_regression"
        )
        if run.test_scored_at is not None:
            scored, transformed = self._recover_score(
                audits, score_tool, model, test, pipeline, problem.target, threshold
            )
            evidence.add(score_tool, scored)
        else:
            transformed = test
            for step in pipeline.replay:
                transformed = _frame(
                    await self._call(
                        invocation,
                        evidence,
                        step.tool,
                        {**step.parameters, "frame": str(transformed)},
                    )
                )
            prediction_tool = "predict_proba" if problem.task == "classification" else "predict"
            predictions = _artifact(
                await self._call(
                    invocation,
                    evidence,
                    prediction_tool,
                    {"model": str(model), "frame": str(transformed)},
                ),
                "predictions",
            )
            arguments: dict[str, JsonValue] = {
                "probabilities" if problem.task == "classification" else "predictions": str(
                    predictions
                ),
                "truth": str(transformed),
                "target": problem.target,
            }
            if problem.task == "classification":
                arguments["threshold"] = threshold
            scored = await self._call(invocation, evidence, score_tool, arguments)
        test_score = _artifact(scored, "table")
        validation = await self._call(
            invocation, evidence, "render_metrics_table", {"scores": scores}
        )
        current = get_run(self.runtime.conn, state.run_id)
        if current is None or current.test_fingerprint is None:
            raise EngineError("champion test fingerprint is missing")
        prior = count_prior_test_scorings(
            self.runtime.conn,
            project_id=current.project_id,
            fingerprint=current.test_fingerprint,
            exclude_run_id=state.run_id,
        )
        inference = next(
            (
                boundary.result
                for boundary in reversed(state.shared_results)
                if boundary.node_name == "inferential_analysis"
            ),
            None,
        )
        coefficient = (
            str(_ref(inference.summary, "coefficients"))
            if inference is not None and inference.status == "completed"
            else None
        )
        retained = {
            str(ref)
            for boundary in (
                *state.shared_results,
                *(item for branch in state.branches for item in branch.results),
            )
            for ref in boundary.result.artifacts
        }
        retained.update(row.id for row in list_artifacts(self.runtime.conn, state.run_id))
        plots: list[JsonValue] = [
            ref
            for ref in sorted(retained)
            if (row := get_artifact(self.runtime.conn, ref)) is not None
            and row.kind == "plot"
            and row.pruned_at is None
            and self.runtime.paths.artifact(ArtifactRef(ref), "plot").is_file()
        ]
        caveats = ReportSection(
            heading="Selection and execution caveats",
            body=f"{SELECTION_CAVEAT}\n\n{TIMING_CAVEAT}\n\n"
            f"Search waves: {state.wave}. Candidate retunes: "
            + ", ".join(f"{item.public_id}={item.retune_count}" for item in state.branches)
            + f". Excluded branches: {json.dumps(excluded, sort_keys=True)}.",
        )
        report = await self._call(
            invocation,
            evidence,
            "assemble_report",
            {
                "title": f"Modelling report: {problem.target}",
                "champion": str(_artifact(selected, "table")),
                "test_score": str(test_score),
                "validation_table": str(_artifact(validation, "report")),
                "coefficient_table": coefficient,
                "plots": plots,
                "sections": [
                    section.model_dump(mode="json")
                    for section in (*choice.sections, *_quality_sections(state), caveats)
                ],
            },
            trusted={"prior_test_scorings": prior, "n_waves": state.wave},
        )
        return evidence.result(
            "completed",
            {
                "decision": choice.model_dump(mode="json"),
                "candidate_id": branch.public_id,
                "model": str(model),
                "model_id": saved.id,
                "estimator": saved.name,
                "champion": str(_artifact(selected, "table")),
                "test_score": str(test_score),
                "test_metrics": scored.summary,
                "report": report.summary,
                "comparison": comparison_summary,
                "ranking": ranked.summary,
                "prior_test_scorings": prior,
                "n_waves": state.wave,
                "threshold": threshold,
                "test_frame": str(transformed),
                "replay": [step.model_dump(mode="json") for step in pipeline.replay],
                "excluded": excluded,
                "selection_bias": SELECTION_CAVEAT,
                "fit_timing": TIMING_CAVEAT,
            },
        )

    def _chosen(
        self, branches: tuple[CandidateProgress, ...], choice: ChampionChoice, ranked: ToolSuccess
    ) -> CandidateProgress:
        branch = next((item for item in branches if item.public_id == choice.candidate_id), None)
        rows = ranked.summary.get("ranking")
        eligible = isinstance(rows, list) and any(
            isinstance(row, dict)
            and row.get("candidate") == choice.candidate_id
            and row.get("eligible") is True
            for row in rows
        )
        if branch is None or not eligible:
            raise _RefusalError("chosen candidate is unknown or ineligible")
        return branch

    def _recover_score(
        self,
        audits: tuple[_Audit, ...],
        tool: str,
        model: ArtifactRef,
        test: ArtifactRef,
        pipeline: PipelineState,
        target: str,
        threshold: JsonValue,
    ) -> tuple[ToolSuccess, ArtifactRef]:
        scores = tuple(audit for audit in audits if audit.tool == tool)
        if len(scores) != 1:
            raise _RefusalError(
                "test scoring interrupted: durable claim has no unique successful score; "
                "retry forbidden"
            )
        score = scores[0]
        prediction_tool = "predict_proba" if tool == "score_classification" else "predict"
        prediction_key = "probabilities" if tool == "score_classification" else "predictions"
        transformed = _ref(score.arguments, "truth")
        score_index = audits.index(score)
        predicted_index = next(
            (
                index
                for index in range(score_index - 1, -1, -1)
                if audits[index].tool == prediction_tool
                and audits[index].arguments.get("model") == str(model)
                and audits[index].arguments.get("frame") == str(transformed)
                and str(_artifact(audits[index].result, "predictions"))
                == score.arguments.get(prediction_key)
            ),
            None,
        )
        if predicted_index is None:
            raise _RefusalError("interrupted score lacks frozen prediction audit")
        # Trace this score's input backwards, ignoring abandoned earlier transform chains.
        frame = transformed
        before = predicted_index
        for step in reversed(pipeline.replay):
            match = next(
                (
                    index
                    for index in range(before - 1, -1, -1)
                    if audits[index].tool == step.tool
                    and _frame(audits[index].result) == frame
                    and all(
                        audits[index].arguments.get(key) == value
                        for key, value in step.parameters.items()
                    )
                ),
                None,
            )
            if match is None:
                raise _RefusalError("interrupted score lacks frozen replay audit")
            frame = _ref(audits[match].arguments, "frame")
            before = match
        if frame != test:
            raise _RefusalError("interrupted score replay does not start at shared test")
        truth = get_artifact(self.runtime.conn, str(transformed))
        if (
            truth is None
            or not truth.derived_from_test
            or score.arguments.get("truth") != str(transformed)
            or score.arguments.get("target") != target
            or (tool == "score_classification" and score.arguments.get("threshold") != threshold)
        ):
            raise _RefusalError(
                "test scoring interrupted: successful audit does not match frozen champion"
            )
        return score.result, transformed
