"""Global (G1-G7), expectation and soft checks."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue

from integration.model import Scenario

if TYPE_CHECKING:
    from integration.driver import RunOutcome
    from integration.evidence import Evidence, Row

TERMINAL = {"completed", "failed", "cancelled", "superseded"}


@dataclass(frozen=True)
class CheckResult:
    id: str
    description: str
    severity: Literal["must", "soft"]
    passed: bool
    detail: str


def _must(check_id: str, description: str, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(check_id, description, "must", passed, detail)


def _soft(check_id: str, description: str, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(check_id, description, "soft", passed, detail)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _payload(event: Mapping[str, object]) -> Mapping[str, object]:
    inner = event.get("payload")
    return inner if isinstance(inner, dict) else {}


def _question(gate: Row) -> dict[str, object]:
    raw = gate.get("question_json")
    parsed = json.loads(raw) if isinstance(raw, str) else {}
    return parsed if isinstance(parsed, dict) else {}


def _branches(evidence: Evidence) -> list[dict[str, object]]:
    if evidence.state is None:
        return []
    branches = evidence.state.get("branches")
    items = list(branches.values()) if isinstance(branches, dict) else branches
    if not isinstance(items, list):
        return []
    return [{str(k): v for k, v in item.items()} for item in items if isinstance(item, dict)]


def _test_scorings(evidence: Evidence) -> int:
    test_ids = {
        str(row["id"])
        for row in evidence.artifacts
        if row.get("split") == "test" or row.get("derived_from_test") == 1
    }
    count = 0
    for call in evidence.tool_calls:
        name = call.get("tool_name")
        args = call.get("args_json")
        if not isinstance(name, str) or not name.startswith("score_") or not isinstance(args, str):
            continue
        if any(value in test_ids for value in _strings(json.loads(args))):
            count += 1
    return count


def global_checks(
    *,
    scenario: Scenario,
    outcome: RunOutcome,
    evidence: Evidence,
    api_log_slice: str,
    mcp_alive: bool,
    live: bool,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(
        _must(
            "G1",
            "no ERROR in api log; MCP alive",
            " ERROR " not in api_log_slice and mcp_alive,
            f"mcp_alive={mcp_alive}",
        )
    )

    expected = scenario.expect.status
    results.append(
        _must(
            "G2",
            "expected terminal status within timeout and budget",
            not outcome.timed_out
            and not outcome.budget_stopped
            and (expected is None or outcome.final_status == expected),
            f"final={outcome.final_status} expected={expected} "
            f"timed_out={outcome.timed_out} budget_stopped={outcome.budget_stopped}",
        )
    )

    server_seqs = [event.get("seq") for event in evidence.server_events]
    seq_ok = server_seqs == list(range(1, len(server_seqs) + 1))
    sse: dict[object, object] = {}
    for event in outcome.events:
        sse.setdefault(event.get("seq"), event.get("type"))
    server_pairs = {event.get("seq"): event.get("type") for event in evidence.server_events}
    sse_ok = sse == server_pairs
    pairs_ok = True
    if outcome.final_status == "completed":
        events = evidence.server_events
        gate_exec: dict[object, object] = {}
        for event in events:
            if event.get("type") == "gate.opened":
                payload = _payload(event)
                if payload.get("gate_id") is not None and payload.get("node_execution_id"):
                    gate_exec[payload["gate_id"]] = payload["node_execution_id"]
        closed = {
            gate_exec[_payload(event).get("gate_id")]
            for event in events
            if event.get("type") == "gate.closed" and _payload(event).get("gate_id") in gate_exec
        }
        for index, event in enumerate(events):
            if event.get("type") != "node.started":
                continue
            execution_id = _payload(event).get("execution_id")
            pairs_ok = pairs_ok and (
                execution_id in closed
                or any(
                    later.get("type") in ("node.completed", "node.failed", "node.skipped")
                    and _payload(later).get("execution_id") == execution_id
                    for later in events[index + 1 :]
                )
            )
    results.append(
        _must(
            "G3",
            "event sequence complete, SSE equals server log, started nodes finished",
            seq_ok and sse_ok and pairs_ok,
            f"seq_ok={seq_ok} sse_ok={sse_ok} pairs_ok={pairs_ok}",
        )
    )

    dangling = [
        row.get("node_name")
        for row in evidence.node_executions
        if row.get("status") in ("running", "pending")
    ]
    results.append(
        _must(
            "G4",
            "no node execution left running/pending after terminal status",
            outcome.final_status not in TERMINAL or not dangling,
            f"dangling={dangling}",
        )
    )

    scorings = _test_scorings(evidence)
    has_champion = evidence.run.get("champion_model_id") is not None
    results.append(
        _must(
            "G5",
            "at most one test scoring; exactly one iff champion",
            scorings <= 1 and (scorings == 1) == has_champion,
            f"scorings={scorings} champion={has_champion}",
        )
    )

    execution_ids = {row.get("id") for row in evidence.node_executions}
    run_id = evidence.run.get("id")
    bad_calls = [
        row.get("id")
        for row in evidence.llm_calls
        if row.get("run_id") != run_id or row.get("node_execution_id") not in execution_ids
    ]
    results.append(
        _must("G6", "llm_calls belong to this run", not bad_calls, f"unattached={bad_calls}")
    )

    if live:
        cost = sum(float(str(row.get("cost_usd") or 0)) for row in evidence.llm_calls)
        results.append(
            _must(
                "G7",
                "scenario cost within max_cost_usd",
                cost <= scenario.max_cost_usd,
                f"cost={cost:.4f} max={scenario.max_cost_usd}",
            )
        )
    else:
        results.append(_must("G7", "scenario cost within max_cost_usd", True, "not live"))
    return results


def recorded_test_metric(evidence: Evidence) -> float | None:
    if evidence.contract_snapshot is None:
        return None
    try:
        snapshot = json.loads(evidence.contract_snapshot)
        metric = snapshot["objective"]["primary_metric"]
        rows = [
            row
            for row in evidence.node_executions
            if row.get("node_name") == "champion_selection" and row.get("status") == "completed"
        ]
        summary = json.loads(str(rows[-1]["summary"]))
        value = summary["test_metrics"]["metrics"][metric]["value"]
        return float(value)
    except KeyError, IndexError, TypeError, ValueError:
        return None


def _primary_metric(evidence: Evidence) -> str | None:
    if evidence.contract_snapshot is None:
        return None
    try:
        return str(json.loads(evidence.contract_snapshot)["objective"]["primary_metric"])
    except KeyError, TypeError, ValueError:
        return None


def _replay(evidence: Evidence, scenario_dir: Path) -> CheckResult:
    recorded = recorded_test_metric(evidence)
    metric = _primary_metric(evidence)
    if recorded is None or metric is None or evidence.pipeline_path is None:
        return _must("E-pipeline_replay", "pipeline.py replay", False, "no recorded test metric")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and the run's own pipeline.
            [
                sys.executable,
                str(evidence.pipeline_path.resolve()),
                str((scenario_dir / "data.csv").resolve()),
            ],
            env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
            cwd=tempfile.mkdtemp(),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        return _must("E-pipeline_replay", "pipeline.py replay", False, f"replay failed: {exc!r}")
    if completed.returncode != 0:
        return _must(
            "E-pipeline_replay",
            "pipeline.py replay matches recorded metric",
            False,
            completed.stderr[-2000:],
        )
    try:
        replayed = float(
            json.loads(completed.stdout.strip().splitlines()[-1])["metrics"][metric]["value"]
        )
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        return _must(
            "E-pipeline_replay",
            "pipeline.py replay",
            False,
            f"replay failed: {exc!r}; stderr: {completed.stderr[-2000:]}",
        )
    ok = abs(replayed - recorded) <= 0.005
    detail = f"recorded={recorded} replayed={replayed}"
    return _must("E-pipeline_replay", "pipeline.py replay matches recorded metric", ok, detail)


def expectation_checks(
    *, scenario: Scenario, outcome: RunOutcome, evidence: Evidence, scenario_dir: Path
) -> list[CheckResult]:
    expect = scenario.expect
    results: list[CheckResult] = []
    executions = evidence.node_executions

    def has(node: str, status: str) -> bool:
        return any(r.get("node_name") == node and r.get("status") == status for r in executions)

    for field_name, status in (
        ("nodes_completed", "completed"),
        ("nodes_skipped", "skipped"),
        ("nodes_failed", "failed"),
    ):
        for node in getattr(expect, field_name):
            results.append(_must(f"E-{field_name}:{node}", f"{node} {status}", has(node, status)))
    for node in expect.nodes_not_run:
        ran = any(r.get("node_name") == node for r in executions)
        results.append(_must(f"E-nodes_not_run:{node}", f"{node} not run", not ran))
    if expect.failure_contains is not None:
        found = any(
            r.get("status") == "failed" and expect.failure_contains in str(r.get("error") or "")
            for r in executions
        ) or any(
            _question(g).get("phase") == "failure"
            and expect.failure_contains in json.dumps(_question(g).get("failure"))
            for g in evidence.gates
        )
        results.append(
            _must("E-failure_contains", f"failure contains {expect.failure_contains!r}", found)
        )
    branches = _branches(evidence)
    if expect.min_candidates_completed:
        done = sum(1 for b in branches if b.get("status") == "completed")
        results.append(
            _must(
                "E-min_candidates_completed",
                "enough candidates completed",
                done >= expect.min_candidates_completed,
                f"completed={done}",
            )
        )
    if expect.candidate_count is not None:
        results.append(
            _must(
                "E-candidate_count",
                "candidate count",
                len(branches) == expect.candidate_count,
                f"branches={len(branches)}",
            )
        )
    if expect.no_gates:
        results.append(_must("E-no_gates", "no gates opened", evidence.gates == []))
    opened = {str(_question(g).get("node")) for g in evidence.gates}
    for node in expect.gates_opened_for:
        results.append(
            _must(f"E-gates_opened_for:{node}", f"gate opened for {node}", node in opened)
        )
    for node, minimum in expect.gate_revision_min.items():
        revisions = [
            int(str(_question(g).get("revision", 0)))
            for g in evidence.gates
            if _question(g).get("node") == node
        ]
        results.append(
            _must(
                f"E-gate_revision_min:{node}",
                f"{node} gate revision >= {minimum}",
                bool(revisions) and max(revisions) >= minimum,
                f"revisions={revisions}",
            )
        )
    if expect.champion is not None:
        results.append(
            _must(
                "E-champion",
                f"champion present == {expect.champion}",
                (evidence.run.get("champion_model_id") is not None) == expect.champion,
            )
        )
    if expect.test_scored is not None:
        results.append(
            _must(
                "E-test_scored",
                f"test scored == {expect.test_scored}",
                (evidence.run.get("test_scored_at") is not None) == expect.test_scored,
            )
        )
    for text in expect.report_contains + expect.metrics_in_report:
        results.append(
            _must(
                f"E-report:{text}",
                f"report contains {text!r}",
                evidence.report is not None and text in evidence.report,
            )
        )
    if expect.manifest:
        results.append(_must("E-manifest", "manifest exists", evidence.manifest is not None))
    used_tools = {str(r.get("tool_name")) for r in evidence.tool_calls}
    for tool in expect.tools_used:
        results.append(_must(f"E-tools_used:{tool}", f"tool {tool} used", tool in used_tools))
    event_types = {event.get("type") for event in evidence.server_events}
    for event_type in expect.events_include:
        results.append(
            _must(
                f"E-events_include:{event_type}", f"event {event_type}", event_type in event_types
            )
        )
    if expect.messages_answered:
        answered = sum(1 for m in outcome.messages if m.get("text"))
        results.append(
            _must(
                "E-messages_answered",
                "messages answered",
                answered >= expect.messages_answered,
                f"answered={answered}",
            )
        )
    if expect.pipeline_replay:
        results.append(_replay(evidence, scenario_dir))
    return results


def soft_checks(*, scenario: Scenario, evidence: Evidence) -> list[CheckResult]:
    soft = scenario.soft
    results: list[CheckResult] = []
    for question, expected in soft.inference.items():
        actual: str | None = None
        for line in (evidence.report or "").splitlines():
            if line.startswith(f"| {question} |"):
                cells = [c.strip() for c in line.split("|") if c.strip()]
                actual = cells[-1] if cells else None
                break
        results.append(
            _soft(
                f"W-inference:{question}",
                f"{question} -> {expected}",
                actual == expected,
                f"actual={actual}",
            )
        )
    if soft.metric_min is not None or soft.metric_max is not None:
        value = recorded_test_metric(evidence)
        ok = (
            value is not None
            and (soft.metric_min is None or value >= soft.metric_min)
            and (soft.metric_max is None or value <= soft.metric_max)
        )
        results.append(_soft("W-metric", "test metric in range", ok, f"value={value}"))
    summaries = [
        f"{r.get('summary') or ''} {r.get('error') or ''}".lower() for r in evidence.node_executions
    ]
    for text in soft.summary_mentions:
        results.append(
            _soft(
                f"W-summary:{text}",
                f"a node summary mentions {text!r}",
                any(text.lower() in s for s in summaries),
            )
        )
    return results


_SHARED_NODES = (
    "intake",
    "problem_definition",
    "data_profiling",
    "data_splitting",
    "experiment_design",
    "inferential_analysis",
    "champion_selection",
    "artifact_persistence",
)


def _db_rows(db_path: Path, sql: str, params: tuple[str, ...]) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def extra_checks(
    *,
    scenario: Scenario,
    outcome: RunOutcome,
    db_path: Path,
    project_slug: str,
    main_run_id: str,
    child_run_id: str | None,
    run2_id: str | None,
    proposal: dict[str, JsonValue] | None,
    scenario_dir: Path,
) -> list[CheckResult]:
    expect = scenario.expect
    results: list[CheckResult] = []
    if expect.fork_idempotent:
        ids = [response.get("new_run_id") for response in outcome.fork_responses]
        results.append(
            _must(
                "E-fork_idempotent",
                "repeated rewind returns the same child",
                len(ids) == 2 and ids[0] == ids[1] and isinstance(ids[0], str) and bool(ids[0]),
                f"new_run_ids={ids}",
            )
        )
    if expect.child_report_contains:
        report_path = scenario_dir / "child" / "report.md"
        text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        for needle in expect.child_report_contains:
            results.append(
                _must(
                    f"E-child_report:{needle}",
                    f"child report contains {needle!r}",
                    needle in text,
                    f"project={project_slug} child={child_run_id}",
                )
            )
    for node in expect.child_not_rerun:
        rows = (
            _db_rows(
                db_path,
                "SELECT COUNT(*) FROM node_executions WHERE run_id = ? AND node_name = ?",
                (child_run_id, node),
            )
            if child_run_id
            else [(-1,)]
        )
        results.append(
            _must(
                f"E-child_not_rerun:{node}",
                f"{node} not re-executed in child",
                rows[0][0] == 0,
                f"rows={rows[0][0]}",
            )
        )
    if expect.proposal_complete:
        contract = proposal.get("contract") if proposal else None
        reasoning = proposal.get("reasoning") if proposal else None

        def _nested(section: str, key: str) -> object:
            part = contract.get(section) if isinstance(contract, dict) else None
            return part.get(key) if isinstance(part, dict) else None

        missing = [
            name
            for name, value in (
                ("target.type", _nested("target", "type")),
                ("objective.task", _nested("objective", "task")),
                ("objective.primary_metric", _nested("objective", "primary_metric")),
                ("split.strategy", _nested("split", "strategy")),
            )
            if value is None
        ]
        blank = not isinstance(reasoning, str) or not reasoning.strip()
        results.append(
            _must(
                "E-proposal_complete",
                "proposal has the required contract fields and reasoning",
                not missing and not blank,
                f"missing={missing} blank_reasoning={blank}",
            )
        )
    if expect.memory_rows_min:
        count = _db_rows(
            db_path,
            "SELECT COUNT(*) FROM memory WHERE project_id = "
            "(SELECT project_id FROM runs WHERE id = ?)",
            (main_run_id,),
        )[0][0]
        results.append(
            _must(
                "E-memory_rows",
                f"memory rows >= {expect.memory_rows_min}",
                isinstance(count, int) and count >= expect.memory_rows_min,
                f"count={count}",
            )
        )
    if expect.second_run_memory:
        memory: object = None
        if run2_id:
            rows = _db_rows(db_path, "SELECT config_json FROM runs WHERE id = ?", (run2_id,))
            if rows and isinstance(rows[0][0], str):
                parsed = json.loads(rows[0][0])
                memory = parsed.get("memory") if isinstance(parsed, dict) else None
        results.append(
            _must(
                "E-second_run_memory",
                "second run config carries memory",
                isinstance(memory, list) and bool(memory),
                f"memory={memory!r}"[:300],
            )
        )
    if expect.shared_completed_once:
        rows = _db_rows(
            db_path,
            "SELECT node_name, candidate_id, attempt FROM node_executions "
            "WHERE run_id = ? AND status = 'completed'",
            (main_run_id,),
        )
        per_node = {node: sum(1 for r in rows if r[0] == node) for node in _SHARED_NODES}
        duplicates = len(rows) - len(set(rows))
        results.append(
            _must(
                "E-shared_completed_once",
                "shared nodes completed at most once, no duplicate completions",
                all(n <= 1 for n in per_node.values()) and duplicates == 0,
                f"per_node={per_node} duplicates={duplicates}",
            )
        )
    return results
