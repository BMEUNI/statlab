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


@pytest.mark.parametrize("value", ["", "x" * 601])
def test_user_summary_bounds(value: str) -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate({"rationale": "r", "user_summary": value})


def test_user_summary_description() -> None:
    schema = IntakeDecision.model_json_schema()
    assert schema["properties"]["user_summary"]["description"].startswith("Always fill this")
