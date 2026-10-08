"""Read-only inspection routes: executions, artifacts, previews and run files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import JsonValue
from statlab.config import Settings
from statlab.persistence.db import connect
from statlab.persistence.repository import (
    claim_test_scoring,
    create_contract,
    create_node_execution,
    create_run,
    record_llm_call,
    record_tool_call,
)
from statlab.storage import create_artifact, ensure_run_layout, mark_pruned
from statlab_contracts.artifacts import (
    ArtifactKind,
    ArtifactMeta,
    new_artifact_ref,
    parse_artifact_ref,
)
from statlab_contracts.contract import DataContract, TargetSpec
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import SplitRole

from tests.test_api_projects import _open_project_api


@dataclass(frozen=True)
class _Seed:
    run_id: str
    execution_id: str
    plot: str
    frame: str
    test_frame: str
    table: str
    pruned: str
    report_plot: str


def _seed(settings: Settings, project_id: str) -> _Seed:
    conn = connect(settings.db_path)
    try:
        contract = create_contract(
            conn,
            project_id=project_id,
            path=settings.data_dir / "seed.json",
            contract=DataContract(target=TargetSpec(column="a")),
        )
        run = create_run(
            conn,
            project_id=project_id,
            contract_id=contract.id,
            mode="autonomous",
            config_json="{}",
        )
        execution = create_node_execution(conn, run_id=run.id, node_name="intake", seq=0)
        record_tool_call(
            conn,
            run_id=run.id,
            node_execution_id=execution.id,
            tool_name="load_csv",
            args_json='{"x":1}',
            result_json='{"status":"ok"}',
            duration_ms=5,
        )
        for cost in (0.25, 0.5):
            record_llm_call(
                conn,
                run_id=run.id,
                node_execution_id=execution.id,
                signature="s",
                model="m",
                prompt_tokens=1,
                completion_tokens=1,
                cached_tokens=0,
                cost_usd=cost,
                duration_ms=10,
            )
        paths = RunPaths(settings.data_dir, "demo", run.id)
        ensure_run_layout(paths)

        def artifact(kind: ArtifactKind, data: bytes, split: SplitRole | None = None) -> str:
            ref = new_artifact_ref()
            path = paths.artifact(ref, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            create_artifact(
                conn,
                ref=ref,
                run_id=run.id,
                kind=kind,
                path=path,
                meta=ArtifactMeta(ref=ref, kind=kind, split=split),
                produced_by="seed",
                split=split,
            )
            return str(ref)

        plot = artifact("plot", b"\x89PNG")
        frame = artifact("dataframe", b"PAR1")
        test_frame = artifact("dataframe", b"PAR1", "test")
        table = artifact("table", b'{"k": 1}')
        pruned = artifact("plot", b"\x89PNG")
        mark_pruned(conn, (parse_artifact_ref(pruned),))
        paths.pipeline_py.write_text("print(1)\n")
        report_plot = f"{new_artifact_ref()}.png"
        (paths.report / "plots").mkdir(parents=True, exist_ok=True)
        (paths.report / "plots" / report_plot).write_bytes(b"png")
        (paths.report / "report.md").write_text(f"# Report\n![p](plots/{report_plot})\n")
        return _Seed(run.id, execution.id, plot, frame, test_frame, table, pruned, report_plot)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_project_dataset_and_contract_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, project):
        upload = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        listed = await client.get(f"/api/projects/{project['id']}/datasets")
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [upload.json()["id"]]
        seed_conn = connect(settings.db_path)
        try:
            contract = create_contract(
                seed_conn,
                project_id=str(project["id"]),
                path=settings.data_dir / "seed.json",
                contract=DataContract(target=TargetSpec(column="a")),
            )
        finally:
            seed_conn.close()
        contracts = await client.get(f"/api/projects/{project['id']}/contracts")
        assert contracts.status_code == 200
        assert [row["id"] for row in contracts.json()] == [contract.id]
        assert (await client.get("/api/projects/missing/datasets")).status_code == 404
        assert (await client.get("/api/projects/missing/contracts")).status_code == 404


@pytest.mark.asyncio
async def test_run_executions_and_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, project):
        seed = _seed(settings, str(project["id"]))
        response = await client.get(f"/api/runs/{seed.run_id}/executions")
        assert response.status_code == 200
        body = response.json()
        assert [row["id"] for row in body["executions"]] == [seed.execution_id]
        assert body["tool_calls"][0]["tool_name"] == "load_csv"
        assert len(body["llm_calls"]) == 2
        assert body["cost_usd"] == 0.75
        assert (await client.get("/api/runs/missing/executions")).status_code == 404


@pytest.mark.asyncio
async def test_artifact_list_lock_download_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, project):
        seed = _seed(settings, str(project["id"]))
        listed = await client.get(f"/api/runs/{seed.run_id}/artifacts")
        assert listed.status_code == 200
        entries = {row["artifact"]["id"]: row["locked"] for row in listed.json()}
        assert set(entries) == {seed.plot, seed.frame, seed.test_frame, seed.table, seed.pruned}
        assert [key for key, value in entries.items() if value] == [seed.test_frame]
        plot = await client.get(f"/api/artifacts/{seed.plot}")
        assert plot.status_code == 200
        assert plot.content == b"\x89PNG"
        assert plot.headers["content-type"] == "image/png"
        table = await client.get(f"/api/artifacts/{seed.table}")
        assert table.headers["content-type"].startswith("application/json")
        frame = await client.get(f"/api/artifacts/{seed.frame}")
        assert frame.headers["content-type"] == "application/octet-stream"
        assert (await client.get(f"/api/artifacts/{seed.test_frame}")).status_code == 403
        assert (await client.get(f"/api/artifacts/{seed.pruned}")).status_code == 410
        assert (await client.get("/api/artifacts/a_0000000000000000")).status_code == 404
        (
            RunPaths(settings.data_dir, "demo", seed.run_id).artifacts / f"{seed.frame}.parquet"
        ).unlink()
        assert (await client.get(f"/api/artifacts/{seed.frame}")).status_code == 404
        conn = connect(settings.db_path)
        try:
            claim_test_scoring(conn, seed.run_id)
        finally:
            conn.close()
        assert (await client.get(f"/api/artifacts/{seed.test_frame}")).status_code == 200
        relisted = await client.get(f"/api/runs/{seed.run_id}/artifacts")
        assert not any(row["locked"] for row in relisted.json())


@pytest.mark.asyncio
async def test_artifact_preview_uses_describe_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, session, project):
        seed = _seed(settings, str(project["id"]))
        summary: dict[str, JsonValue] = {
            "rows": 2,
            "cols": 1,
            "dtypes": {"a": "int64"},
            "head": [{"a": 1}],
        }
        session.responses.append(ToolSuccess(summary=summary))
        response = await client.get(f"/api/artifacts/{seed.frame}/preview")
        assert response.status_code == 200
        assert response.json() == summary
        name, arguments, timeout = session.calls[-1]
        assert name == "describe_artifact"
        assert arguments == {
            "project": "demo",
            "run": seed.run_id,
            "artifact": seed.frame,
            "kind": "dataframe",
            "head_rows": 20,
        }
        assert timeout == 60
        count = len(session.calls)
        assert (await client.get(f"/api/artifacts/{seed.plot}/preview")).status_code == 422
        assert len(session.calls) == count
        assert (await client.get(f"/api/artifacts/{seed.test_frame}/preview")).status_code == 403


@pytest.mark.asyncio
async def test_run_files_and_report_plots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, project):
        seed = _seed(settings, str(project["id"]))
        base = f"/api/runs/{seed.run_id}/files"
        pipeline = await client.get(f"{base}/pipeline.py")
        assert pipeline.status_code == 200
        assert pipeline.text == "print(1)\n"
        assert pipeline.headers["content-type"].startswith("text/x-python")
        report = await client.get(f"{base}/report.md")
        assert report.status_code == 200
        assert f"plots/{seed.report_plot}" in report.text
        plot = await client.get(f"{base}/plots/{seed.report_plot}")
        assert plot.status_code == 200
        assert plot.content == b"png"
        assert (await client.get(f"{base}/plots/x.png")).status_code == 404
        assert (await client.get(f"{base}/plots/{new_artifact_ref()}.png")).status_code == 404
        assert (await client.get(f"{base}/manifest.json")).status_code == 404
        assert (await client.get(f"{base}/secret.txt")).status_code == 404
        assert (await client.get("/api/runs/missing/files/pipeline.py")).status_code == 404
