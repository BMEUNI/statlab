from __future__ import annotations

import pytest
from pydantic import ValidationError
from statlab.domains.decisions import Decision, IntakeDecision


def test_user_summary_default_and_set() -> None:
    assert Decision(rationale="r").user_summary is None
    assert (
        IntakeDecision(rationale="r", user_summary="Loaded 10 rows.").user_summary
        == "Loaded 10 rows."
    )


@pytest.mark.parametrize("value", ["", 7])
def test_user_summary_bounds(value: str) -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate({"rationale": "r", "user_summary": value})


def test_long_user_summary_is_shortened() -> None:
    exact = "x" * 600
    assert Decision(rationale="r", user_summary=exact).user_summary == exact
    assert (
        Decision.model_validate(
            {"rationale": "r", "user_summary": "First sentence. " + "y" * 700}
        ).user_summary
        == "First sentence."
    )
    unbroken = Decision.model_validate({"rationale": "r", "user_summary": "z" * 700}).user_summary
    assert unbroken == "z" * 599 + "…"
    assert len(unbroken) == 600
    edge = "a" * 598 + ". " + "b" * 50
    assert (
        Decision.model_validate({"rationale": "r", "user_summary": edge}).user_summary
        == "a" * 598 + "."
    )
    schema = IntakeDecision.model_json_schema()
    assert schema["properties"]["user_summary"]["anyOf"][0]["maxLength"] == 600


def test_user_summary_description() -> None:
    schema = IntakeDecision.model_json_schema()
    assert schema["properties"]["user_summary"]["description"].startswith("Always fill this")
