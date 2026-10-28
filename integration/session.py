"""Suite services and one-scenario execution, shared by pytest and the old CLI (chunk 89a)."""

from __future__ import annotations

import dataclasses
import json
import logging
import secrets
import sqlite3
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import JsonValue
from statlab.llm.client import LlmClient
from statlab.persistence.models import RunStatus

from integration.cassette import CassetteProvider
from integration.checks import (
    CheckResult,
    expectation_checks,
    extra_checks,
    global_checks,
    soft_checks,
)
from integration.driver import Budget, Driver, HarnessError, RunOutcome, Workspace
from integration.evidence import Evidence, collect
from integration.model import Scenario
from integration.offline_llm import OfflineProvider
from integration.services import ApiServer, McpProcess, build_settings, free_port

if TYPE_CHECKING:
    import pytest


@dataclass
class ScenarioRecord:
    scenario: Scenario
    result: str = "SKIPPED"
    checks: list[CheckResult] = field(default_factory=list)
    cost: float = 0.0
    duration: float = 0.0
    folder: Path | None = None
    budget_stopped: bool = False
    skip_reason: str = ""


def sql_scalar(db_path: Path, sql: str, params: tuple[str, ...] = ()) -> float:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return float(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def setup_logging(api_log: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.FileHandler(api_log)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)


async def evaluate(
    label: str,
    scenario: Scenario,
    run_id: str,
    outcome: RunOutcome,
    run_dir: Path,
    *,
    expect_status: RunStatus | None,
    full: bool,
    workspace: Workspace,
    mcp: McpProcess,
    live: bool,
    log_before: int,
) -> tuple[list[CheckResult], Evidence]:
    evidence = collect(
        db_path=workspace.db_path,
        data_dir=workspace.data_dir,
        slug=scenario.id.lower(),
        run_id=run_id,
        scenario_dir=run_dir,
    )
    with workspace.api_log.open("rb") as handle:
        handle.seek(log_before)
        log_slice = handle.read().decode("utf-8", errors="replace")
    adjusted = scenario.model_copy(
        update={"expect": scenario.expect.model_copy(update={"status": expect_status})}
    )
    checks = global_checks(
        scenario=adjusted,
        outcome=outcome,
        evidence=evidence,
        api_log_slice=log_slice,
        mcp_alive=mcp.alive(),
        live=live,
    )
    if full:
        checks.extend(
            expectation_checks(
                scenario=scenario, outcome=outcome, evidence=evidence, scenario_dir=run_dir
            )
        )
        checks.extend(soft_checks(scenario=scenario, evidence=evidence))
    if label != "main":
        checks = [dataclasses.replace(c, id=f"{label}-{c.id}") for c in checks]
    return checks, evidence


async def run_one(
    scenario: Scenario,
    *,
    driver: Driver,
    workspace: Workspace,
    mcp: McpProcess,
    budget: Budget,
    live: bool,
) -> ScenarioRecord:
    print(f"== {scenario.id} {scenario.title}", flush=True)
    record = ScenarioRecord(scenario)
    scenario_dir = workspace.scenario_dir(scenario.id)
    record.folder = scenario_dir
    log_before = workspace.api_log.stat().st_size
    started = time.monotonic()
    outcome: RunOutcome | None = None
    run2_id: str | None = None
    http_start = len(driver.http_log)
    try:
        if scenario.setup.invalid_cases:
            record.checks.extend(await driver.invalid_setup(scenario, scenario_dir))
        else:
            prepared = await driver.setup(scenario, scenario_dir)
            outcome = await driver.run(scenario, prepared.run_id, scenario_dir, budget=budget)
            record.budget_stopped = outcome.budget_stopped
            checks, _ = await evaluate(
                "main",
                scenario,
                prepared.run_id,
                outcome,
                scenario_dir,
                expect_status=scenario.expect.status,
                full=True,
                workspace=workspace,
                mcp=mcp,
                live=live,
                log_before=log_before,
            )
            record.checks.extend(checks)
            if scenario.setup.runs == 2:
                run2_id = await driver.create_run(
                    prepared.project_id, prepared.dataset_id, prepared.contract_id, scenario.mode
                )
                run2_dir = scenario_dir / "run2"
                run2_dir.mkdir(exist_ok=True)
                outcome2 = await driver.run(
                    scenario.model_copy(update={"actions": (), "gates": ()}),
                    run2_id,
                    run2_dir,
                    budget=budget,
                )
                checks, _ = await evaluate(
                    "run2",
                    scenario,
                    run2_id,
                    outcome2,
                    run2_dir,
                    expect_status=scenario.expect.status,
                    full=False,
                    workspace=workspace,
                    mcp=mcp,
                    live=live,
                    log_before=log_before,
                )
                record.checks.extend(checks)
            if outcome.child_run_id:
                child_dir = scenario_dir / "child"
                child_dir.mkdir(exist_ok=True)
                outcome_child = await driver.run(
                    scenario.model_copy(update={"actions": (), "gates": ()}),
                    outcome.child_run_id,
                    child_dir,
                    budget=budget,
                )
                checks, _ = await evaluate(
                    "child",
                    scenario,
                    outcome.child_run_id,
                    outcome_child,
                    child_dir,
                    expect_status=scenario.expect.child_status,
                    full=False,
                    workspace=workspace,
                    mcp=mcp,
                    live=live,
                    log_before=log_before,
                )
                record.checks.extend(checks)
            record.checks.extend(
                extra_checks(
                    scenario=scenario,
                    outcome=outcome,
                    db_path=workspace.db_path,
                    project_slug=scenario.id.lower(),
                    main_run_id=prepared.run_id,
                    child_run_id=outcome.child_run_id,
                    run2_id=run2_id,
                    proposal=prepared.proposal,
                    scenario_dir=scenario_dir,
                )
            )
            record.cost = sql_scalar(
                workspace.db_path,
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = ?",
                (prepared.run_id,),
            )
    except (HarnessError, httpx.HTTPError, TimeoutError) as exc:
        record.checks.append(CheckResult("H-error", "harness error", "must", False, str(exc)))
    record.duration = time.monotonic() - started
    failed_must = [c for c in record.checks if c.severity == "must" and not c.passed]
    record.result = "FAIL" if failed_must else "PASS"
    payload: dict[str, object] = {
        "id": scenario.id,
        "title": scenario.title,
        "run_id": outcome.run_id if outcome else None,
        "child_run_id": outcome.child_run_id if outcome else None,
        "run2_id": run2_id,
        "fork_responses": outcome.fork_responses if outcome else [],
        "final_status": outcome.final_status if outcome else None,
        "duration_s": round(record.duration, 2),
        "cost_usd": record.cost,
        "checks": [dataclasses.asdict(c) for c in record.checks],
        "outcome": (
            {
                "gate_answers": outcome.gate_answers,
                "actions": outcome.actions,
                "messages": outcome.messages,
                "timed_out": outcome.timed_out,
                "budget_stopped": outcome.budget_stopped,
                "started_at": outcome.started_at,
                "ended_at": outcome.ended_at,
            }
            if outcome
            else None
        ),
        "http_log": driver.http_log[http_start:],
    }
    (scenario_dir / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"== {scenario.id} {record.result}", flush=True)
    return record


def git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH.
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


def write_summary(
    workspace: Workspace,
    records: list[ScenarioRecord],
    *,
    mode: str,
    started: str,
    ended: str,
) -> Path:
    total_cost = sql_scalar(workspace.db_path, "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls")
    calls = int(sql_scalar(workspace.db_path, "SELECT COUNT(*) FROM llm_calls"))
    lines = [
        "# Integration summary",
        "",
        f"- Mode: {mode}",
        f"- Commit: {git_head()}",
        f"- Started: {started}",
        f"- Ended: {ended}",
        (
            f"- Recorded cost USD: {total_cost:.4f} ({calls} replayed LLM calls, no spend)"
            if mode == "replay"
            else f"- Total cost USD: {total_cost:.4f} ({calls} LLM calls)"
        ),
        "",
        "| Scenario | Title | Result | Must | Soft warnings | Cost USD | Duration s |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        must = [c for c in record.checks if c.severity == "must"]
        soft_failed = [c for c in record.checks if c.severity == "soft" and not c.passed]
        passed_must = sum(1 for c in must if c.passed)
        lines.append(
            f"| {record.scenario.id} | {record.scenario.title} | {record.result} | "
            f"{passed_must}/{len(must)} | {len(soft_failed)} | {record.cost:.4f} | "
            f"{record.duration:.1f} |"
        )
    for record in records:
        failed = [c for c in record.checks if not c.passed]
        if record.result != "FAIL" and not failed:
            continue
        lines.extend(["", f"## {record.scenario.id} - {record.scenario.title}", ""])
        lines.append(f"Folder: {record.folder}")
        lines.extend(f"- [{c.severity}] {c.id}: {c.description} -- {c.detail}" for c in failed)
    path = workspace.root / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_workspace(mode: str, started: datetime) -> Workspace:
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    root = Path("integration/out") / f"{stamp}-{mode}"
    workspace = Workspace(
        root=root,
        data_dir=root / "data",
        db_path=root / "statlab.db",
        api_log=root / "api.log",
        mcp_log=root / "mcp.log",
    )
    workspace.data_dir.mkdir(parents=True)
    workspace.api_log.touch()
    return workspace


@dataclass(frozen=True)
class SuiteServices:
    workspace: Workspace
    mcp: McpProcess
    driver: Driver
    budget: Budget
    provider: OfflineProvider | CassetteProvider | None
    live: bool


@asynccontextmanager
async def open_services(
    workspace: Workspace, *, mode: str, budget_usd: float, cassettes: Path
) -> AsyncIterator[SuiteServices]:
    token = secrets.token_urlsafe(32)
    mcp = McpProcess(data_dir=workspace.data_dir, token=token, log_path=workspace.mcp_log)
    api: ApiServer | None = None
    driver: Driver | None = None
    try:
        mcp_url = mcp.start()
        api_port = free_port()
        settings = build_settings(
            token=token,
            mcp_url=mcp_url,
            data_dir=workspace.data_dir.resolve(),
            db_path=workspace.db_path.resolve(),
            api_port=api_port,
        )
        provider: OfflineProvider | CassetteProvider | None
        if mode == "record":
            provider = CassetteProvider(mode="record", root=cassettes)
        elif mode == "replay":
            provider = CassetteProvider(mode="replay", root=cassettes)
        elif mode == "live":
            provider = None
        else:
            provider = OfflineProvider(rich=mode == "offline-rich")
        llm_client = LlmClient(settings, acompletion=provider) if provider is not None else None
        server = ApiServer(settings=settings, llm_client=llm_client)
        api = server
        base_url = await server.start()

        async def restart() -> None:
            await server.stop()
            await server.start()

        driver = Driver(
            base_url=base_url,
            workspace=workspace,
            restart=restart,
            cheap=mode in ("live", "record", "replay"),
        )
        await driver.sign_in()
        budget = Budget(workspace.db_path, budget_usd)
        yield SuiteServices(
            workspace=workspace,
            mcp=mcp,
            driver=driver,
            budget=budget,
            provider=provider,
            live=mode in ("live", "record"),
        )
    finally:
        if driver is not None:
            await driver.close()
        if api is not None:
            await api.stop()
        mcp.stop()


@dataclass
class Suite:
    services: SuiteServices
    started: datetime
    records: dict[str, ScenarioRecord] = field(default_factory=dict)
    stopped: bool = False


def judged_mode(config: pytest.Config) -> bool:
    return config.getoption("llm") in ("record", "replay", "live")


def _load_rows(path: Path) -> list[object]:
    if not path.is_file():
        return []
    rows: object = json.loads(path.read_text(encoding="utf-8"))
    return rows if isinstance(rows, list) else []


def champion_summary(record: ScenarioRecord) -> dict[str, JsonValue] | None:
    if record.folder is None:
        return None
    found: dict[str, JsonValue] | None = None
    for row in _load_rows(record.folder / "node_executions.json"):
        if not isinstance(row, dict):
            continue
        if row.get("node_name") != "champion_selection" or row.get("status") != "completed":
            continue
        summary = row.get("summary")
        if isinstance(summary, str):
            summary = json.loads(summary)
        if isinstance(summary, dict):
            found = summary
    return found


def tools_called(record: ScenarioRecord) -> list[str]:
    if record.folder is None:
        return []
    names: list[str] = []
    for row in _load_rows(record.folder / "tool_calls.json"):
        name = row.get("tool_name") if isinstance(row, dict) else None
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def final_status(record: ScenarioRecord) -> str:
    if record.folder is None or not (record.folder / "result.json").is_file():
        return "unknown"
    payload = json.loads((record.folder / "result.json").read_text(encoding="utf-8"))
    status = payload.get("final_status") if isinstance(payload, dict) else None
    return status if isinstance(status, str) else "unknown"
