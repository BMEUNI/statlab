"""Offline backend for UI click-throughs: MCP + API with the stand-in LLM; no paid calls."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import signal
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from statlab.llm.client import LlmClient

from integration.datasets import write_csv
from integration.model import Scenario, load_scenarios
from integration.offline_llm import OfflineProvider
from integration.services import ApiServer, McpProcess, build_settings
from integration.session import setup_logging


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", choices=("offline", "offline-rich"), default="offline")
    parser.add_argument("--scenario", default="S01")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--scenarios", type=Path, default=Path("integration/scenarios.toml"))
    return parser.parse_args(argv)


async def _serve(args: argparse.Namespace, scenario: Scenario) -> None:
    root = Path("integration/out") / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-ui")
    (root / "data").mkdir(parents=True)
    (root / "samples").mkdir(parents=True)
    for key in ("churn", "churn_dirty", "houses", "species"):
        write_csv(key, root / "samples" / f"{key}.csv")
    setup_logging(root / "api.log")
    token = secrets.token_urlsafe(32)
    mcp = McpProcess(data_dir=(root / "data").resolve(), token=token, log_path=root / "mcp.log")
    server: ApiServer | None = None
    try:
        settings = build_settings(
            token=token,
            mcp_url=mcp.start(),
            data_dir=(root / "data").resolve(),
            db_path=(root / "statlab.db").resolve(),
            api_port=args.port,
        )
        provider = OfflineProvider(rich=args.llm == "offline-rich")
        provider.scenario = scenario
        server = ApiServer(settings=settings, llm_client=LlmClient(settings, acompletion=provider))
        base_url = await server.start()
        print(f"API ready at {base_url} (offline LLM, scenario {scenario.id})", flush=True)
        print(f"Sample CSVs: {root / 'samples'}", flush=True)
        print(f"Start the UI: STATLAB_API_ORIGIN={base_url} make ui", flush=True)
        await asyncio.Event().wait()
    finally:
        try:
            if server is not None:
                await server.stop()
        finally:
            mcp.stop()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    lock = Path("uv.lock")
    if not lock.is_file() or lock.stat().st_size == 0:
        print("run from the repository root", file=sys.stderr)
        return 2
    scenarios = {s.id: s for s in load_scenarios(args.scenarios)}
    if args.scenario not in scenarios:
        print(f"unknown scenario id: {args.scenario}", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        asyncio.run(_serve(args, scenarios[args.scenario]))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
