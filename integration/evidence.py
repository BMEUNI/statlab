"""Read-only evidence collection for one scenario run."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue

Row = dict[str, object]

_QUERIES = {
    "node_executions": "SELECT * FROM node_executions WHERE run_id = ? ORDER BY rowid",
    "llm_calls": "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY rowid",
    "tool_calls": "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY rowid",
    "gates": "SELECT * FROM gates WHERE run_id = ? ORDER BY rowid",
    "models": "SELECT * FROM models WHERE run_id = ? ORDER BY rowid",
    "artifacts": "SELECT * FROM artifacts WHERE run_id = ? ORDER BY rowid",
}


@dataclass
class Evidence:
    run: Row = field(default_factory=dict)
    node_executions: list[Row] = field(default_factory=list)
    llm_calls: list[Row] = field(default_factory=list)
    tool_calls: list[Row] = field(default_factory=list)
    gates: list[Row] = field(default_factory=list)
    models: list[Row] = field(default_factory=list)
    artifacts: list[Row] = field(default_factory=list)
    server_events: list[dict[str, JsonValue]] = field(default_factory=list)
    state: dict[str, JsonValue] | None = None
    report: str | None = None
    pipeline: str | None = None
    pipeline_path: Path | None = None
    manifest: str | None = None
    contract_snapshot: str | None = None


def _rows(conn: sqlite3.Connection, table: str, run_id: str) -> list[Row]:
    return [dict(row) for row in conn.execute(_QUERIES[table], (run_id,)).fetchall()]


def _copy_text(source: Path, scenario_dir: Path) -> str | None:
    if not source.is_file():
        return None
    shutil.copyfile(source, scenario_dir / source.name)
    return source.read_text(encoding="utf-8")


def collect(
    *, db_path: Path, data_dir: Path, slug: str, run_id: str, scenario_dir: Path
) -> Evidence:
    evidence = Evidence()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run_rows = [
            dict(row)
            for row in conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchall()
        ]
        evidence.run = run_rows[0] if run_rows else {}
        (scenario_dir / "runs.json").write_text(
            json.dumps(run_rows, indent=2, default=str), encoding="utf-8"
        )
        for table in _QUERIES:
            rows = _rows(conn, table, run_id)
            setattr(evidence, table, rows)
            (scenario_dir / f"{table}.json").write_text(
                json.dumps(rows, indent=2, default=str), encoding="utf-8"
            )
    finally:
        conn.close()

    run_dir = data_dir / slug / "runs" / run_id
    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    evidence.server_events.append({str(k): v for k, v in parsed.items()})
    state_path = run_dir / "state.json"
    if state_path.is_file():
        parsed_state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(parsed_state, dict):
            evidence.state = {str(k): v for k, v in parsed_state.items()}
    evidence.report = _copy_text(run_dir / "report" / "report.md", scenario_dir)
    evidence.pipeline = _copy_text(run_dir / "pipeline.py", scenario_dir)
    if evidence.pipeline is not None:
        evidence.pipeline_path = scenario_dir / "pipeline.py"
    evidence.manifest = _copy_text(run_dir / "manifest.json", scenario_dir)
    evidence.contract_snapshot = _copy_text(run_dir / "contract.snapshot.json", scenario_dir)
    return evidence
