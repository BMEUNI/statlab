"""Scenario schema and loader for the integration harness."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from statlab.persistence.models import RunMode, RunStatus
from statlab_contracts.gates import GateAction
from statlab_contracts.vocabulary import TextEncoding

DatasetKey = Literal[
    "churn",
    "houses",
    "churn_dirty",
    "species",
    "sales",
    "visits",
    "reviews",
    "adult",
    "autompg",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GateRule(_Frozen):
    node: str
    phase: Literal["before", "after", "failure"] | None = None
    action: GateAction
    edits: dict[str, JsonValue] = {}
    instruction: str | None = None
    reason: str | None = None
    target: str | None = None
    once: bool = True


class Trigger(_Frozen):
    event: str | None = None
    node: str | None = None
    status: RunStatus | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Trigger:
        if (self.event is None) == (self.status is None):
            raise ValueError("exactly one of event/status must be set")
        if self.node is not None and self.event is None:
            raise ValueError("node is only allowed with event")
        return self


class Action(_Frozen):
    when: Trigger
    do: Literal["pause", "resume", "cancel", "message", "rewind", "restart_api"]
    text: str | None = None
    target: str | None = None
    request_id: str | None = None

    @model_validator(mode="after")
    def _fields_match_action(self) -> Action:
        if (self.do == "message") != (self.text is not None):
            raise ValueError("text is required iff do == 'message'")
        if self.do == "rewind":
            if self.target is None or self.request_id is None:
                raise ValueError("target and request_id are required for rewind")
        elif self.target is not None or self.request_id is not None:
            raise ValueError("target and request_id are only allowed for rewind")
        return self


class Expect(_Frozen):
    status: RunStatus | None = None
    nodes_completed: tuple[str, ...] = ()
    nodes_skipped: tuple[str, ...] = ()
    nodes_failed: tuple[str, ...] = ()
    nodes_not_run: tuple[str, ...] = ()
    failure_contains: str | None = None
    min_candidates_completed: int = 0
    candidate_count: int | None = None
    no_gates: bool = False
    gates_opened_for: tuple[str, ...] = ()
    gate_revision_min: dict[str, int] = {}
    champion: bool | None = None
    test_scored: bool | None = None
    report_contains: tuple[str, ...] = ()
    metrics_in_report: tuple[str, ...] = ()
    pipeline_replay: bool = False
    manifest: bool = False
    tools_used: tuple[str, ...] = ()
    events_include: tuple[str, ...] = ()
    messages_answered: int = 0
    fork_idempotent: bool = False
    child_status: RunStatus | None = None
    child_report_contains: tuple[str, ...] = ()
    child_not_rerun: tuple[str, ...] = ()
    proposal_complete: bool = False
    memory_rows_min: int = 0
    second_run_memory: bool = False
    shared_completed_once: bool = False


class Soft(_Frozen):
    inference: dict[str, Literal["reject H0", "not rejected"]] = {}
    metric_min: float | None = None
    metric_max: float | None = None
    summary_mentions: tuple[str, ...] = ()


class Setup(_Frozen):
    invalid_cases: bool = False
    propose: bool = False
    runs: int = Field(default=1, ge=1, le=2)


class OfflineHints(_Frozen):
    reference_levels: dict[str, str] = {}
    revised_candidates: tuple[str, ...] = ()
    text_encodings: tuple[TextEncoding, ...] = ()
    proposed_contract: dict[str, JsonValue] = {}


class Scenario(_Frozen):
    id: str = Field(pattern=r"^S\d{2}$")
    title: str
    dataset: DatasetKey
    mode: RunMode = "autonomous"
    contract: dict[str, JsonValue]
    setup: Setup = Setup()
    gates: tuple[GateRule, ...] = ()
    actions: tuple[Action, ...] = ()
    expect: Expect = Expect()
    soft: Soft = Soft()
    offline: OfflineHints = OfflineHints()
    timeout_s: int = Field(default=1200, gt=0)
    max_cost_usd: float = Field(default=0.25, gt=0)

    @property
    def replayable(self) -> bool:
        return not any(action.when.event is not None for action in self.actions)


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    items = raw.get("scenario", [])
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for item in items:
        scenario = Scenario.model_validate(item)
        if scenario.id in seen:
            raise ValueError(f"duplicate scenario id {scenario.id}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    return tuple(scenarios)
