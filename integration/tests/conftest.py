"""pytest fixtures for the integration suite (chunk 89a): session services, one run per scenario."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from integration.model import Scenario, load_scenarios
from integration.session import (
    ScenarioRecord,
    SuiteServices,
    make_workspace,
    open_services,
    run_one,
    setup_logging,
    write_summary,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--llm", choices=("live", "offline", "offline-rich"), default=None)
    parser.addoption("--budget-usd", type=float, default=2.0)
    parser.addoption("--log-debug", action="store_true")
    parser.addoption("--scenarios-file", type=Path, default=Path("integration/scenarios.toml"))


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("llm") is None:
        raise pytest.UsageError("--llm is required: live, offline or offline-rich")
    lock = Path("uv.lock")
    if not lock.is_file() or lock.stat().st_size == 0:
        raise pytest.UsageError("run from the repository root")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "scenario" in metafunc.fixturenames:
        scenarios = load_scenarios(Path(metafunc.config.getoption("scenarios_file")))
        metafunc.parametrize("scenario", scenarios, ids=lambda s: s.id)


@dataclass
class Suite:
    services: SuiteServices
    started: datetime
    records: dict[str, ScenarioRecord] = field(default_factory=dict)
    stopped: bool = False


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def suite(pytestconfig: pytest.Config) -> AsyncIterator[Suite]:
    mode = pytestconfig.getoption("llm")
    started = datetime.now(UTC)
    workspace = make_workspace(mode, started)
    setup_logging(workspace.api_log)
    if pytestconfig.getoption("log_debug"):
        logging.getLogger("statlab").setLevel(logging.DEBUG)
    async with open_services(
        workspace, mode=mode, budget_usd=pytestconfig.getoption("budget_usd")
    ) as services:
        session_suite = Suite(services, started)
        yield session_suite
    write_summary(
        workspace,
        list(session_suite.records.values()),
        mode=mode,
        started=started.isoformat(),
        ended=datetime.now(UTC).isoformat(),
    )


@pytest_asyncio.fixture(loop_scope="session")
async def scenario_record(suite: Suite, scenario: Scenario) -> ScenarioRecord:
    cached = suite.records.get(scenario.id)
    if cached is not None and cached.result != "SKIPPED":
        return cached
    if cached is not None or suite.stopped:
        suite.records[scenario.id] = ScenarioRecord(scenario)
        pytest.skip("suite budget reached")
    services = suite.services
    if services.provider is not None:
        services.provider.scenario = scenario
    record = await run_one(
        scenario,
        driver=services.driver,
        workspace=services.workspace,
        mcp=services.mcp,
        budget=services.budget,
        live=services.live,
    )
    suite.records[scenario.id] = record
    suite.stopped = record.budget_stopped
    return record
