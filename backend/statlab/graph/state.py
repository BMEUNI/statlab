"""Small typed node boundaries; no fitted objects live in run state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import CandidateSpec

from statlab.domains.base import GatePolicy, NodeKind, NodeResult, NodeSpec
from statlab.errors import EngineError
from statlab.persistence.models import RunMode, RunStatus


class SpineEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    kind: NodeKind
    skippable: bool
    decision_schema: str
    tool_phases: tuple[str, ...]
    gate_policy: GatePolicy

    @classmethod
    def from_spec(cls, spec: NodeSpec) -> SpineEntry:
        return cls(
            name=spec.name,
            kind=spec.kind,
            skippable=spec.skippable,
            decision_schema=f"{spec.decision_schema.__module__}.{spec.decision_schema.__qualname__}",
            tool_phases=spec.tool_phases,
            gate_policy=spec.gate_policy,
        )


class BoundaryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_name: str
    execution_id: str
    result: NodeResult


class CandidateProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    public_id: str = Field(pattern=r"^c_[0-9]{2,}\z")
    spec: CandidateSpec
    results: tuple[BoundaryResult, ...] = ()

    def database_id(self, run_id: str) -> str:
        """Length-prefix the run id so the global key is unambiguous."""
        return f"{len(run_id)}:{run_id}:{self.public_id}"


class RunState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    spine_id: str
    spine: tuple[SpineEntry, ...]
    cursor: int = Field(default=0, ge=0)
    status: RunStatus = "pending"
    mode: RunMode
    shared_results: tuple[BoundaryResult, ...] = ()
    candidate: CandidateProgress | None = None
    pending_result: BoundaryResult | None = None

    @model_validator(mode="after")
    def _topology(self) -> RunState:
        if self.cursor > len(self.spine):
            raise ValueError("cursor is beyond declared spine")
        if len({entry.name for entry in self.spine}) != len(self.spine):
            raise ValueError("declared node names must be unique")
        completed = self.spine[: self.cursor]
        if tuple(item.node_name for item in self.shared_results) != tuple(
            entry.name for entry in completed if entry.kind == "SHARED"
        ):
            raise ValueError("shared boundaries disagree with cursor")
        candidate_results = self.candidate.results if self.candidate else ()
        if tuple(item.node_name for item in candidate_results) != tuple(
            entry.name for entry in completed if entry.kind == "PER_CANDIDATE"
        ):
            raise ValueError("candidate boundaries disagree with cursor")
        if any(
            item.result.status not in ("completed", "skipped")
            for item in (*self.shared_results, *candidate_results)
        ):
            raise ValueError("committed boundaries must be completed or skipped")
        if self.status == "completed" and self.cursor != len(self.spine):
            raise ValueError("completed run has unfinished spine")
        if self.status in ("failed", "awaiting_user") and (
            self.pending_result is None
            or self.cursor == len(self.spine)
            or self.pending_result.node_name != self.spine[self.cursor].name
            or self.pending_result.result.status != self.status
        ):
            raise ValueError("halted run requires matching pending boundary")
        return self

    @classmethod
    def initial(
        cls,
        *,
        run_id: str,
        spine_id: str,
        nodes: tuple[NodeSpec, ...],
        mode: RunMode,
        candidates: tuple[CandidateSpec, ...] = (),
    ) -> RunState:
        if len(candidates) > 1:
            raise EngineError("multiple candidates require fan-out support")
        candidate = CandidateProgress(public_id="c_01", spec=candidates[0]) if candidates else None
        return cls(
            run_id=run_id,
            spine_id=spine_id,
            spine=tuple(SpineEntry.from_spec(node) for node in nodes),
            mode=mode,
            candidate=candidate,
        )

    @property
    def retained_refs(self) -> frozenset[ArtifactRef]:
        results = self.shared_results + (self.candidate.results if self.candidate else ())
        return frozenset(ref for boundary in results for ref in boundary.result.artifacts)
