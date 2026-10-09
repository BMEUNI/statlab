"""CLI entry point: run integration scenarios against the real backend."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from statlab.llm.client import LlmClient
from statlab.persistence.models import RunStatus

from integration.checks import (
    CheckResult,
    expectation_checks,
    extra_checks,
    global_checks,
    soft_checks,
)
from integration.driver import Budget, Driver, HarnessError, RunOutcome, Workspace
from integration.evidence import Evidence, collect
from integration.model import Scenario, load_scenarios
from integration.offline_llm import OfflineProvider
from integration.services import ApiServer, McpProcess, build_settings, free_port


@dataclass
class ScenarioRecord:
    scenario: Scenario
    result: str = "SKIPPED"
    checks: list[CheckResult] = field(default_factory=list)
    cost: float = 0.0
    duration: float = 0.0
    folder: Path | None = None
    budget_stopped: bool = False


def _sql_scalar(db_path: Path, sql: str, params: tuple[str, ...] = ()) -> float:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return float(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="integration.run_scenarios")
    parser.add_argument("--llm", choices=("live", "offline", "offline-rich"), required=True)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=2.0)
    parser.add_argument("--scenarios", type=Path, default=Path("integration/scenarios.toml"))
    return parser.parse_args(argv)


def _setup_logging(api_log: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.FileHandler(api_log)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)


async def _evaluate(
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


async def _run_one(
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
            checks, _ = await _evaluate(
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
                checks, _ = await _evaluate(
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
                checks, _ = await _evaluate(
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
            record.cost = _sql_scalar(
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


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH.
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip()


def _write_summary(
    workspace: Workspace,
    records: list[ScenarioRecord],
    *,
    mode: str,
    started: str,
    ended: str,
) -> Path:
    total_cost = _sql_scalar(workspace.db_path, "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls")
    calls = int(_sql_scalar(workspace.db_path, "SELECT COUNT(*) FROM llm_calls"))
    lines = [
        "# Integration summary",
        "",
        f"- Mode: {mode}",
        f"- Commit: {_git_head()}",
        f"- Started: {started}",
        f"- Ended: {ended}",
        f"- Total cost USD: {total_cost:.4f} ({calls} LLM calls)",
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


async def _suite(scenarios: tuple[Scenario, ...], args: argparse.Namespace) -> int:
    started = datetime.now(UTC)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    root = Path("integration/out") / f"{stamp}-{args.llm}"
    workspace = Workspace(
        root=root,
        data_dir=root / "data",
        db_path=root / "statlab.db",
        api_log=root / "api.log",
        mcp_log=root / "mcp.log",
    )
    workspace.data_dir.mkdir(parents=True)
    workspace.api_log.touch()
    _setup_logging(workspace.api_log)
    if args.verbose:
        logging.getLogger("statlab").setLevel(logging.DEBUG)
    token = secrets.token_urlsafe(32)
    mcp = McpProcess(data_dir=workspace.data_dir, token=token, log_path=workspace.mcp_log)
    api: ApiServer | None = None
    driver: Driver | None = None
    records: list[ScenarioRecord] = []
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
        provider = OfflineProvider(rich=args.llm == "offline-rich") if args.llm != "live" else None
        llm_client = LlmClient(settings, acompletion=provider) if provider is not None else None
        server = ApiServer(settings=settings, llm_client=llm_client)
        api = server
        base_url = await server.start()

        async def restart() -> None:
            await server.stop()
            await server.start()

        driver = Driver(
            base_url=base_url, workspace=workspace, restart=restart, cheap=args.llm == "live"
        )
        budget = Budget(workspace.db_path, args.budget_usd)
        stop = False
        for scenario in scenarios:
            if stop:
                records.append(ScenarioRecord(scenario))
                continue
            if provider is not None:
                provider.scenario = scenario
            record = await _run_one(
                scenario,
                driver=driver,
                workspace=workspace,
                mcp=mcp,
                budget=budget,
                live=args.llm == "live",
            )
            records.append(record)
            stop = record.budget_stopped
    finally:
        if driver is not None:
            await driver.close()
        if api is not None:
            await api.stop()
        mcp.stop()
    ended = datetime.now(UTC)
    summary = _write_summary(
        workspace,
        records,
        mode=args.llm,
        started=started.isoformat(),
        ended=ended.isoformat(),
    )
    print(summary)
    return 0 if all(r.result in ("PASS", "SKIPPED") for r in records) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    lock = Path("uv.lock")
    if not lock.is_file() or lock.stat().st_size == 0:
        print("run from the repository root", file=sys.stderr)
        return 2
    scenarios = load_scenarios(args.scenarios)
    if args.only:
        known = {s.id for s in scenarios}
        unknown = [i for i in args.only if i not in known]
        if unknown:
            print(f"unknown scenario ids: {unknown}", file=sys.stderr)
            return 2
        scenarios = tuple(s for s in scenarios if s.id in args.only)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        return asyncio.run(_suite(scenarios, args))
    except KeyboardInterrupt:
        print("interrupted; services stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
