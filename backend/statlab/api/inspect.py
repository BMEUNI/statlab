"""Read-only endpoints: executions, artifacts, previews and run files (D32 lock at the UI)."""

from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from statlab_contracts.artifacts import ArtifactKind, parse_artifact_ref
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab.api.projects import APIResources, ProjectsService, bounded_file
from statlab.domains.candidate_pipeline import candidate_result
from statlab.domains.decisions import ReplayStep
from statlab.domains.foundation import problem_from_state, shared_result
from statlab.errors import EngineError, ToolCallError
from statlab.graph.state import RunState
from statlab.mcp_client import PREPARATION_TOOLS, PredictionUpload
from statlab.persistence.models import (
    ArtifactRow,
    LlmCallRow,
    NodeExecutionRow,
    RunRow,
    ToolCallRow,
)
from statlab.persistence.repository import (
    get_run,
    list_llm_calls,
    list_node_executions,
    list_tool_calls,
    require_project,
    run_ancestry,
)
from statlab.storage import get_artifact, list_artifacts


class RunExecutions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    executions: tuple[NodeExecutionRow, ...]
    tool_calls: tuple[ToolCallRow, ...]
    llm_calls: tuple[LlmCallRow, ...]
    cost_usd: float


class ArtifactEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: ArtifactRow
    locked: bool


RUN_FILES: dict[str, tuple[tuple[str, ...], str]] = {
    "report.md": (("report", "report.md"), "text/markdown; charset=utf-8"),
    "pipeline.py": (("pipeline.py",), "text/x-python; charset=utf-8"),
    "manifest.json": (("manifest.json",), "application/json"),
    "comparison.json": (("comparison.json",), "application/json"),
    "contract.snapshot.json": (("contract.snapshot.json",), "application/json"),
    "skill.snapshot.md": (("skill.snapshot.md",), "text/markdown; charset=utf-8"),
    "predictions.csv": (("predictions.csv",), "text/csv; charset=utf-8"),
    "uploaded_predictions.csv": (("uploaded_predictions.csv",), "text/csv; charset=utf-8"),
}
ARTIFACT_MEDIA: dict[ArtifactKind, str] = {
    "dataframe": "application/octet-stream",
    "predictions": "application/octet-stream",
    "transformer": "application/octet-stream",
    "model": "application/octet-stream",
    "inferential_fit": "application/octet-stream",
    "curve": "application/json",
    "table": "application/json",
    "plot": "image/png",
    "report": "text/markdown; charset=utf-8",
}
PLOT_NAME = re.compile(r"^a_[0-9a-f]{16}\.(?:png|chart\.json)\Z")
NO_VALIDATION = "this model has no validation predictions yet"
NO_CHAMPION = "this run has no champion model yet"
NO_SOURCE = "the rows before the split are not recorded for this run"
NO_PREPARATION = "this run's data preparation cannot be repeated on new rows"
ROW_FILTERS: Final = frozenset({"drop_duplicate_rows", "drop_high_missing_rows"})
LOAD_OPTIONS: Final = ("delimiter", "encoding", "decimal", "na_values", "header_row")
_CHUNK: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class _Champion:
    target: str
    task: Literal["classification", "regression"]
    model: str
    threshold: float | None
    replay: tuple[tuple[str, dict[str, JsonValue]], ...]
    source: str


def _locked(artifact: ArtifactRow, run: RunRow) -> bool:
    return (artifact.derived_from_test or artifact.split == "test") and run.test_scored_at is None


def _checkpoint(paths: RunPaths) -> RunState | None:
    """The run's retained checkpoint, or None before the first save."""
    try:
        text = paths.state_json.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return RunState.model_validate_json(text)
    except ValueError:
        raise EngineError("run checkpoint is malformed") from None


