"""CLI entry point: run integration scenarios against the real backend."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from integration.model import Scenario, load_scenarios
from integration.offline_llm import OfflineProvider
from integration.session import (
    ScenarioRecord,
    make_workspace,
    open_services,
    run_one,
    setup_logging,
    write_summary,
)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="integration.run_scenarios")
    parser.add_argument("--llm", choices=("live", "offline", "offline-rich"), required=True)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=2.0)
    parser.add_argument("--scenarios", type=Path, default=Path("integration/scenarios.toml"))
    return parser.parse_args(argv)


async def _suite(scenarios: tuple[Scenario, ...], args: argparse.Namespace) -> int:
    started = datetime.now(UTC)
    workspace = make_workspace(args.llm, started)
    setup_logging(workspace.api_log)
    if args.verbose:
        logging.getLogger("statlab").setLevel(logging.DEBUG)
    records: list[ScenarioRecord] = []
    async with open_services(
        workspace,
        mode=args.llm,
        budget_usd=args.budget_usd,
        cassettes=Path("integration/.cache/cassettes"),
    ) as services:
        stop = False
        for scenario in scenarios:
            if stop:
                records.append(ScenarioRecord(scenario))
                continue
            if isinstance(services.provider, OfflineProvider):
                services.provider.scenario = scenario
            record = await run_one(
                scenario,
                driver=services.driver,
                workspace=services.workspace,
                mcp=services.mcp,
                budget=services.budget,
                live=services.live,
            )
            records.append(record)
            stop = record.budget_stopped
    ended = datetime.now(UTC)
    summary = write_summary(
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
