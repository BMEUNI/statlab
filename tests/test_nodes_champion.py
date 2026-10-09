"""Offline champion safety regressions; authored but not executed."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import JsonValue
from statlab.domains.base import NodeResult
from statlab.domains.candidate_models import CandidateModelExecutor
from statlab.domains.candidate_pipeline import candidate_result, pipeline_from_state
from statlab.domains.champion import ChampionExecutor, champion_audits, eligible_candidates
from statlab.domains.decisions import ChampionChoice, PipelineState
from statlab.errors import EngineError, PersistenceError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.llm.skills import NodeSkill, SkillPolicy
from statlab.mcp_client import CallContext, Repair
from statlab.persistence.models import ProjectRow, RunMode
from statlab.persistence.repository import (
    claim_test_scoring,
    create_node_execution,
    ensure_candidate,
    finish_node_execution,
    next_node_attempt,
    record_tool_call,
    save_champion_model,
)
from statlab.storage import create_artifact
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, ArtifactRef
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.envelope import Failure, ToolFailure, ToolSuccess
from statlab_contracts.vocabulary import TargetType

from tests.test_nodes_candidate_pipeline import (
    CandidateTools,
    candidate_harness,
    candidate_invocation,
    prepare_candidate,
    save_result,
)
from tests.test_nodes_foundation import LEGACY_SPINE, Harness


class ChampionTools(CandidateTools):
    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.eligible = True
        self.interrupt_score = False
        self.interrupt_report = False
        self.interrupt_select = False
        self.manifest_text: str | None = None

    def result(self, kind: ArtifactKind, summary: dict[str, JsonValue]) -> ToolSuccess:
        return ToolSuccess(summary=summary, artifacts=(self.artifact(kind),))

    async def call(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        repair: Repair | None = None,
    ) -> ToolSuccess:
        assert tool in ctx.allowed and repair is None
        sent = {**params, **(trusted or {})}
        self.calls.append((ctx, tool, sent.copy()))
        if tool == self.failure_tool:
            raise ToolCallError(tool, Failure(error_class="data", message="champion tool refused"))
        if tool in ("score_regression", "score_classification"):
            assert ctx.champion_selected
            assert claim_test_scoring(self.conn, ctx.paths.run_id)
            if self.interrupt_score:
                raise asyncio.CancelledError
            output = self.result(
                "table", {"metrics": {"rmse": {"value": 0.8, "ci_low": 0.6, "ci_high": 0.9}}}
            )
        elif tool == "compare_models":
            output = self.result("table", {"top_two_distinguishable": False})
        elif tool == "rank_models":
            output = self.result(
                "table",
                {
                    "ranking": [
                        {"candidate": "c_01", "eligible": self.eligible},
                        {"candidate": "c_02", "eligible": True},
                    ],
                    "top_two_eligible_distinguishable": False,
                },
            )
        elif tool == "select_champion":
            output = self.result(
                "table", {"candidate": sent["candidate"], "rationale": sent["rationale"]}
            )
        elif tool.startswith("apply_") or tool == "drop_columns":
            assert ctx.champion_selected
            output = self.result("dataframe", {})
        elif tool in ("predict", "predict_proba"):
            assert ctx.champion_selected
            output = self.result("predictions", {})
        elif tool == "render_metrics_table":
            output = self.result("report", {"markdown": "validation metrics from tool"})
        elif tool == "assemble_report":
            if self.interrupt_report:
                raise asyncio.CancelledError
            data = b"# Real report\n"
            (ctx.paths.report / "report.md").write_bytes(data)
            output = ToolSuccess(
                summary={"path": "report/report.md", "sha256": hashlib.sha256(data).hexdigest()}
            )
        elif tool == "export_pipeline_script":
            ctx.paths.pipeline_py.write_text("# frozen computation\n")
            output = ToolSuccess(
                summary={"sha256": hashlib.sha256(ctx.paths.pipeline_py.read_bytes()).hexdigest()}
            )
        elif tool == "snapshot_run":
            ctx.paths.manifest_json.write_text(
                self.manifest_text
                if self.manifest_text is not None
                else json.dumps({"run_hash": "h" * 64, "files": []})
            )
            output = ToolSuccess(summary={"run_hash": "h" * 64})
        else:
            raise AssertionError(tool)
        derived = tool not in (
            "compare_models",
            "rank_models",
            "select_champion",
            "render_metrics_table",
        )
        for meta in output.artifacts:
            path = ctx.paths.artifact(meta.ref, meta.kind)
            path.write_bytes(b"offline artifact")
            create_artifact(
                self.conn,
                ref=meta.ref,
                run_id=ctx.paths.run_id,
                kind=meta.kind,
                path=path,
                meta=meta,
                produced_by=tool,
                derived_from_test=derived,
            )
        record_tool_call(
            self.conn,
            run_id=ctx.paths.run_id,
            node_execution_id=ctx.node_execution_id,
            tool_name=tool,
            args_json=json.dumps(sent),
            result_json=output.model_dump_json(),
            duration_ms=1,
        )
        if tool == "select_champion" and self.interrupt_select:
            raise asyncio.CancelledError
        return output


def tail_invocation(h: Harness, index: int = 11) -> NodeInvocation:
    spec = LEGACY_SPINE[index]
    execution = create_node_execution(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_name=spec.name,
        seq=index + 1,
        attempt=next_node_attempt(
            h.runtime.conn,
            run_id=h.state.run_id,
            node_name=spec.name,
            candidate_id=None,
        ),
    )
    return NodeInvocation(h.state, spec, execution, None)


def _persist_fixture_call(
    h: Harness,
    execution_id: str,
    tool: str,
    arguments: dict[str, JsonValue],
    output: ToolSuccess,
) -> None:
    for meta in output.artifacts:
        path = h.runtime.paths.artifact(meta.ref, meta.kind)
        path.write_bytes(b"frozen fitted model" if meta.kind == "model" else b"offline artifact")
        create_artifact(
            h.runtime.conn,
            ref=meta.ref,
            run_id=h.state.run_id,
            kind=meta.kind,
            path=path,
            meta=meta,
            produced_by=tool,
            split=meta.split,
            derived_from_test=meta.split == "test",
        )
    record_tool_call(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_execution_id=execution_id,
        tool_name=tool,
        args_json=json.dumps(arguments),
        result_json=output.model_dump_json(),
        duration_ms=1,
    )


async def champion_harness(
    conn: sqlite3.Connection,
    project: ProjectRow,
    root: Path,
    *,
    target_type: TargetType = "continuous",
) -> tuple[Harness, ChampionTools]:
    h, candidate_tools = await candidate_harness(conn, project, root, target_type=target_type)
    shared: list[BoundaryResult] = []
    mapped: dict[str, str] = {}
    audited: list[tuple[str, ToolSuccess]] = []
    for index, boundary in enumerate(h.state.shared_results):
        assert boundary.node_name == LEGACY_SPINE[index].name
        execution = create_node_execution(
            conn,
            run_id=h.state.run_id,
            node_name=boundary.node_name,
            seq=index + 1,
            candidate_id=None,
            attempt=next_node_attempt(
                conn, run_id=h.state.run_id, node_name=boundary.node_name, candidate_id=None
            ),
        )
        finish_node_execution(conn, execution.id, boundary.result)
        mapped[boundary.execution_id] = execution.id
        shared.append(
            BoundaryResult(
                node_name=boundary.node_name, execution_id=execution.id, result=boundary.result
            )
        )
        entries = boundary.result.summary.get("tools", [])
        assert isinstance(entries, list)
        for entry in entries:
            assert isinstance(entry, dict)
            tool_name = entry["tool"]
            stored = entry["result"]
            assert isinstance(tool_name, str) and isinstance(stored, dict)
            audited.append((tool_name, ToolSuccess.model_validate(stored)))
    for (ctx, tool, arguments), (audit_tool, shared_output) in zip(
        h.tools.calls, audited, strict=True
    ):
        assert tool == audit_tool
        _persist_fixture_call(h, mapped[ctx.node_execution_id], tool, arguments, shared_output)
    h.state = h.state.model_copy(update={"shared_results": tuple(shared)})
    await prepare_candidate(h)
    for index in (8, 9, 10):
        invocation = candidate_invocation(h, index)
        output = await CandidateModelExecutor(h.runtime)(invocation)
        save_result(h, invocation, output)
    for (ctx, tool, arguments), (output_tool, tool_output) in zip(
        candidate_tools.calls, candidate_tools.outputs, strict=True
    ):
        assert tool == output_tool
        _persist_fixture_call(h, ctx.node_execution_id, tool, arguments, tool_output)
    original = h.state.branches[0]
    first = CandidateProgress(
        public_id=original.public_id,
        spec=original.spec,
        results=original.results,
        position=11,
        status="completed",
    )
    spec = CandidateSpec(
        label="different fixed intercept",
        rationale="Independent fixed comparison fixture",
        preprocessing=first.spec.preprocessing,
        features=first.spec.features,
        estimator=EstimatorSpec(name=first.spec.estimator.name, params={"fit_intercept": False}),
    )
    second_progress = CandidateProgress(public_id="c_02", spec=spec)
    second_id = second_progress.database_id(h.state.run_id)
    ensure_candidate(conn, candidate_id=second_id, run_id=h.state.run_id, spec=spec)
    second_results: list[BoundaryResult] = []
    for boundary in first.results:
        summary = dict(boundary.result.summary)
        if "pipeline" in summary:
            pipeline = PipelineState.model_validate(summary["pipeline"])
            summary["pipeline"] = PipelineState(
                train=pipeline.train,
                valid=pipeline.valid,
                replay=pipeline.replay,
                feature_manifest=pipeline.feature_manifest,
                methodology=spec,
            ).model_dump(mode="json")
        result = NodeResult(
            status=boundary.result.status,
            summary=summary,
            skip_reason=boundary.result.skip_reason,
        )
        copied = create_node_execution(
            conn,
            run_id=h.state.run_id,
            node_name=boundary.node_name,
            seq=1 + [node.name for node in LEGACY_SPINE].index(boundary.node_name),
            candidate_id=second_id,
            attempt=next_node_attempt(
                conn, run_id=h.state.run_id, node_name=boundary.node_name, candidate_id=second_id
            ),
        )
        finish_node_execution(conn, copied.id, result)
        second_results.append(
            BoundaryResult(node_name=boundary.node_name, execution_id=copied.id, result=result)
        )
    second = CandidateProgress(
        public_id="c_02", spec=spec, results=tuple(second_results), position=11, status="completed"
    )
    h.state = RunState(
        run_id=h.state.run_id,
        spine_id=h.state.spine_id,
        spine=h.state.spine,
        mode="autonomous",
        cursor=11,
        traversal=h.state.traversal,
        branches=(first, second),
        shared_results=h.state.shared_results,
    )
    tools = ChampionTools(conn)
    tools.counter = 1000
    names = frozenset(
        {
            "compare_models",
            "rank_models",
            "select_champion",
            "predict",
            "predict_proba",
            "score_regression",
            "score_classification",
            "render_metrics_table",
            "assemble_report",
            "export_pipeline_script",
            "snapshot_run",
            *(step.tool for step in pipeline_from_state(h.state, first).replay),
        }
    )
    skill = SkillPolicy(
        "fixed",
        tuple(
            NodeSkill(
                node.name, "section", "purpose", "heuristics", "tools", "outputs", "pitfalls", names
            )
            for node in LEGACY_SPINE
        ),
        b"fixed",
    )
    h.runtime = replace(h.runtime, tools=tools, skill=skill)
    h.runtime.paths.skill_snapshot_md.write_bytes(skill.original_bytes)
    h.llm.responses[ChampionChoice] = ChampionChoice(
        candidate_id="c_01", rationale="Validation and simplicity"
    )
    return h, tools


@pytest.mark.asyncio
@pytest.mark.parametrize("target_type", ["continuous", "binary"])
async def test_champion_selects_before_exact_replay_and_one_task_score(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, target_type: TargetType
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path, target_type=target_type)
    pipeline = pipeline_from_state(h.state, h.state.branches[0])
    output = await ChampionExecutor(h.runtime)(tail_invocation(h))
    assert output.status == "completed"
    names = [name for _, name, _ in tools.calls]
    score = "score_regression" if target_type == "continuous" else "score_classification"
    assert names.count("select_champion") == names.count(score) == 1
    replay = tools.calls[names.index("select_champion") + 1 :]
    for step, (ctx, name, arguments) in zip(pipeline.replay, replay, strict=False):
        assert name == step.tool and ctx.champion_selected
        assert {key: arguments[key] for key in step.parameters} == step.parameters
    assert ("predict_proba" in names) == (target_type == "binary")
    assert "select_threshold" not in names and not any(name.startswith("fit_") for name in names)
    report = next(arguments for _, name, arguments in tools.calls if name == "assemble_report")
    assert report["n_waves"] == h.state.wave and report["prior_test_scorings"] == 0
    assert report["coefficient_table"] is None
    assert "not comparable" in str(report["sections"])
    assert output.summary["candidate_id"] == "c_01"


@pytest.mark.asyncio
async def test_guided_unknown_and_ineligible_choice_stop_before_selection(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    h.state = with_mode(h.state, "guided")
    held = await ChampionExecutor(h.runtime)(tail_invocation(h))
    assert held.status == "awaiting_user"
    assert not any(name == "select_champion" for _, name, _ in tools.calls)
    h.state = with_mode(h.state, "autonomous")
    h.llm.responses[ChampionChoice] = ChampionChoice(candidate_id="c_99", rationale="Unknown")
    assert (await ChampionExecutor(h.runtime)(tail_invocation(h))).status == "failed"
    h.llm.responses[ChampionChoice] = ChampionChoice(candidate_id="c_01", rationale="Excluded")
    tools.eligible = False
    assert (await ChampionExecutor(h.runtime)(tail_invocation(h))).status == "failed"
    assert not any(name == "select_champion" for _, name, _ in tools.calls)


def with_mode(state: RunState, mode: RunMode) -> RunState:
    return RunState(
        run_id=state.run_id,
        spine_id=state.spine_id,
        spine=state.spine,
        mode=mode,
        cursor=state.cursor,
        traversal=state.traversal,
        branches=state.branches,
        shared_results=state.shared_results,
        wave=state.wave,
        budget=state.budget,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["select", "score", "report"])
async def test_interruption_recovers_successes_and_never_retries_ambiguous_claim(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, boundary: str
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    tools.interrupt_select = boundary == "select"
    tools.interrupt_score = boundary == "score"
    tools.interrupt_report = boundary == "report"
    with pytest.raises(asyncio.CancelledError):
        await ChampionExecutor(h.runtime)(tail_invocation(h))
    tools.interrupt_select = tools.interrupt_score = tools.interrupt_report = False
    resumed = await ChampionExecutor(h.runtime)(tail_invocation(h))
    assert resumed.status == ("failed" if boundary == "score" else "completed")
    names = [name for _, name, _ in tools.calls]
    assert names.count("select_champion") == 1
    assert names.count("score_regression") == 1
    if boundary == "score":
        assert resumed.error is not None and "retry forbidden" in resumed.error.message


@pytest.mark.asyncio
async def test_latest_model_alignment_excludes_stale_retune_and_one_survivor_fails(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    second = h.state.branches[1]
    results = list(second.results)
    evaluation = results[3]
    summary = {**evaluation.result.summary, "model": "a_000000000000ffff"}
    results[3] = BoundaryResult(
        node_name=evaluation.node_name,
        execution_id=evaluation.execution_id,
        result=NodeResult(status="completed", summary=summary),
    )
    altered = CandidateProgress(
        public_id=second.public_id,
        spec=second.spec,
        results=tuple(results),
        position=11,
        status="completed",
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "branches": [
                h.state.branches[0].model_dump(mode="json"),
                altered.model_dump(mode="json"),
            ],
        }
    )
    survivors, excluded = eligible_candidates(h.state)
    assert len(survivors) == 1 and "aligned" in str(excluded)
    output = await ChampionExecutor(h.runtime)(tail_invocation(h))
    assert output.status == "failed" and tools.calls == []


@pytest.mark.asyncio
async def test_dispatch_identity_rejects_foreign_node(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await champion_harness(migrated_db, project, tmp_path)
    with pytest.raises(EngineError, match="shared champion"):
        await ChampionExecutor(h.runtime)(tail_invocation(h, 12))


@pytest.mark.asyncio
async def test_latest_retune_model_threshold_and_repository_exposure_are_reported(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    from statlab.persistence.repository import create_run, get_run, set_test_fingerprint

    h, tools = await champion_harness(migrated_db, project, tmp_path, target_type="binary")
    first = h.state.branches[0]
    train, evaluation = first.results[2:4]
    latest_model = ArtifactRef("a_000000000000dddd")
    execution = create_node_execution(
        migrated_db,
        run_id=h.state.run_id,
        node_name="model_training",
        seq=9,
        attempt=next_node_attempt(
            migrated_db,
            run_id=h.state.run_id,
            node_name="model_training",
            candidate_id=first.database_id(h.state.run_id),
        ),
        candidate_id=first.database_id(h.state.run_id),
    )
    path = h.runtime.paths.artifact(latest_model, "model")
    path.write_bytes(b"latest retuned fitted model")
    meta = ArtifactMeta(ref=latest_model, kind="model")
    create_artifact(
        migrated_db,
        ref=latest_model,
        run_id=h.state.run_id,
        kind="model",
        path=path,
        meta=meta,
        produced_by="train_estimator",
    )
    record_tool_call(
        migrated_db,
        run_id=h.state.run_id,
        node_execution_id=execution.id,
        tool_name="train_estimator",
        args_json="{}",
        result_json=ToolSuccess(artifacts=(meta,)).model_dump_json(),
        duration_ms=1,
    )
    latest_train = BoundaryResult(
        node_name=train.node_name,
        execution_id=execution.id,
        result=NodeResult(
            status="completed",
            summary={**train.result.summary, "model": str(latest_model), "retune_count": 1},
        ),
    )
    latest_eval = BoundaryResult(
        node_name=evaluation.node_name,
        execution_id=evaluation.execution_id,
        result=NodeResult(
            status="completed",
            summary={
                **evaluation.result.summary,
                "model": str(latest_model),
                "retune_count": 1,
                "threshold": 0.3,
            },
        ),
    )
    retuned = CandidateProgress(
        public_id=first.public_id,
        spec=first.spec,
        position=11,
        status="completed",
        retune_count=1,
        results=(*first.results[:4], latest_train, latest_eval, first.results[4]),
    )
    coefficient = ArtifactRef("a_000000000000eeee")
    coefficient_path = h.runtime.paths.artifact(coefficient, "table")
    coefficient_path.write_bytes(b"canonical coefficient table")
    create_artifact(
        migrated_db,
        ref=coefficient,
        run_id=h.state.run_id,
        kind="table",
        path=coefficient_path,
        meta=ArtifactMeta(ref=coefficient, kind="table"),
        produced_by="collect_coefficients",
    )
    shared = tuple(
        BoundaryResult(
            node_name=item.node_name,
            execution_id=item.execution_id,
            result=NodeResult(status="completed", summary={"coefficients": str(coefficient)}),
        )
        if item.node_name == "inferential_analysis"
        else item
        for item in h.state.shared_results
    )
    h.state = RunState(
        run_id=h.state.run_id,
        spine_id=h.state.spine_id,
        spine=h.state.spine,
        mode="autonomous",
        cursor=11,
        wave=2,
        traversal=h.state.traversal,
        branches=(retuned, h.state.branches[1]),
        shared_results=shared,
    )
    current = get_run(migrated_db, h.state.run_id)
    assert current is not None and current.test_fingerprint is not None
    prior = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=current.contract_id,
        mode="autonomous",
        config_json="{}",
    )
    assert set_test_fingerprint(migrated_db, prior.id, current.test_fingerprint)
    assert claim_test_scoring(migrated_db, prior.id)
    output = await ChampionExecutor(h.runtime)(tail_invocation(h))
    assert output.status == "completed" and output.summary["model"] == str(latest_model)
    prediction = next(arguments for _, name, arguments in tools.calls if name == "predict_proba")
    assert prediction["model"] == str(latest_model)
    score = next(arguments for _, name, arguments in tools.calls if name == "score_classification")
    assert score["threshold"] == 0.3
    report = next(arguments for _, name, arguments in tools.calls if name == "assemble_report")
    assert report["coefficient_table"] == str(coefficient)
    assert report["prior_test_scorings"] == 1 and report["n_waves"] == 2
    assert "c_01=1" in str(report["sections"])


def _failed(output: NodeResult, message: str) -> None:
    assert output.status == "failed"
    assert output.error is not None
    assert output.error.message == message


async def _run_champion(h: Harness) -> NodeResult:
    return await ChampionExecutor(h.runtime)(tail_invocation(h))


@pytest.mark.asyncio
async def test_champion_audit_skips_failures_and_malformed_rows_fail_closed(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await champion_harness(migrated_db, project, tmp_path)
    inv = tail_invocation(h)
    record_tool_call(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_execution_id=inv.execution.id,
        tool_name="compare_models",
        args_json=json.dumps({}),
        result_json=ToolFailure(error_class="data", message="refused").model_dump_json(),
        duration_ms=1,
    )
    assert champion_audits(h.runtime) == ()
    record_tool_call(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_execution_id=inv.execution.id,
        tool_name="compare_models",
        args_json=json.dumps({}),
        result_json="not json",
        duration_ms=1,
    )
    with pytest.raises(PersistenceError, match="cannot recover champion audit"):
        champion_audits(h.runtime)
    with pytest.raises(EngineError, match="champion persistence failed"):
        await _run_champion(h)


@pytest.mark.asyncio
async def test_eligibility_requires_join_and_excludes_unsuccessful_branches(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await champion_harness(migrated_db, project, tmp_path)
    second = h.state.branches[1]
    for status in ("running", "failed"):
        if status == "running":
            rebuilt = CandidateProgress(
                public_id=second.public_id,
                spec=second.spec,
                results=second.results,
                position=second.position,
                status="running",
            )
        else:
            assert [r.node_name for r in second.results[:4]] == [
                "preprocessing",
                "feature_engineering",
                "model_training",
                "model_evaluation",
            ]
            rebuilt = CandidateProgress(
                public_id=second.public_id,
                spec=second.spec,
                results=second.results[:4],
                position=10,
                status="failed",
                pending_result=BoundaryResult(
                    node_name="model_interpretation",
                    execution_id="failed-interpretation",
                    result=NodeResult(
                        status="failed",
                        error=Failure(error_class="data", message="interpretation refused"),
                    ),
                ),
            )
        state = RunState.model_validate(
            {
                **h.state.model_dump(mode="json"),
                **({"cursor": 6} if status == "running" else {}),
                "branches": [
                    h.state.branches[0].model_dump(mode="json"),
                    rebuilt.model_dump(mode="json"),
                ],
            }
        )
        if status == "running":
            with pytest.raises(EngineError, match="champion selection requires joined candidates"):
                eligible_candidates(state)
        else:
            survivors, excluded = eligible_candidates(state)
            assert survivors == (h.state.branches[0],)
            assert excluded == {"c_02": "failed"}


@pytest.mark.asyncio
async def test_champion_tool_failure_is_structured(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    tools.failure_tool = "compare_models"
    output = await _run_champion(h)
    _failed(output, "champion tool refused")
    assert output.artifacts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["duplicate", "malformed", "disagree", "ranking", "comparison"])
async def test_interrupted_selection_audits_must_be_unique_and_consistent(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, case: str
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    conn = h.runtime.conn
    tools.interrupt_select = True
    with pytest.raises(asyncio.CancelledError):
        await _run_champion(h)
    tools.interrupt_select = False
    inv = tail_invocation(h)
    row = conn.execute("SELECT * FROM tool_calls WHERE tool_name = 'select_champion'").fetchone()
    args = json.loads(row["args_json"])

    def record(args_json: str, result_json: str) -> None:
        record_tool_call(
            conn,
            run_id=h.state.run_id,
            node_execution_id=inv.execution.id,
            tool_name="select_champion",
            args_json=args_json,
            result_json=result_json,
            duration_ms=1,
        )

    if case == "duplicate":
        record(row["args_json"], row["result_json"])
        _failed(await _run_champion(h), "interrupted selection has multiple successful audits")
    elif case == "comparison":
        conn.execute(
            "DELETE FROM tool_calls WHERE tool_name = 'compare_models' "
            "AND node_execution_id IN (SELECT id FROM node_executions "
            "WHERE node_name = 'champion_selection')"
        )
        _failed(
            await _run_champion(h),
            "interrupted selection disagrees with latest validation evidence",
        )
    else:
        conn.execute("DELETE FROM tool_calls WHERE id = ?", (row["id"],))
        success = ToolSuccess.model_validate_json(row["result_json"])
        if case == "malformed":
            record(json.dumps({**args, "candidate": 1}), row["result_json"])
            with pytest.raises(EngineError, match="malformed successful selection audit"):
                await _run_champion(h)
        elif case == "disagree":
            record(
                json.dumps(args),
                ToolSuccess(
                    summary={"candidate": "c_02"}, artifacts=success.artifacts
                ).model_dump_json(),
            )
            with pytest.raises(EngineError, match="disagrees with its choice"):
                await _run_champion(h)
        else:
            record(json.dumps({**args, "ranking": "a_00000000000000aa"}), row["result_json"])
            _failed(
                await _run_champion(h),
                "interrupted selection lacks matching validation ranking audit",
            )


@pytest.mark.asyncio
async def test_claimed_or_pointed_run_without_selection_audit_refuses(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    claim_test_scoring(h.runtime.conn, h.state.run_id)
    _failed(await _run_champion(h), "interrupted champion has no successful selection audit")
    assert not any(name == "compare_models" for _, name, _ in tools.calls)


@pytest.mark.asyncio
async def test_durable_pointer_must_match_frozen_choice(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    h.llm.responses[ChampionChoice] = ChampionChoice(candidate_id="c_02", rationale="Second branch")
    tools.interrupt_select = True
    with pytest.raises(asyncio.CancelledError):
        await _run_champion(h)
    tools.interrupt_select = False
    first = h.state.branches[0]
    save_champion_model(
        h.runtime.conn,
        run_id=h.state.run_id,
        candidate_id=first.database_id(h.state.run_id),
        artifact_id=str(candidate_result(first, "model_training").summary["model"]),
        name=first.spec.estimator.name,
        parameters={"fit_intercept": False},
    )
    _failed(await _run_champion(h), "durable champion disagrees with frozen latest model")


@pytest.mark.asyncio
async def test_missing_test_fingerprint_fails_after_score(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await champion_harness(migrated_db, project, tmp_path)
    h.runtime.conn.execute(
        "UPDATE runs SET test_fingerprint = NULL WHERE id = ?", (h.state.run_id,)
    )
    with pytest.raises(EngineError, match="champion test fingerprint is missing"):
        await _run_champion(h)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "DELETE FROM tool_calls WHERE tool_name = 'predict' "
            "AND node_execution_id IN (SELECT id FROM node_executions "
            "WHERE node_name = 'champion_selection')",
            "interrupted score lacks frozen prediction audit",
        ),
        (
            "DELETE FROM tool_calls WHERE tool_name = 'apply_scaler' "
            "AND node_execution_id IN (SELECT id FROM node_executions "
            "WHERE node_name = 'champion_selection')",
            "interrupted score lacks frozen replay audit",
        ),
        (
            "UPDATE tool_calls SET args_json = json_set(args_json, '$.frame', "
            "'a_00000000000000bb') WHERE tool_name = 'drop_columns' "
            "AND node_execution_id IN (SELECT id FROM node_executions "
            "WHERE node_name = 'champion_selection')",
            "interrupted score replay does not start at shared test",
        ),
        (
            "UPDATE tool_calls SET args_json = json_set(args_json, '$.target', 'not_target') "
            "WHERE tool_name = 'score_regression' "
            "AND node_execution_id IN (SELECT id FROM node_executions "
            "WHERE node_name = 'champion_selection')",
            "test scoring interrupted: successful audit does not match frozen champion",
        ),
    ],
)
async def test_recovered_score_requires_frozen_audit_chain(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    statement: str,
    message: str,
) -> None:
    h, tools = await champion_harness(migrated_db, project, tmp_path)
    replay = [step.tool for step in pipeline_from_state(h.state, h.state.branches[0]).replay]
    assert "apply_scaler" in replay and "drop_columns" in replay
    tools.interrupt_report = True
    with pytest.raises(asyncio.CancelledError):
        await _run_champion(h)
    tools.interrupt_report = False
    assert h.runtime.conn.execute(statement).rowcount >= 1
    _failed(await _run_champion(h), message)
