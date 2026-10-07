"""Declared traversal, durable outcomes, crash windows and run concurrency."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr
from statlab.config import Settings
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError, LlmCallError, PersistenceError, ToolCallError
from statlab.events import EventBus
from statlab.graph import Checkpointer, NodeExecutor, NodeInvocation, RunLoop, RunState, RunTracker
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import (
    claim_test_scoring,
    create_contract,
    create_run,
    ensure_candidate,
    finish_node_execution,
    get_run,
    next_node_attempt,
    record_tool_call,
    set_test_fingerprint,
)
from statlab.storage import create_artifact, ensure_run_layout, get_artifact
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import Failure, ToolFailure, ToolSuccess
from statlab_contracts.layout import RunPaths

from tests.test_graph_state import candidate_spec, node


@dataclass
class World:
    conn: sqlite3.Connection
    paths: RunPaths
    settings: Settings

    def initial(self, nodes: tuple[NodeSpec, ...], *, candidate: bool = False) -> RunState:
        return RunState.initial(
            run_id=self.paths.run_id,
            spine_id="synthetic",
            nodes=nodes,
            mode="autonomous",
            candidates=(candidate_spec(),) if candidate else (),
        )

    def loop(
        self, nodes: tuple[NodeSpec, ...], executor: NodeExecutor, tracker: RunTracker | None = None
    ) -> RunLoop:
        return RunLoop(
            conn=self.conn,
            nodes=nodes,
            spine_id="synthetic",
            executor=executor,
            checkpointer=Checkpointer(self.paths),
            events=EventBus(self.paths),
            settings=self.settings,
            tracker=tracker or RunTracker(),
        )


def new_world(
    conn: sqlite3.Connection,
    project: ProjectRow,
    contract: DataContract,
    root: Path,
    *,
    data_dir: Path | None = None,
) -> World:
    stored = create_contract(
        conn, project_id=project.id, path=root / "contract.json", contract=contract
    )
    run = create_run(
        conn, project_id=project.id, contract_id=stored.id, mode="autonomous", config_json="{}"
    )
    paths = RunPaths(data_dir or root / "data", project.slug, run.id)
    ensure_run_layout(paths)
    settings = Settings(
        mcp_token=SecretStr("fixture-token"),
        data_dir=paths.data_dir,
        db_path=root / "settings.sqlite",
    )
    return World(conn, paths, settings)


@pytest.fixture
def engine_world(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> World:
    return new_world(migrated_db, project, minimal_contract, tmp_path)


async def complete(invocation: NodeInvocation) -> NodeResult:
    return NodeResult(status="completed", summary={"node": invocation.spec.name})


def artifact(world: World, kind: ArtifactKind = "dataframe") -> ArtifactMeta:
    ref = new_artifact_ref()
    path = world.paths.artifact(ref, kind)
    path.write_bytes(b"fixture")
    meta = ArtifactMeta(ref=ref, kind=kind)
    create_artifact(
        world.conn,
        ref=ref,
        run_id=world.paths.run_id,
        kind=kind,
        path=path,
        meta=meta,
        produced_by="fixture",
    )
    return meta


def log_outputs(world: World, execution_id: str, outputs: tuple[ArtifactMeta, ...]) -> None:
    record_tool_call(
        world.conn,
        run_id=world.paths.run_id,
        node_execution_id=execution_id,
        tool_name="fixture",
        args_json="{}",
        result_json=ToolSuccess(artifacts=outputs).model_dump_json(),
        duration_ms=1,
    )


@pytest.mark.asyncio
async def test_synthetic_three_node_traversal_and_terminal_rerun(engine_world: World) -> None:
    nodes = (node(), node("beta"), node("gamma"))
    seen: list[NodeInvocation] = []

    async def execute(invocation: NodeInvocation) -> NodeResult:
        seen.append(invocation)
        disk = Checkpointer(engine_world.paths).load(
            spine_id="synthetic", spine=invocation.state.spine
        )
        assert disk == invocation.state
        assert len(invocation.state.shared_results) == invocation.state.cursor
        return NodeResult(status="completed", summary={"ok": True}, metrics={"metric": 0.8})

    loop = engine_world.loop(nodes, execute)
    state = await loop.run(engine_world.initial(nodes))
    assert state.status == "completed" and state.cursor == 3
    assert [item.spec.name for item in seen] == [item.name for item in nodes]
    rows = engine_world.conn.execute("SELECT * FROM node_executions ORDER BY rowid").fetchall()
    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert all(row["attempt"] == 1 and row["concurrency_at_start"] == 1 for row in rows)
    assert all(row["status"] == "completed" and row["ended_at"] for row in rows)
    run = get_run(engine_world.conn, state.run_id)
    assert (
        run is not None and run.status == "completed" and run.ended_at and run.current_node is None
    )
    assert await loop.run(engine_world.initial(nodes)) == state
    assert len(seen) == 3
    assert state.shared_results[0].result.metrics == {"metric": 0.8}


@pytest.mark.asyncio
async def test_shared_candidate_shared_separation(engine_world: World) -> None:
    nodes = (node(), node("beta", candidate=True), node("gamma"))
    refs = [artifact(engine_world).ref for _ in nodes]

    async def execute(invocation: NodeInvocation) -> NodeResult:
        assert (invocation.candidate is not None) == (invocation.spec.kind == "PER_CANDIDATE")
        return NodeResult(status="completed", artifacts=(refs[invocation.state.cursor],))

    Checkpointer(engine_world.paths).save(engine_world.initial(nodes, candidate=True))
    result = await engine_world.loop(nodes, execute).run(
        engine_world.initial(nodes, candidate=True)
    )
    assert [boundary.node_name for boundary in result.shared_results] == ["alpha", "gamma"]
    assert result.candidate is not None
    assert [boundary.node_name for boundary in result.candidate.results] == ["beta"]
    assert result.retained_refs == set(refs)
    stored = ensure_candidate(
        engine_world.conn,
        candidate_id=result.candidate.database_id(result.run_id),
        run_id=result.run_id,
        spec=result.candidate.spec,
    )
    assert (
        stored.label == "Human baseline" and stored.spec_json == candidate_spec().model_dump_json()
    )
    rows = engine_world.conn.execute(
        "SELECT candidate_id FROM node_executions ORDER BY rowid"
    ).fetchall()
    assert [row[0] for row in rows] == [None, stored.id, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["skipped", "failed", "awaiting_user"])
async def test_halted_or_skipped_outcome(engine_world: World, outcome: str) -> None:
    nodes = (node(skippable=True), node("beta"))
    if outcome == "skipped":
        result = NodeResult(status="skipped", skip_reason="Not applicable")
    elif outcome == "failed":
        result = NodeResult(status="failed", error=Failure(error_class="data", message="Bad data"))
    else:
        result = NodeResult(status="awaiting_user", summary={"question": "Proceed?"})

    async def execute(invocation: NodeInvocation) -> NodeResult:
        return result if invocation.state.cursor == 0 else NodeResult(status="completed")

    state = await engine_world.loop(nodes, execute).run(engine_world.initial(nodes))
    if outcome == "skipped":
        assert state.cursor == 2 and state.status == "completed"
        assert state.shared_results[0].result.skip_reason == "Not applicable"
    else:
        assert state.cursor == 0 and state.status == outcome
        assert state.pending_result is not None and state.pending_result.result == result
        assert state.shared_results == ()
    run = get_run(engine_world.conn, state.run_id)
    assert run is not None and run.status == state.status
    assert (
        Checkpointer(engine_world.paths).load(spine_id=state.spine_id, spine=state.spine) == state
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "llm", "bug"])
async def test_exception_conversion_preserves_classes_and_hides_secrets(
    engine_world: World, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    failure = Failure(error_class="transient", message="Service unavailable")
    error = ToolCallError("fixture", failure) if kind == "tool" else LlmCallError(failure)
    if kind == "bug":
        error_to_raise: Exception = ValueError("secret credential")
    else:
        error_to_raise = error

    async def execute(invocation: NodeInvocation) -> NodeResult:
        raise error_to_raise

    nodes = (node(),)
    loop = engine_world.loop(nodes, execute)
    result = await loop.run(engine_world.initial(nodes))
    assert result.pending_result is not None
    assert result.pending_result.result.error is not None
    assert result.pending_result.result.error.error_class == (
        "bug" if kind == "bug" else "transient"
    )
    if kind == "bug":
        assert loop.last_error is not None and loop.last_error.cause is error_to_raise
    if kind == "bug":
        assert "Node alpha executor failed with ValueError" in caplog.text
        assert "in execute" in caplog.text
    else:
        assert "executor failed" not in caplog.text
    assert "secret credential" not in caplog.text
    assert "secret credential" not in engine_world.paths.state_json.read_text()
    assert "secret credential" not in engine_world.paths.events_jsonl.read_text()


@pytest.mark.asyncio
async def test_cancellation_recoverable_input_and_incremented_attempt(engine_world: World) -> None:
    nodes = (node(),)
    marker = asyncio.CancelledError("cancel")

    async def cancelled(invocation: NodeInvocation) -> NodeResult:
        raise marker

    loop = engine_world.loop(nodes, cancelled)
    with pytest.raises(asyncio.CancelledError) as raised:
        await loop.run(engine_world.initial(nodes))
    assert raised.value is marker and loop.tracker.concurrency == 0
    state = loop.checkpointer.load(spine_id="synthetic", spine=engine_world.initial(nodes).spine)
    assert state is not None and state.status == "running" and state.cursor == 0
    row = engine_world.conn.execute("SELECT * FROM node_executions").fetchone()
    assert row["status"] == "running" and row["ended_at"] is None
    assert claim_test_scoring(engine_world.conn, state.run_id)
    assert set_test_fingerprint(engine_world.conn, state.run_id, "test-fingerprint")
    result = await engine_world.loop(nodes, complete).run(engine_world.initial(nodes))
    assert result.cursor == 1
    assert [
        row[0]
        for row in engine_world.conn.execute("SELECT attempt FROM node_executions ORDER BY rowid")
    ] == [1, 2]
    run = get_run(engine_world.conn, state.run_id)
    assert run is not None and run.test_scored_at and run.test_fingerprint == "test-fingerprint"


@pytest.mark.asyncio
async def test_concurrent_runs_and_duplicate_refusal(
    engine_world: World, project: ProjectRow, minimal_contract: DataContract, tmp_path: Path
) -> None:
    second = new_world(engine_world.conn, project, minimal_contract, tmp_path)
    tracker = RunTracker()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(invocation: NodeInvocation) -> NodeResult:
        started.set()
        await release.wait()
        return NodeResult(status="completed")

    nodes = (node(),)
    first_loop = engine_world.loop(nodes, slow, tracker)
    task = asyncio.create_task(first_loop.run(engine_world.initial(nodes)))
    await started.wait()
    with pytest.raises(EngineError, match="already"):
        await engine_world.loop(nodes, complete, tracker).run(engine_world.initial(nodes))
    await second.loop(nodes, complete, tracker).run(second.initial(nodes))
    second_row = engine_world.conn.execute(
        "SELECT concurrency_at_start FROM node_executions WHERE run_id = ?", (second.paths.run_id,)
    ).fetchone()
    assert second_row[0] == 2
    release.set()
    await task
    assert tracker.concurrency == 0


@pytest.mark.asyncio
async def test_defective_skip_and_missing_candidate(
    engine_world: World, project: ProjectRow, minimal_contract: DataContract, tmp_path: Path
) -> None:
    async def skip(invocation: NodeInvocation) -> NodeResult:
        return NodeResult(status="skipped", skip_reason="defect")

    nodes = (node(),)
    with pytest.raises(EngineError, match="non-skippable"):
        await engine_world.loop(nodes, skip).run(engine_world.initial(nodes))
    # The declared spine is immutable; a fresh run supplies the candidate topology.
    nodes = (node(candidate=True),)
    # A topology defect is tested on a genuinely fresh run, not erased attempt history.
    fresh = new_world(engine_world.conn, project, minimal_contract, tmp_path)
    with pytest.raises(EngineError, match="requires a candidate"):
        await fresh.loop(nodes, complete).run(fresh.initial(nodes))


@pytest.mark.asyncio
async def test_invalid_initial_and_missing_checkpoint(engine_world: World) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    loop = engine_world.loop(nodes, complete)
    with pytest.raises(EngineError, match="identity"):
        await loop.run(initial.model_copy(update={"spine_id": "wrong"}))
    with pytest.raises(EngineError, match="pending"):
        await loop.run(initial.model_copy(update={"status": "running"}))
    engine_world.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (initial.run_id,))
    with pytest.raises(PersistenceError, match="missing"):
        await loop.run(initial)
    engine_world.conn.execute(
        "UPDATE runs SET status = 'pending', mode = 'guided' WHERE id = ?", (initial.run_id,)
    )
    with pytest.raises(EngineError, match="mode"):
        await loop.run(initial)
    engine_world.conn.execute("DELETE FROM runs WHERE id = ?", (initial.run_id,))
    with pytest.raises(PersistenceError, match="exist"):
        await loop.run(initial)


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_intermediates", [False, True])
async def test_prune_exact_execution_after_boundary_and_keep_all_outputs(
    engine_world: World, keep_intermediates: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_world.settings.keep_intermediates = keep_intermediates
    nodes = (node(), node("beta", candidate=True))
    earlier, retained, intermediate = (
        artifact(engine_world),
        artifact(engine_world),
        artifact(engine_world),
    )
    model = artifact(engine_world, "model")
    transformer = artifact(engine_world, "transformer")
    report = artifact(engine_world, "report")
    unrelated = artifact(engine_world)

    async def execute(invocation: NodeInvocation) -> NodeResult:
        if invocation.state.cursor == 0:
            log_outputs(engine_world, invocation.execution.id, (earlier,))
            return NodeResult(status="completed", artifacts=(earlier.ref,))
        log_outputs(
            engine_world,
            invocation.execution.id,
            (retained, intermediate, model, transformer, report),
        )
        return NodeResult(status="completed", artifacts=(retained.ref, model.ref))

    loop = engine_world.loop(nodes, execute)
    loop.checkpointer.save(engine_world.initial(nodes, candidate=True))
    original_prune = loop._prune

    def checked_prune(execution_id: str, state: RunState) -> None:
        assert loop.checkpointer.load(spine_id=state.spine_id, spine=state.spine) == state
        original_prune(execution_id, state)

    monkeypatch.setattr(loop, "_prune", checked_prune)
    await loop.run(engine_world.initial(nodes, candidate=True))
    for meta in (earlier, retained, model, transformer, report, unrelated):
        assert engine_world.paths.artifact(meta.ref, meta.kind).exists()
    row = get_artifact(engine_world.conn, str(intermediate.ref))
    assert row is not None
    assert bool(row.pruned_at) == (not keep_intermediates)
    assert (
        engine_world.paths.artifact(intermediate.ref, intermediate.kind).exists()
        == keep_intermediates
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "skipped", "awaiting_user", "cancelled"])
async def test_no_prune_on_noncompletion(engine_world: World, outcome: str) -> None:
    meta = artifact(engine_world)
    nodes = (node(skippable=True),)

    async def execute(invocation: NodeInvocation) -> NodeResult:
        log_outputs(engine_world, invocation.execution.id, (meta,))
        if outcome == "cancelled":
            raise asyncio.CancelledError
        if outcome == "failed":
            return NodeResult(
                status="failed", error=Failure(error_class="data", message="Bad data")
            )
        if outcome == "skipped":
            return NodeResult(status="skipped", skip_reason="unused")
        return NodeResult(status="awaiting_user")

    loop = engine_world.loop(nodes, execute)
    loop.checkpointer.save(engine_world.initial(nodes))
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await loop.run(engine_world.initial(nodes))
    else:
        await loop.run(engine_world.initial(nodes))
    row = get_artifact(engine_world.conn, str(meta.ref))
    assert row is not None and row.pruned_at is None
    assert Path(row.path).exists()


@pytest.mark.asyncio
async def test_checkpoint_failure_after_sql_does_not_prune_and_replays(
    engine_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodes = (node(),)
    output = artifact(engine_world)

    async def execute(invocation: NodeInvocation) -> NodeResult:
        log_outputs(engine_world, invocation.execution.id, (output,))
        return NodeResult(status="completed")

    loop = engine_world.loop(nodes, execute)
    loop.checkpointer.save(engine_world.initial(nodes))
    original = loop.checkpointer.save

    def fail_boundary(state: RunState) -> None:
        if state.cursor == 1:
            raise PersistenceError("injected boundary write failure")
        original(state)

    monkeypatch.setattr(loop.checkpointer, "save", fail_boundary)
    with pytest.raises(PersistenceError, match="injected"):
        await loop.run(engine_world.initial(nodes))
    assert engine_world.paths.artifact(output.ref, output.kind).exists()
    row = engine_world.conn.execute("SELECT status FROM node_executions").fetchone()
    assert row[0] == "completed"
    assert (
        next_node_attempt(
            engine_world.conn,
            run_id=engine_world.paths.run_id,
            node_name="alpha",
            candidate_id=None,
        )
        == 2
    )
    result = await engine_world.loop(nodes, complete).run(engine_world.initial(nodes))
    assert result.status == "completed"


def test_repository_seams_fail_closed(engine_world: World) -> None:
    conn = engine_world.conn
    with pytest.raises(PersistenceError, match="does not exist"):
        finish_node_execution(conn, "missing", NodeResult(status="completed"))
    spec = candidate_spec()
    ensure_candidate(conn, candidate_id="candidate", run_id=engine_world.paths.run_id, spec=spec)
    with pytest.raises(PersistenceError, match="mismatch"):
        ensure_candidate(conn, candidate_id="candidate", run_id="other", spec=spec)
    with pytest.raises(PersistenceError, match="persist candidate"):
        ensure_candidate(conn, candidate_id="other", run_id="missing", spec=spec)
    conn.close()
    with pytest.raises(PersistenceError, match="attempts"):
        next_node_attempt(conn, run_id="r", node_name="n", candidate_id=None)
    with pytest.raises(PersistenceError, match="outcome"):
        finish_node_execution(conn, "missing", NodeResult(status="completed"))


@pytest.mark.asyncio
async def test_empty_topology_and_final_cursor_recovery(engine_world: World) -> None:
    state = await engine_world.loop((), complete).run(engine_world.initial(()))
    assert state.status == "completed" and state.cursor == 0
    # Last committed node may precede the final run-status checkpoint.
    running = state.model_copy(update={"status": "running"})
    Checkpointer(engine_world.paths).save(running)
    engine_world.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (state.run_id,))
    assert (
        await engine_world.loop((), complete).run(engine_world.initial(()))
    ).status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["run", "spine", "events", "cursor"])
async def test_other_identity_and_input_defects(engine_world: World, change: str) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    loop = engine_world.loop(nodes, complete)
    if change == "run":
        initial = initial.model_copy(update={"run_id": "foreign"})
    elif change == "spine":
        initial = initial.model_copy(update={"spine": ()})
    elif change == "events":
        loop.events = EventBus(RunPaths(engine_world.paths.data_dir, "other", initial.run_id))
    else:
        from statlab.graph import BoundaryResult

        initial = RunState(
            run_id=initial.run_id,
            spine_id=initial.spine_id,
            spine=initial.spine,
            cursor=1,
            mode=initial.mode,
            shared_results=(
                BoundaryResult(
                    node_name=nodes[0].name,
                    execution_id="ne_prior",
                    result=NodeResult(status="completed"),
                ),
            ),
        )
    with pytest.raises(EngineError):
        await loop.run(initial)


def test_prune_audit_refusals_and_failed_logged_call(engine_world: World) -> None:
    nodes = (node(),)
    loop, state = engine_world.loop(nodes, complete), engine_world.initial(nodes)
    from statlab.persistence.repository import create_node_execution

    execution = create_node_execution(
        engine_world.conn, run_id=state.run_id, node_name="alpha", seq=1
    )
    record_tool_call(
        engine_world.conn,
        run_id=state.run_id,
        node_execution_id=execution.id,
        tool_name="fixture",
        args_json="{}",
        duration_ms=1,
        result_json=ToolFailure(error_class="data", message="Bad data").model_dump_json(),
    )
    loop._prune(execution.id, state)
    engine_world.conn.execute("UPDATE tool_calls SET result_json = 'bad'")
    with pytest.raises(PersistenceError, match="audit"):
        loop._prune(execution.id, state)
    unknown = ArtifactMeta(ref=new_artifact_ref(), kind="dataframe")
    engine_world.conn.execute(
        "UPDATE tool_calls SET result_json = ?",
        (ToolSuccess(artifacts=(unknown,)).model_dump_json(),),
    )
    with pytest.raises(PersistenceError, match="not registered"):
        loop._prune(execution.id, state)
    engine_world.conn.close()
    with pytest.raises(PersistenceError, match="audit"):
        loop._prune(execution.id, state)


@pytest.mark.asyncio
async def test_closed_database_and_prune_io_are_domain_errors(
    engine_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodes = (node(),)
    loop = engine_world.loop(nodes, complete)

    def fail_prune(execution_id: str, state: RunState) -> None:
        raise OSError("injected prune IO failure")

    monkeypatch.setattr(loop, "_prune", fail_prune)
    with pytest.raises(PersistenceError, match="operation"):
        await loop.run(engine_world.initial(nodes))
    assert loop.tracker.concurrency == 0
    engine_world.conn.close()
    with pytest.raises(PersistenceError, match="operation"):
        await engine_world.loop(nodes, complete).run(engine_world.initial(nodes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history", ["node", "candidate", "artifact", "score", "fingerprint", "events"]
)
async def test_missing_checkpoint_refuses_reset_pending_history(
    engine_world: World, history: str
) -> None:
    from statlab.persistence.repository import create_node_execution

    nodes = (node(),)
    loop = engine_world.loop(nodes, complete)
    initial = engine_world.initial(nodes)
    if history == "node":
        create_node_execution(engine_world.conn, run_id=initial.run_id, node_name="alpha", seq=1)
    elif history == "candidate":
        ensure_candidate(
            engine_world.conn,
            candidate_id="prior-candidate",
            run_id=initial.run_id,
            spec=candidate_spec(),
        )
    elif history == "artifact":
        artifact(engine_world)
    elif history == "score":
        assert claim_test_scoring(engine_world.conn, initial.run_id)
    elif history == "fingerprint":
        assert set_test_fingerprint(engine_world.conn, initial.run_id, "locked")
    else:
        await loop.events.append("run.status", {"status": "running"})
    before = get_run(engine_world.conn, initial.run_id)
    with pytest.raises(PersistenceError, match="durable history"):
        await loop.run(initial)
    assert get_run(engine_world.conn, initial.run_id) == before
    assert not engine_world.paths.state_json.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["paused", "awaiting_user", "completed", "failed", "cancelled", "superseded"]
)
async def test_direct_loop_refuses_sql_control_ahead_of_running_checkpoint(
    engine_world: World, status: str
) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    loop = engine_world.loop(nodes, complete)
    loop.checkpointer.save(initial.model_copy(update={"status": "running"}))
    engine_world.conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, initial.run_id))
    before = engine_world.paths.state_json.read_bytes()
    with pytest.raises(EngineError, match="refuses execution"):
        await loop.run(initial)
    assert engine_world.paths.state_json.read_bytes() == before
    assert not engine_world.conn.execute("SELECT * FROM node_executions").fetchall()


def test_history_reader_closed_database_is_domain_error(engine_world: World) -> None:
    from statlab.persistence.repository import has_run_history

    with pytest.raises(PersistenceError, match="does not exist"):
        has_run_history(engine_world.conn, "missing")
    engine_world.conn.close()
    with pytest.raises(PersistenceError, match="history"):
        has_run_history(engine_world.conn, "missing")
