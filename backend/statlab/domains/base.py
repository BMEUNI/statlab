"""Node interface types. The engine knows only these; it never names a node.

D27's invariant: no node name appears outside `backend/statlab/domains/`. Chunk 27 adds the
`Domain` aggregate and the concrete `tabular_supervised` spine to this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.envelope import Failure

NodeKind = Literal["SHARED", "PER_CANDIDATE"]
"""SHARED runs once per run; PER_CANDIDATE runs once per candidate (PLAN.md §4.2)."""

GatePolicy = Literal["always", "exception", "never"]
"""Guided-mode gating (D29). `always` for shared nodes; `exception` for per-candidate nodes,
which never ask in guided mode (the experiment design approval covers each candidate) and stop
only on failure (chunk 46c); `never` for nodes with no user-meaningful decision."""

NodeStatus = Literal["completed", "skipped", "failed", "awaiting_user"]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """A declared position in the spine. Ordering is the list's; this carries no index.

    A frozen dataclass rather than a pydantic model: these are constructed in code by chunk 27,
    never parsed from untrusted input, so validation would be cost without benefit.
    """

    name: str
    kind: NodeKind
    skippable: bool
    decision_schema: type[BaseModel]
    tool_phases: tuple[str, ...]
    gate_policy: GatePolicy


@dataclass(frozen=True, slots=True)
class Domain:
    """Caller-selected metadata; execution remains an engine callback."""

    name: str
    contract_model: type[BaseModel]
    skill_file: Path
    tool_phases: tuple[str, ...]
    nodes: tuple[NodeSpec, ...]


class NodeResult(BaseModel):
    """What a node hands back to the engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NodeStatus
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    skip_reason: str | None = None
    error: Failure | None = None

    @model_validator(mode="after")
    def _skip_reason_iff_skipped(self) -> NodeResult:
        """A skip must always carry a reason, and only a skip may (D3)."""
        if self.status == "skipped":
            if not self.skip_reason or not self.skip_reason.strip():
                raise ValueError("skip_reason is mandatory and non-blank when status == 'skipped'")
        elif self.skip_reason is not None:
            raise ValueError(f"skip_reason is forbidden when status == {self.status!r}")
        return self

    @model_validator(mode="after")
    def _error_iff_failed(self) -> NodeResult:
        """A failure must always carry a Failure, and only a failure may."""
        if self.status == "failed":
            if self.error is None:
                raise ValueError("error is mandatory when status == 'failed'")
        elif self.error is not None:
            raise ValueError(f"error is forbidden when status == {self.status!r}")
        return self

    @model_validator(mode="after")
    def _no_artifacts_on_failure(self) -> NodeResult:
        """A failed or skipped node reports no artifacts and no metrics.

        Artifacts may exist on disk from a partial attempt — chunk 06 keeps them for diagnosis
        (D24) — but they are not part of the run's state, because the engine must not treat a
        half-finished node's output as an input to the next one.
        """
        if self.status in ("failed", "skipped"):
            if self.artifacts:
                raise ValueError(f"artifacts must be empty when status == {self.status!r}")
            if self.metrics:
                raise ValueError(f"metrics must be empty when status == {self.status!r}")
        return self
