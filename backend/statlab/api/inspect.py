"""Read-only endpoints: executions, artifacts, previews and run files (D32 lock at the UI)."""

from __future__ import annotations

import math
import re
from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, JsonValue
from statlab_contracts.artifacts import ArtifactKind, parse_artifact_ref
from statlab_contracts.layout import RunPaths

from statlab.api.projects import APIResources, ProjectsService
from statlab.errors import EngineError
from statlab.graph.state import RunState
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
PLOT_NAME = re.compile(r"^a_[0-9a-f]{16}\.png\Z")


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
        return FileResponse(path, media_type="image/png")

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
