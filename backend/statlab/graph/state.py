"""Small typed node boundaries; no fitted objects live in run state."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import CandidateSpec

from statlab.domains.base import GatePolicy, NodeKind, NodeResult, NodeSpec
from statlab.errors import EngineError
from statlab.graph.candidates import BudgetState, TraversalPlan
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
    wave: int = Field(default=1, ge=1)
    position: int = Field(default=0, ge=0)
    retune_count: int = Field(default=0, ge=0)
    status: Literal[
        "pending", "running", "completed", "failed", "awaiting_user", "budget_exhausted"
    ] = "pending"
    pending_result: BoundaryResult | None = None

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
    branches: tuple[CandidateProgress, ...] = Field(
        default=(), validation_alias=AliasChoices("branches", "candidate")
    )
    traversal: TraversalPlan | None = None
    wave: int = Field(default=1, ge=1)
    budget: BudgetState | None = None
    pending_result: BoundaryResult | None = None

    @field_validator("branches", mode="before")
    @classmethod
    def _legacy(cls, value: object, info: ValidationInfo) -> tuple[CandidateProgress, ...]:
        if value is None:
            return ()
        values = value if isinstance(value, (list, tuple)) else (value,)
        return tuple(
            item
            if isinstance(item, CandidateProgress)
            else CandidateProgress.model_validate_json(
                json.dumps({**item, "position": info.data.get("cursor", 0)})
                if isinstance(item, dict) and "position" not in item
                else json.dumps(item),
                strict=True,
            )
            for item in values
        )

    @property
    def candidate(self) -> CandidateProgress | None:
        """B7 read compatibility; fan-out consumers use branches."""
        return self.branches[0] if len(self.branches) == 1 else None

    @model_validator(mode="after")
    def _topology(self) -> RunState:
        if self.cursor > len(self.spine):
            raise ValueError("cursor is beyond declared spine")
        if len({entry.name for entry in self.spine}) != len(self.spine):
            raise ValueError("declared node names must be unique")
        if len({branch.public_id for branch in self.branches}) != len(self.branches):
            raise ValueError("branch identities must be unique")
        if len({branch.spec.spec_hash for branch in self.branches}) != len(self.branches):
            raise ValueError("branch specifications must be unique")
        if self.traversal is None:
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
        else:
            try:
                start, end = self.traversal.region(self.spine)
            except EngineError as exc:
                raise ValueError(str(exc)) from exc
            names = tuple(entry.name for entry in self.spine)
            shared_names = [item.node_name for item in self.shared_results]
            expected = [entry.name for entry in self.spine[: self.cursor] if entry.kind == "SHARED"]
            # The overlapped boundary may finish before the branch barrier advances.
            if (
                self.traversal.overlap_shared is not None
                and self.cursor == start - 1
                and self.traversal.overlap_shared in shared_names
            ):
                expected.append(self.traversal.overlap_shared)
            design = self.traversal.design_node
            repeats = 0
            filtered: list[str] = []
            for name in shared_names:
                if name == design and name in filtered:
                    repeats += 1
                else:
                    filtered.append(name)
            if filtered != expected:
                raise ValueError("shared boundaries disagree with cursor")
            if repeats > self.wave - 1:
                raise ValueError("design history exceeds wave")
            for branch in self.branches:
                if branch.wave > self.wave or branch.position > end:
                    raise ValueError("branch position/wave exceeds declaration")
                position = max(start, branch.position)
                history = tuple(item.node_name for item in branch.results)
                expected_history = list(names[start:position])
                if branch.retune_count:
                    training = self.traversal.training_node
                    evaluation = self.traversal.evaluation_node
                    if training is None or evaluation is None:
                        raise ValueError("retune requires training/evaluation")
                    t, e = names.index(training), names.index(evaluation)
                    expected_history = list(names[start:t])
                    expected_history += list(names[t : e + 1]) * branch.retune_count
                    expected_history += list(names[t:position])
                if history != tuple(expected_history):
                    raise ValueError("candidate boundaries disagree with position")
                if branch.status == "completed" and branch.position != end:
                    raise ValueError("completed candidate has unfinished region")
                if branch.status in ("failed", "awaiting_user", "budget_exhausted") and (
                    branch.pending_result is None
                    or position >= end
                    or branch.pending_result.node_name != names[position]
                    or branch.pending_result.result.status
                    != ("failed" if branch.status == "budget_exhausted" else branch.status)
                ):
                    raise ValueError("halted candidate requires matching pending boundary")
            if (
                self.cursor >= end
                and self.branches
                and any(
                    branch.status in ("pending", "running", "awaiting_user")
                    for branch in self.branches
                )
            ):
                raise ValueError("shared tail precedes candidate join")
        boundaries = (
            *self.shared_results,
            *(item for branch in self.branches for item in branch.results),
        )
        if any(item.result.status not in ("completed", "skipped") for item in boundaries):
            raise ValueError("committed boundaries must be completed or skipped")
        if self.status == "completed" and self.cursor != len(self.spine):
            raise ValueError("completed run has unfinished spine")
        branch_halt = self.traversal is not None and any(
            branch.status == self.status
            or (self.status == "failed" and branch.status == "budget_exhausted")
            for branch in self.branches
        )
        halted_nodes = tuple(entry.name for entry in self.spine[self.cursor : self.cursor + 1])
        if self.traversal is not None and self.wave > 1:
            start, _ = self.traversal.region(self.spine)
            design = self.traversal.design_node
            current_wave = tuple(branch for branch in self.branches if branch.wave == self.wave)
            if (
                design is not None
                and self.cursor == (start - 1 if self.traversal.overlap_shared else start)
                and sum(item.node_name == design for item in self.shared_results) == self.wave - 1
                and current_wave
                and all(
                    branch.status == "pending" and not branch.results and branch.position == start
                    for branch in current_wave
                )
            ):
                halted_nodes += (design,)
        if (
            self.status in ("failed", "awaiting_user")
            and not branch_halt
            and (
                self.pending_result is None
                or self.pending_result.node_name not in halted_nodes
                or self.pending_result.result.status != self.status
            )
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
        branches = tuple(
            CandidateProgress(public_id=f"c_{i:02d}", spec=spec)
            for i, spec in enumerate(candidates, 1)
        )
        return cls(
            run_id=run_id,
            spine_id=spine_id,
            spine=tuple(SpineEntry.from_spec(node) for node in nodes),
            mode=mode,
            branches=branches,
            traversal=TraversalPlan() if len(branches) > 1 else None,
        )

    @property
    def retained_refs(self) -> frozenset[ArtifactRef]:
        results = self.shared_results + tuple(
            item for branch in self.branches for item in branch.results
        )
        return frozenset(ref for boundary in results for ref in boundary.result.artifacts)