def _run(resource: APIResources, run_id: str) -> tuple[RunRow, RunPaths]:
    run = get_run(resource.conn, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    project = require_project(resource.conn, run.project_id)
    ProjectsService(resource).paths(project)
    return run, RunPaths(resource.settings.data_dir, project.slug, run.id)


def _artifact(resource: APIResources, artifact_id: str) -> tuple[ArtifactRow, RunPaths]:
    row = get_artifact(resource.conn, artifact_id)
    if row is None:
        raise HTTPException(404, "artifact not found")
    run, paths = _run(resource, row.run_id)
    if row.pruned_at is not None:
        raise HTTPException(410, "artifact was pruned")
    if _locked(row, run):
        raise HTTPException(403, "test-derived artifact is locked until the test score is recorded")
    return row, paths


def _producer(
    conn: sqlite3.Connection, run_id: str, ref: str
) -> tuple[str, dict[str, JsonValue]] | None:
    """The tool and arguments of the logged call that produced ref, in the run or a fork ancestor
    (chunk 77)."""
    for ancestor in run_ancestry(conn, run_id):
        for call in list_tool_calls(conn, ancestor.id):
            if ref not in call.result_json:
                continue
            result = TOOL_RESULT_ADAPTER.validate_json(call.result_json)
            if isinstance(result, ToolSuccess) and any(
                str(meta.ref) == ref for meta in result.artifacts
            ):
                arguments = json.loads(call.args_json)
                return (call.tool_name, arguments if isinstance(arguments, dict) else {})
    return None


def _split_source(conn: sqlite3.Connection, run_id: str, test: str) -> str | None:
    """The frame the shared split was made from: the frame argument of the logged split call that
    produced the test frame, in the run or a fork ancestor (chunk 77)."""
    found = _producer(conn, run_id, test)
    if found is None or not found[0].startswith("split_"):
        return None
    source = found[1].get("frame")
    return source if isinstance(source, str) else None


def _preparation(
    conn: sqlite3.Connection, run_id: str, source: str
) -> tuple[str, dict[str, JsonValue], tuple[tuple[str, dict[str, JsonValue]], ...]] | None:
    """The raw table, its load_csv options and the replayable steps from it to the split source,
    row filters left out; None when the chain cannot be replayed (chunk 77)."""
    steps: list[tuple[str, dict[str, JsonValue]]] = []
    current = source
    for _ in range(20):
        found = _producer(conn, run_id, current)
        if found is None:
            return None
        tool, arguments = found
        if tool == "load_csv":
            return (
                current,
                {key: arguments[key] for key in LOAD_OPTIONS if key in arguments},
                tuple(reversed(steps)),
            )
        previous = arguments.get("frame")
        if not isinstance(previous, str) or tool not in PREPARATION_TOOLS | ROW_FILTERS:
            return None
        if tool in PREPARATION_TOOLS:
            steps.append(
                (
                    tool,
                    {
                        key: value
                        for key, value in arguments.items()
                        if key not in ("project", "run", "frame")
                    },
                )
            )
        current = previous
    return None


def _champion(resource: APIResources, run: RunRow, paths: RunPaths) -> _Champion:
    state = _checkpoint(paths)
    if state is None:
        raise HTTPException(409, NO_CHAMPION)
    try:
        champion = shared_result(state, "champion_selection").summary
        split = shared_result(state, "data_splitting").summary
        problem = problem_from_state(state)
    except EngineError:
        raise HTTPException(409, NO_CHAMPION) from None
    model = champion.get("model")
    replay = champion.get("replay")
    test = split.get("test")
    if not isinstance(model, str) or not isinstance(replay, list) or not isinstance(test, str):
        raise HTTPException(409, NO_CHAMPION)
    try:
        steps = tuple(ReplayStep.model_validate(item) for item in replay)
    except ValidationError:
        raise EngineError("champion replay is malformed") from None
    source = _split_source(resource.conn, run.id, test)
    if source is None:
        raise HTTPException(409, NO_SOURCE)
    threshold = champion.get("threshold")
    return _Champion(
        target=problem.target,
        task=problem.task,
        model=model,
        threshold=threshold if isinstance(threshold, float) else None,
        replay=tuple((step.tool, dict(step.parameters)) for step in steps),
        source=source,
    )


async def _predict(
    resource: APIResources,
    run: RunRow,
    paths: RunPaths,
    champion: _Champion,
    upload: PredictionUpload | None,
) -> dict[str, JsonValue]:
    try:
        result = await resource.mcp.predict_unlabelled(
            project=paths.slug,
            run=run.id,
            frame=champion.source,
            target=champion.target,
            task=champion.task,
            model=champion.model,
            threshold=champion.threshold,
            replay=champion.replay,
            upload=upload,
        )
    except ToolCallError as exc:
        if exc.failure.error_class in ("tool_param", "data"):
            raise HTTPException(422, exc.failure.message) from None
        raise
    return result.summary


def inspect_router(resources: Callable[[], APIResources]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs/{run_id}/executions", response_model=RunExecutions)
    async def executions(run_id: str) -> RunExecutions:
        resource = resources()
        run, _paths = _run(resource, run_id)
        llm_calls = list_llm_calls(resource.conn, run.id)
        return RunExecutions(
            executions=list_node_executions(resource.conn, run.id),
            tool_calls=list_tool_calls(resource.conn, run.id),
            llm_calls=llm_calls,
            cost_usd=math.fsum(call.cost_usd for call in llm_calls),
        )

    @router.get("/api/runs/{run_id}/artifacts", response_model=tuple[ArtifactEntry, ...])
    async def artifacts(run_id: str) -> tuple[ArtifactEntry, ...]:
        resource = resources()
        run, paths = _run(resource, run_id)
        return tuple(
            ArtifactEntry(artifact=row, locked=_locked(row, run))
            for row in list_artifacts(
                resource.conn, run.id, include_pruned=True, checkpoint=_checkpoint(paths)
            )
        )

    @router.get("/api/artifacts/{artifact_id}/preview")
    async def preview(artifact_id: str) -> dict[str, JsonValue]:
        resource = resources()
        row, paths = _artifact(resource, artifact_id)
        if row.kind not in ("dataframe", "predictions"):
            raise HTTPException(422, "preview supports dataframe and predictions artifacts")
        result = await resource.mcp.describe_artifact(
            project=paths.slug, run=row.run_id, artifact=row.id, kind=row.kind, head_rows=20
        )
        return result.summary

    @router.get("/api/runs/{run_id}/validation")
    async def validation(
        run_id: str,
        candidate: str,
        worst_first: bool = False,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, JsonValue]:
        resource = resources()
        run, paths = _run(resource, run_id)
        state = _checkpoint(paths)
        if state is None:
            raise HTTPException(404, NO_VALIDATION)
        branch = next((item for item in state.branches if item.public_id == candidate), None)
        if branch is None:
            raise HTTPException(404, "model not found")
        try:
            split = shared_result(state, "data_splitting").summary
            evaluation = candidate_result(branch, "model_evaluation").summary
            problem = problem_from_state(state)
        except EngineError:
            raise HTTPException(404, NO_VALIDATION) from None
        source = evaluation.get(
            "probabilities" if problem.task == "classification" else "predictions"
        )
        frame = split.get("valid")
        if not isinstance(source, str) or not isinstance(frame, str):
            raise HTTPException(404, NO_VALIDATION)
        threshold = evaluation.get("threshold")
        result = await resource.mcp.validation_rows(
            project=paths.slug,
            run=run.id,
            frame=frame,
            predictions=source,
            target=problem.target,
            task=problem.task,
            threshold=threshold if isinstance(threshold, float) else None,
            worst_first=worst_first,
            offset=offset,
            limit=limit,
        )
        return result.summary

    @router.post("/api/runs/{run_id}/predictions")
    async def predictions(run_id: str) -> dict[str, JsonValue]:
        resource = resources()
        run, paths = _run(resource, run_id)
        return await _predict(resource, run, paths, _champion(resource, run, paths), None)

    @router.post("/api/runs/{run_id}/predictions/upload")
    async def predict_upload(run_id: str, request: Request) -> dict[str, JsonValue]:
        resource = resources()
        run, paths = _run(resource, run_id)
        champion = _champion(resource, run, paths)
        preparation = _preparation(resource.conn, run.id, champion.source)
        if preparation is None:
            raise HTTPException(409, NO_PREPARATION)
        raw, options, steps = preparation
        token = secrets.token_hex(16)
        stored = paths.root / f"upload_{token}.csv"
        limit = min(resource.settings.max_upload_mb, 200) * 1024 * 1024
        size = 0
        try:
            async with bounded_file(request, limit=limit) as uploaded:
                name = uploaded.filename or ""
                if Path(name).suffix.lower() != ".csv":
                    raise HTTPException(422, "CSV filename must end in .csv")
                with stored.open("wb") as handle:
                    while chunk := await uploaded.read(_CHUNK):
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, "upload exceeds limit")
                        handle.write(chunk)
            if size == 0:
                raise HTTPException(422, "uploaded file is empty")
            return await _predict(
                resource,
                run,
                paths,
                champion,
                PredictionUpload(token=token, raw=raw, options=options, steps=steps),
            )
        finally:
            stored.unlink(missing_ok=True)

    @router.get("/api/artifacts/{artifact_id}")
    async def download(artifact_id: str) -> FileResponse:
        resource = resources()
        row, paths = _artifact(resource, artifact_id)
        path = paths.artifact(parse_artifact_ref(row.id), row.kind)
        if not path.is_file():
            raise HTTPException(404, "artifact file is missing")
        return FileResponse(path, media_type=ARTIFACT_MEDIA[row.kind])

    @router.get("/api/runs/{run_id}/files/plots/{plot}")
    async def run_plot(run_id: str, plot: str) -> FileResponse:
        resource = resources()
        _run_row, paths = _run(resource, run_id)
        if PLOT_NAME.match(plot) is None:
            raise HTTPException(404, "run file not found")
        path = paths.report / "plots" / plot
        if not path.is_file():
            raise HTTPException(404, "run file not found")
        media = "application/json" if plot.endswith(".chart.json") else "image/png"
        return FileResponse(path, media_type=media)

    @router.get("/api/runs/{run_id}/files/{name}")
    async def run_file(run_id: str, name: str) -> FileResponse:
        resource = resources()
        _run_row, paths = _run(resource, run_id)
        entry = RUN_FILES.get(name)
        if entry is None:
            raise HTTPException(404, "run file not found")
        parts, media = entry
        path = paths.root.joinpath(*parts)
        if not path.is_file():
            raise HTTPException(404, "run file not found")
        return FileResponse(path, media_type=media)

    return router
