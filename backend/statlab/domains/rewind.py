"""Tabular fork publication and retained champion provenance."""

from __future__ import annotations

import hashlib
import sqlite3

from statlab_contracts.layout import RunPaths

from statlab.domains.foundation import _object, _ref, shared_result
from statlab.errors import PersistenceError
from statlab.graph.state import RunState
from statlab.persistence.models import ModelRow, RunRow
from statlab.persistence.repository import get_champion_model, get_run
from statlab.storage import get_artifact, validate_checkpoint_ownership


def champion_provenance(conn: sqlite3.Connection, state: RunState) -> tuple[RunRow, ModelRow]:
    """The retained committed selection owns the score claim, including an ancestor selection."""
    boundary = next(
        (item for item in reversed(state.shared_results) if item.node_name == "champion_selection"),
        None,
    )
    if boundary is None:
        raise PersistenceError("retained champion boundary is unavailable")
    execution = conn.execute(
        "SELECT run_id FROM node_executions WHERE id = ?", (boundary.execution_id,)
    ).fetchone()
    if execution is None:
        raise PersistenceError("champion execution is unavailable")
    if execution["run_id"] != state.run_id:
        validate_checkpoint_ownership(conn, state.run_id, state)
    run = get_run(conn, execution["run_id"])
    model = get_champion_model(conn, execution["run_id"])
    score = _ref(boundary.result.summary, "test_score")
    row = get_artifact(conn, str(score))
    if (
        run is None
        or model is None
        or run.test_scored_at is None
        or row is None
        or row.run_id != run.id
        or not row.derived_from_test
        or model.id != boundary.result.summary.get("model_id")
        or model.artifact_id != boundary.result.summary.get("model")
    ):
        raise PersistenceError("retained champion score provenance is invalid")
    return run, model


async def publish_retained_report(parent: RunPaths, child: RunPaths, state: RunState) -> None:
    if not any(item.node_name == "champion_selection" for item in state.shared_results):
        return
    champion = shared_result(state, "champion_selection").summary
    report = _object(champion.get("report"), "champion report")
    source = parent.report / "report.md"
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != report.get("sha256"):
        raise PersistenceError("retained report hash disagrees with committed champion")
    (child.report / "report.md").write_bytes(content)
    # Retained plots come from immutable artifact refs; independent publication copies
    # protect the original's report paths from subsequent child report generation.
    for ref in state.retained_refs:
        artifact = parent.artifacts / f"{ref}.png"
        if artifact.is_file():
            plots = child.report / "plots"
            plots.mkdir(exist_ok=True)
            (plots / artifact.name).write_bytes(artifact.read_bytes())
            chart = parent.artifacts / "charts" / f"{ref}.json"
            if chart.is_file():
                (plots / f"{ref}.chart.json").write_bytes(chart.read_bytes())
