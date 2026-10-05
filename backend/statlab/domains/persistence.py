"""Preserve the audited computation, immutable inputs and deterministic outcome."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from pydantic import JsonValue, TypeAdapter, ValidationError
from statlab_contracts.envelope import Failure

from statlab.domains.base import NodeResult
from statlab.domains.decisions import PersistenceDecision
from statlab.domains.foundation import (
    _Evidence,
    _object,
    _ref,
    _RefusalError,
    problem_from_state,
    shared_result,
)
from statlab.domains.rewind import champion_provenance
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError, PersistenceError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.mcp_client import logged_calls
from statlab.persistence.repository import get_run, remember_run_outcome


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_manifest(path: Path, manifest: dict[str, JsonValue]) -> None:
    """Publish complete metadata; retain snapshot_run's original content hash."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PersistenceExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        if invocation.spec.name != "artifact_persistence" or invocation.candidate is not None:
            raise EngineError("persistence executor requires shared artifact persistence")
        evidence = _Evidence()
        try:
            self.runtime.context(invocation)
            return await self._execute(invocation, evidence)
        except _RefusalError as exc:
            return evidence.result(
                "failed", {}, error=Failure(error_class="data", message=str(exc))
            )
        except ToolCallError as exc:
            return evidence.result("failed", {}, error=exc.failure)
        except (OSError, ValueError, sqlite3.Error, PersistenceError) as exc:
            raise EngineError("artifact preservation failed", cause=exc) from exc

    async def _execute(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        runtime = self.runtime
        champion = shared_result(invocation.state, "champion_selection").summary
        target = _ref(champion, "test_score")
        problem = problem_from_state(invocation.state)
        run = get_run(runtime.conn, runtime.paths.run_id)
        provenance, model = champion_provenance(runtime.conn, invocation.state)
        if run is None:
            raise _RefusalError("persistence requires durable champion and completed test score")
        if model.id != champion.get("model_id") or model.artifact_id != champion.get("model"):
            raise _RefusalError("completed champion disagrees with durable model")
        lockfile = runtime.lockfile_path
        if lockfile is None:
            raise _RefusalError("persistence requires caller-supplied readable lockfile_path")
        try:
            lock_bytes = lockfile.read_bytes()
        except OSError as exc:
            raise _RefusalError("caller-supplied lockfile is unreadable", cause=exc) from exc
        preserved = runtime.paths.root / "uv.lock"
        if preserved.exists() and preserved.read_bytes() != lock_bytes:
            raise _RefusalError("run lockfile already differs from supplied bytes")
        if runtime.paths.skill_snapshot_md.read_bytes() != runtime.skill.original_bytes:
            raise _RefusalError("immutable run skill snapshot differs from policy")
        report = _object(champion.get("report"), "champion report")
        # Report location comes from the fixed tool contract, never arbitrary narrative paths.
        report_path = runtime.paths.report / "report.md"
        report_hash = _digest(report_path)
        if report.get("sha256") != report_hash:
            raise PersistenceError("preserved report differs from completed champion report")
        calls = logged_calls(
            runtime.conn, runtime.paths.run_id, fork_aware=True, checkpoint=invocation.state
        )
        if str(target) not in {ref for call in calls for ref in call.outputs}:
            raise _RefusalError("champion test score is absent from real tool audit")
        decision = PersistenceDecision(
            rationale="Preserve exact audited computation and immutable run inputs"
        )
        proposal: dict[str, JsonValue] = {
            "target": str(target),
            "model_id": model.id,
            "report_sha256": report_hash,
            "skill_sha256": runtime.skill.sha256,
            "lockfile_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "calls": [call.model_dump(mode="json") for call in calls],
            "operations": [
                "preserve_lockfile",
                "export_pipeline",
                "snapshot_run",
                "remember_outcome",
            ],
        }
        approved = await runtime.approve(invocation, decision, (proposal,))
        if approved is None:
            return evidence.result(
                "awaiting_user",
                {"decision": decision.model_dump(mode="json"), "proposal": proposal},
            )
        decision = approved
        if preserved.exists():
            if preserved.read_bytes() != lock_bytes:
                raise _RefusalError("run lockfile already differs from supplied bytes")
        else:
            # Exclusive creation preserves the first immutable input even on retry.
            with preserved.open("xb") as stream:
                stream.write(lock_bytes)
        exported = await runtime.call(
            invocation,
            "export_pipeline_script",
            {},
            trusted={
                "calls": [call.model_dump(mode="json") for call in calls],
                "target": str(target),
            },
        )
        evidence.add("export_pipeline_script", exported)
        snapshot_calls = logged_calls(
            runtime.conn, runtime.paths.run_id, fork_aware=True, checkpoint=invocation.state
        )
        snapshot = await runtime.call(
            invocation,
            "snapshot_run",
            {},
            trusted={
                "calls": [call.model_dump(mode="json") for call in snapshot_calls],
            },
        )
        evidence.add("snapshot_run", snapshot)
        try:
            manifest = TypeAdapter(dict[str, JsonValue]).validate_json(
                runtime.paths.manifest_json.read_bytes()
            )
        except ValidationError as exc:
            raise PersistenceError("snapshot manifest is malformed", cause=exc) from exc
        if manifest.get("run_hash") != snapshot.summary.get("run_hash"):
            raise PersistenceError("snapshot manifest disagrees with returned run hash")
        audit_models = runtime.conn.execute(
            "SELECT DISTINCT model FROM llm_calls WHERE run_id = ? ORDER BY model",
            (runtime.paths.run_id,),
        ).fetchall()
        manifest["preservation"] = {
            "score_run_id": provenance.id,
            "inherited_score": provenance.id != run.id,
            "test_scored_at": provenance.test_scored_at,
            "fork": invocation.state.fork_metadata,
            "skill_sha256": runtime.skill.sha256,
            "lockfile_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "node_models": {
                skill.node: {
                    "role": runtime.router.role_for(skill.node).value,
                    "model": runtime.router.model_for(skill.node, skill_model=skill.model),
                }
                for skill in runtime.skill.nodes
            },
            "audited_llm_models": [str(row["model"]) for row in audit_models],
            "hash_semantics": "Preservation metadata is not an input to snapshot run_hash.",
        }
        manifest["files"] = [
            {
                "path": str(path.relative_to(runtime.paths.root)),
                "sha256": _digest(path),
                "size": path.stat().st_size,
            }
            for path in sorted(runtime.paths.root.rglob("*"))
            if path.is_file() and path != runtime.paths.manifest_json
        ]
        _atomic_manifest(runtime.paths.manifest_json, manifest)
        fact: dict[str, JsonValue] = {
            "target": problem.target,
            "task": problem.task,
            "estimator": model.name,
            "candidate_id": champion.get("candidate_id"),
            "primary_metric": problem.primary_metric,
            "selection_bias": champion.get("selection_bias"),
            "prior_test_scorings": champion.get("prior_test_scorings"),
            "n_waves": champion.get("n_waves"),
            "validation_ranking": champion.get("ranking"),
            "excluded": champion.get("excluded"),
        }
        memory = remember_run_outcome(
            runtime.conn, project_id=run.project_id, run_id=run.id, value=fact
        )
        return evidence.result(
            "completed",
            {
                "decision": decision.model_dump(mode="json"),
                "target": str(target),
                "export": exported.summary,
                "snapshot": snapshot.summary,
                "pipeline_sha256": _digest(runtime.paths.pipeline_py),
                "report_sha256": report_hash,
                "manifest_sha256": _digest(runtime.paths.manifest_json),
                "skill_sha256": runtime.skill.sha256,
                "lockfile_sha256": _digest(preserved),
                "run_hash": manifest["run_hash"],
                "memory_id": memory.id,
            },
        )
