"""DeepEval judged quality metrics for the scenario runs (chunk 89c)."""

from __future__ import annotations

import json

import pytest
from deepeval.metrics import FaithfulnessMetric, GEval, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

from integration.cassette import CassetteProvider
from integration.judge import StatlabJudge
from integration.model import Scenario
from integration.session import (
    ScenarioRecord,
    Suite,
    champion_summary,
    final_status,
    judged_mode,
    tools_called,
)


@pytest.mark.asyncio(loop_scope="session")
async def test_tools_match_expectation(
    scenario: Scenario, scenario_record: ScenarioRecord, judge: StatlabJudge
) -> None:
    if not scenario.expect.tools_used:
        pytest.skip("no expected tools")
    metric = ToolCorrectnessMetric(threshold=1.0, model=judge, include_reason=True, async_mode=True)
    case = LLMTestCase(
        input=scenario.title,
        actual_output=final_status(scenario_record),
        tools_called=[ToolCall(name=name) for name in tools_called(scenario_record)],
        expected_tools=[ToolCall(name=name) for name in scenario.expect.tools_used],
    )
    await metric.a_measure(case)
    assert metric.is_successful(), metric.reason


@pytest.mark.asyncio(loop_scope="session")
async def test_report_faithful_to_evidence(
    scenario: Scenario,
    scenario_record: ScenarioRecord,
    judge: StatlabJudge,
    suite: Suite,
    pytestconfig: pytest.Config,
) -> None:
    if not judged_mode(pytestconfig):
        pytest.skip("judged metrics run in record, replay and live modes")
    summary = champion_summary(scenario_record)
    if summary is None:
        pytest.skip("no champion summary")
    decision = summary["decision"]
    assert isinstance(decision, dict)
    sections = decision["sections"]
    if not isinstance(sections, list) or not sections:
        pytest.skip("no report sections")
    actual_output = "\n\n".join(
        f"{section['heading']}\n{section['body']}"
        for section in sections
        if isinstance(section, dict)
    )
    metric = FaithfulnessMetric(threshold=0.7, model=judge, include_reason=True, async_mode=True)
    case = LLMTestCase(
        input="Explain the champion choice",
        actual_output=actual_output,
        retrieval_context=[
            json.dumps(summary["ranking"], sort_keys=True),
            json.dumps(summary["comparison"], sort_keys=True),
        ],
    )
    provider = suite.services.provider
    if isinstance(provider, CassetteProvider):
        provider.start(f"{scenario.id}-judge")
    await metric.a_measure(case)
    assert metric.is_successful(), metric.reason


@pytest.mark.asyncio(loop_scope="session")
async def test_champion_rationale_consistent(
    scenario: Scenario,
    scenario_record: ScenarioRecord,
    judge: StatlabJudge,
    suite: Suite,
    pytestconfig: pytest.Config,
) -> None:
    summary = champion_summary(scenario_record)
    if not judged_mode(pytestconfig) or summary is None:
        pytest.skip("judged metrics run in record, replay and live modes")
    metric = GEval(
        name="Rationale consistency",
        criteria=(
            "The rationale must agree with the validation ranking in the input: it may only "
            "claim a candidate is better where the ranking supports it, and it must not "
            "mention any test-set result."
        ),
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        threshold=0.6,
        model=judge,
        async_mode=True,
    )
    decision = summary["decision"]
    assert isinstance(decision, dict)
    case = LLMTestCase(
        input=json.dumps(summary["ranking"], sort_keys=True),
        actual_output=str(decision["rationale"]),
    )
    provider = suite.services.provider
    if isinstance(provider, CassetteProvider):
        provider.start(f"{scenario.id}-judge")
    await metric.a_measure(case)
    assert metric.is_successful(), metric.reason
