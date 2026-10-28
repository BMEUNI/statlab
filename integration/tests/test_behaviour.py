"""Per-scenario behaviour checks (G1-G7, expectations, extra checks) as asserts (chunk 89a)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from integration.session import ScenarioRecord


@pytest.mark.asyncio(loop_scope="session")
async def test_scenario_behaviour(scenario_record: ScenarioRecord) -> None:
    failed = [c for c in scenario_record.checks if c.severity == "must" and not c.passed]
    assert not failed, "\n".join(
        [str(scenario_record.folder), *(f"{c.id}: {c.description} -- {c.detail}" for c in failed)]
    )


@pytest.mark.soft
@pytest.mark.asyncio(loop_scope="session")
async def test_scenario_soft(
    scenario_record: ScenarioRecord, record_property: Callable[[str, object], None]
) -> None:
    for c in scenario_record.checks:
        if c.severity == "soft":
            record_property(c.id, "pass" if c.passed else f"fail: {c.detail}")
