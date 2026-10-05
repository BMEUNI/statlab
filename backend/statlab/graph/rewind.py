"""Validated declared-frontier projection and durable, caller-scheduled fork publication."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, TypeAdapter
from statlab_contracts.gates import RewindIntent
from statlab_contracts.layout import RunPaths

from statlab.errors import EngineError, PersistenceError
from statlab.graph.checkpoint import Checkpointer
from statlab.graph.control import ControlContext, ControlResponse
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.router import InterruptClass
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.persistence.db import transaction
from statlab.persistence.ids import utc_now
from statlab.persistence.repository import (
    create_run,
    ensure_candidate,
    get_run,
    list_runs,
    run_ancestry,
    set_test_fingerprint,
)
from statlab.storage import ensure_run_layout, fork_artifacts, validate_checkpoint_ownership


def project_frontier(state: RunState, target: str) -> RunState:
    """Use declared topology; retain genuine execution IDs, never fabricate outcomes."""
    names = tuple(entry.name for entry in state.spine)
    if target not in names:
        raise EngineError("rewind target is not declared")
    index = names.index(target)
    if (
        index > state.cursor
        and not any(item.node_name == target for item in state.shared_results)
        and not any(branch.position >= index for branch in state.branches)
    ):
        raise EngineError("rewind frontier has not been reached")
    traversal = state.traversal
    start, end = (
        traversal.region(state.spine) if traversal is not None else (len(names), len(names))
    )
    if traversal is None and any(entry.kind == "PER_CANDIDATE" for entry in state.spine):
        positions = tuple(i for i, entry in enumerate(state.spine) if entry.kind == "PER_CANDIDATE")
        start, end = positions[0], positions[-1] + 1
    tail = index >= end
    if tail:
        if traversal is not None and any(
            branch.status in ("pending", "running", "awaiting_user") for branch in state.branches
        ):
            raise EngineError("shared-tail rewind requires terminal candidate frontiers")
        branches = state.branches
        shared = tuple(item for item in state.shared_results if names.index(item.node_name) < index)
        wave = state.wave
    else:
        design = traversal.design_node if traversal is not None else None
        design_index = names.index(design) if design is not None else None
        design_history = tuple(item for item in state.shared_results if item.node_name == design)
        selected: dict[str, BoundaryResult] = {}
        for boundary in state.shared_results:
            if names.index(boundary.node_name) < index:
                selected[boundary.node_name] = boundary
        shared = tuple(selected[name] for name in names[:index] if name in selected)
        expected_shared = tuple(
            entry.name for entry in state.spine[:index] if entry.kind == "SHARED"
        )
        if tuple(boundary.node_name for boundary in shared) != expected_shared:
            raise EngineError("required preceding shared boundary is unavailable")
        keep_specs = design_index is None or index > design_index
        approved = (
            tuple(
                branch
                for branch in state.branches
                if design is None or branch.wave <= len(design_history)
            )
            if keep_specs
            else ()
        )
        projected: list[CandidateProgress] = []
        for branch in approved:
            position = max(start, index)
            required = names[start:position] if index >= start else ()
            prefix = branch.results[: len(required)]
            if tuple(boundary.node_name for boundary in prefix) != required:
                raise EngineError("candidate lacks first-pass boundaries before requested frontier")
            projected.append(
                branch.model_copy(
                    update={
                        "results": prefix,
                        "wave": 1,
                        "position": position,
                        "retune_count": 0,
                        "pending_result": None,
                        "status": "pending",
                    }
                )
            )
        branches, wave = tuple(projected), 1
        if start <= index < end and not branches:
            raise EngineError("candidate frontier has no approved specifications")
    metadata: dict[str, JsonValue] = {
        "source_run_id": state.run_id,
        "source_wave": state.wave,
        "target": target,
        "projection": "terminal_histories" if tail else "first_pass_wave1",
        "source_candidates": {
            branch.public_id: {"wave": branch.wave, "retune_count": branch.retune_count}
            for branch in state.branches
        },
    }
    return RunState.model_validate(
        {
            **state.model_dump(),
            "cursor": index,
            "status": "pending",
            "pending_result": None,
            "shared_results": shared,
            "branches": branches,
            "wave": wave,
            "budget": None,
            "fork_metadata": metadata,
        }
    )


@dataclass(frozen=True, slots=True)
class ForkResult:
    new_run_id: str
    state: RunState
    lifecycle: RunLifecycle


type ForkResolver = Callable[[RunPaths, RunState], RunLifecycle]
type ForkPublisher = Callable[[RunPaths, RunPaths, RunState], Awaitable[None]]


class ForkService:
    def __init__(
        self,
        parent: RunLifecycle,
        *,
        resolver: ForkResolver,
        publish_domain: ForkPublisher | None = None,
    ) -> None:
        self.parent, self.resolver, self.publish_domain = parent, resolver, publish_domain
        self.conn, self.paths = parent.loop.conn, parent.loop.checkpointer.paths

    def _existing(self, request_id: str) -> ForkResult | None:
        parent_row = get_run(self.conn, self.paths.run_id)
        if parent_row is None:
            raise PersistenceError("fork parent does not exist")
        for row in list_runs(self.conn, parent_row.project_id):
            if row.forked_from_run_id != parent_row.id:
                continue
            config = TypeAdapter(dict[str, JsonValue]).validate_json(row.config_json)
            fork = config.get("fork")
            if (
                isinstance(fork, dict)
                and fork.get("request_id") == request_id
                and fork.get("published") is True
            ):
                paths = RunPaths(self.paths.data_dir, self.paths.slug, row.id)
                state = Checkpointer(paths).load(
                    spine_id=self.parent.initial.spine_id, spine=self.parent.initial.spine
                )
                if state is None:
                    raise PersistenceError("published fork checkpoint is unavailable")
                return ForkResult(row.id, state, self._resolve(paths, state))
        return None

    def _resolve(self, paths: RunPaths, state: RunState) -> RunLifecycle:
        lifecycle = self.resolver(paths, state)
        if (
            lifecycle.loop.conn is not self.conn
            or lifecycle.initial != state
            or lifecycle.loop.checkpointer.paths != paths
            or lifecycle.loop.events.paths != paths
            or lifecycle.loop.tracker is not self.parent.loop.tracker
            or lifecycle.loop.control is self.parent.loop.control
        ):
            raise EngineError("fork resolver must return a fresh correctly bound lifecycle")
        return lifecycle

    async def fork(self, target: str, *, request_id: str | None = None) -> ForkResult:
        # Check obvious ownership/target failures before changing the parent's lifecycle.
        if target not in {spec.name for spec in self.parent.loop.nodes}:
            raise EngineError("rewind target is not declared")
        await self.parent.halt_for_fork()
        async with self.parent.exclusive():
            if request_id is not None:
                existing = self._existing(request_id)
                if existing is not None:
                    await self._finish_parent(
                        await self.parent.state(),
                        existing.new_run_id,
                        {
                            **existing.state.fork_metadata,
                            "request_id": request_id,
                            "published": True,
                        },
                    )
                    return existing
            source = await self.parent.state()
            parent_row = get_run(self.conn, source.run_id)
            if parent_row is None:
                raise PersistenceError("fork parent does not exist")
            projection = project_frontier(source, target)
            retained = validate_checkpoint_ownership(self.conn, source.run_id, projection)
            if any(row.pruned_at is not None or not Path(row.path).is_file() for row in retained):
                raise PersistenceError("required retained artifact file is missing or pruned")
            immutable = ("contract.snapshot.json", "skill.snapshot.md", "uv.lock")
            for name in immutable[:2]:
                if not (self.paths.root / name).is_file():
                    raise PersistenceError("fork requires immutable contract and skill inputs")
            config = TypeAdapter(dict[str, JsonValue]).validate_json(parent_row.config_json)
            config["fork"] = {
                **projection.fork_metadata,
                "request_id": request_id,
                "published": False,
            }
            child = create_run(
                self.conn,
                project_id=parent_row.project_id,
                contract_id=parent_row.contract_id,
                mode=source.mode,
                config_json=json.dumps(config),
                forked_from_run_id=source.run_id,
                status="cancelled",
            )
            # Unpublished rows never enter restart scheduling, including interruption during IO.
            paths = RunPaths(self.paths.data_dir, self.paths.slug, child.id)
            child_state = projection.model_copy(update={"run_id": child.id})
            try:
                ensure_run_layout(paths)
                for name in immutable:
                    path = self.paths.root / name
                    if path.is_file():
                        (paths.root / name).write_bytes(path.read_bytes())
                linked = fork_artifacts(self.paths, paths, retained)
                if frozenset(linked) != child_state.retained_refs:
                    raise PersistenceError("fork did not link every retained artifact")
                for branch in child_state.branches:
                    ensure_candidate(
                        self.conn,
                        candidate_id=branch.database_id(child.id),
                        run_id=child.id,
                        spec=branch.spec,
                        wave=branch.wave,
                    )
                # A complete retained split determines the inherited fingerprint; claims stay new.
                split_runs = {
                    row.run_id for row in retained if row.split in ("train", "valid", "test")
                }
                for ancestor in run_ancestry(self.conn, source.run_id):
                    roles = {
                        row.split
                        for row in retained
                        if row.run_id == ancestor.id and row.kind == "dataframe"
                    }
                    if (
                        ancestor.id in split_runs
                        and {"train", "valid", "test"} <= roles
                        and ancestor.test_fingerprint is not None
                    ):
                        set_test_fingerprint(self.conn, child.id, ancestor.test_fingerprint)
                        break
                Checkpointer(paths).save(child_state)
                if self.publish_domain is not None:
                    await self.publish_domain(self.paths, paths, child_state)
                lifecycle = self._resolve(paths, child_state)
                published = {
                    **child_state.fork_metadata,
                    "request_id": request_id,
                    "published": True,
                }
                config["fork"] = published
                with transaction(self.conn):
                    self.conn.execute(
                        "UPDATE runs SET status = 'pending', config_json = ?, ended_at = NULL "
                        "WHERE id = ?",
                        (json.dumps(config), child.id),
                    )
                    if parent_row.status != "completed":
                        self.conn.execute(
                            "UPDATE runs SET status = 'superseded', ended_at = ? WHERE id = ?",
                            (utc_now(), source.run_id),
                        )
                await self._finish_parent(source, child.id, published)
                return ForkResult(child.id, child_state, lifecycle)
            except (sqlite3.Error, OSError, ValueError) as exc:
                raise PersistenceError(
                    "fork publication interrupted; inspect durable publication status", cause=exc
                ) from exc

    async def _finish_parent(
        self, source: RunState, child_id: str, published: dict[str, JsonValue]
    ) -> None:
        row = get_run(self.conn, source.run_id)
        if row is None:
            raise PersistenceError("fork parent does not exist")
        if row.status != "completed":
            halted = source.model_copy(update={"status": "superseded"})
            self.parent.loop.checkpointer.save(halted)
            if source.status != "superseded":
                await self.parent.loop._status(halted, str(published["target"]))
        payload: dict[str, JsonValue] = {"new_run_id": child_id, **published}
        if not any(
            event.type == "run.forked" and event.payload == payload
            for event in await self.parent.loop.events.replay()
        ):
            await self.parent.loop.events.append("run.forked", payload)

    async def gate_rewind(self, target: str, request_id: str) -> str:
        return (await self.fork(target, request_id=request_id)).new_run_id

    async def handle(
        self, message: str, classified: InterruptClass, context: ControlContext
    ) -> ControlResponse:
        if (
            classified.category != "change"
            or context.conn is not self.conn
            or context.state.run_id != self.paths.run_id
        ):
            raise EngineError("fork control binding mismatch")
        intent = await context.llm.complete(
            context.conn,
            run_id=self.paths.run_id,
            node_execution_id=context.audit_execution_id,
            model=context.router.model_for("interrupt_router"),
            reasoning=False,
            output=RewindIntent,
            messages=(
                {
                    "role": "system",
                    "content": "Parse the user's explicit rewind target. Declared nodes: "
                    + ", ".join(entry.name for entry in context.state.spine)
                    + ". Do not invent parameter changes or targets.",
                },
                {"role": "user", "content": message},
            ),
            recovery=context.recovery,
        )
        fork = await self.fork(intent.target)
        return ControlResponse(
            category="change",
            text=(
                f"Fork published at {intent.target}; "
                f"source wave {fork.state.fork_metadata['source_wave']}; "
                f"{fork.state.fork_metadata['projection']}."
            ),
            new_run_id=fork.new_run_id,
        )
