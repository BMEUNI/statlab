"""pytest fixtures for the integration suite (chunk 89a): session services, one run per scenario."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import litellm
import pytest
import pytest_asyncio
from statlab.llm.routing import ModelRouter, load_routing_environment

from integration.cassette import CassetteProvider
from integration.checks import CheckResult
from integration.judge import StatlabJudge, offline_acompletion
from integration.model import Scenario, load_scenarios
from integration.offline_llm import OfflineProvider
from integration.session import (
    ScenarioRecord,
    Suite,
    make_workspace,
    open_services,
    run_one,
    setup_logging,
    write_summary,
)

REPLAY_SKIP = "timing-dependent lifecycle action; live or record only"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--llm", choices=("live", "offline", "offline-rich", "record", "replay"), default=None
    )
    parser.addoption("--cassettes", type=Path, default=Path("integration/.cache/cassettes"))
    parser.addoption("--budget-usd", type=float, default=2.0)
    parser.addoption("--log-debug", action="store_true")
    parser.addoption("--record-missing", action="store_true")
    parser.addoption("--scenarios-file", type=Path, default=Path("integration/scenarios.toml"))


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("llm") is None:
        raise pytest.UsageError("--llm is required: live, offline, offline-rich, record or replay")
    if config.getoption("record_missing") and config.getoption("llm") != "replay":
        raise pytest.UsageError("--record-missing requires --llm replay")
    lock = Path("uv.lock")
    if not lock.is_file() or lock.stat().st_size == 0:
        raise pytest.UsageError("run from the repository root")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "scenario" in metafunc.fixturenames:
        scenarios = load_scenarios(Path(metafunc.config.getoption("scenarios_file")))
        metafunc.parametrize("scenario", scenarios, ids=lambda s: s.id)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def suite(pytestconfig: pytest.Config) -> AsyncIterator[Suite]:
    mode = pytestconfig.getoption("llm")
    started = datetime.now(UTC)
    workspace = make_workspace(mode, started)
    setup_logging(workspace.api_log)
    if pytestconfig.getoption("log_debug"):
        logging.getLogger("statlab").setLevel(logging.DEBUG)
    async with open_services(
        workspace,
        mode=mode,
        budget_usd=pytestconfig.getoption("budget_usd"),
        cassettes=pytestconfig.getoption("cassettes"),
    ) as services:
        if isinstance(services.provider, CassetteProvider):
            services.provider.record_missing = pytestconfig.getoption("record_missing")
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
    if cached is not None:
        pytest.skip(cached.skip_reason or "suite budget reached")
    if suite.stopped:
        suite.records[scenario.id] = ScenarioRecord(scenario, skip_reason="suite budget reached")
        pytest.skip("suite budget reached")
    services = suite.services
    provider = services.provider
    if (
        isinstance(provider, CassetteProvider)
        and provider.mode == "replay"
        and not scenario.replayable
    ):
        suite.records[scenario.id] = ScenarioRecord(scenario, skip_reason=REPLAY_SKIP)
        pytest.skip(REPLAY_SKIP)
    if isinstance(provider, OfflineProvider):
        provider.scenario = scenario
    elif isinstance(provider, CassetteProvider):
        provider.start(scenario.id)
    record = await run_one(
        scenario,
        driver=services.driver,
        workspace=services.workspace,
        mcp=services.mcp,
        budget=services.budget,
        live=services.live,
    )
    if isinstance(provider, CassetteProvider) and provider.mode == "replay":
        record.checks.append(
            CheckResult(
                "H-drift",
                "replayed prompts match the recording",
                "soft",
                not provider.drift,
                ", ".join(provider.drift),
            )
        )
    suite.records[scenario.id] = record
    suite.stopped = record.budget_stopped
    return record


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def judge(suite: Suite, pytestconfig: pytest.Config) -> AsyncIterator[StatlabJudge]:
    model = ModelRouter({}, load_routing_environment()).model_for("judge")
    provider = suite.services.provider
    acompletion: Callable[..., Awaitable[object]]
    if isinstance(provider, CassetteProvider):
        acompletion = provider
    elif pytestconfig.getoption("llm") == "live":
        acompletion = litellm.acompletion
    else:
        acompletion = offline_acompletion
    instance = StatlabJudge(model, acompletion)
    yield instance
    print(
        f"judge: {instance.calls} calls, {instance.prompt_tokens} prompt + "
        f"{instance.completion_tokens} completion tokens",
        flush=True,
    )
