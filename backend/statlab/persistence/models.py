"""Status vocabularies and row models: chunk 05 §3.5, §3.7.

One frozen pydantic model per repository-backed table. JSON columns are typed `str`, holding
verbatim `model_dump_json()` output -- never `pydantic.Json[T]`, which would parse on read and
re-serialise on write through a different code path. See `ContractRow.contract()`'s docstring
for why that distinction is a correctness requirement, not a style preference.
"""

from __future__ import annotations

import json
from typing import Final, Literal, NoReturn, get_args

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta
from statlab_contracts.contract import DataContract
from statlab_contracts.vocabulary import SplitRole

from statlab.domains.base import NodeStatus
from statlab.errors import PersistenceError

RunStatus = Literal[
    "pending",
    "running",
    "awaiting_user",
    "paused",
    "failed",
    "cancelled",
    "completed",
    "superseded",
]
"""`superseded` (R4) is the status a rewind marks the parent run with (D17, PLAN.md §5): the
parent halted and a fork continues from it. Without it the DDL CHECK would reject the write
D36b performs."""
RunMode = Literal["guided", "autonomous"]

PreTerminalStatus = Literal["pending", "running"]
"""States a `node_executions` row occupies before the node stops. No `NodeResult` can carry
these -- a `NodeResult` only exists once the node has stopped, which is why its validators
(`skip_reason` iff skipped, `error` iff failed) would be meaningless for them."""

NodeExecutionStatus = PreTerminalStatus | NodeStatus
"""A row's lifecycle state: pre-terminal states plus every terminal outcome `NodeStatus`
defines. Derived, so adding a terminal outcome to `NodeStatus` flows into the schema vocabulary
automatically and §3.8's drift test catches the DDL that was not updated -- the same discipline
as chunk 03's `METRICS_FOR_TASK` and chunk 04's `DIMENSION_FOR_FIELD`."""

NODE_EXECUTION_STATUSES: Final[frozenset[str]] = frozenset(
    get_args(PreTerminalStatus) + get_args(NodeStatus)
)
"""Flat member set, for the CHECK-constraint drift test. `get_args` on the union itself returns
the two Literals rather than six strings, so the flattening is done once, here."""


class ProjectRow(BaseModel):
    """A `projects` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    slug: str
    name: str
    created_at: str


class DatasetRow(BaseModel):
    """A `datasets` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    filename: str
    path: str
    sha256: str
    n_rows: int
    n_cols: int
    created_at: str


class ContractRow(BaseModel):
    """A `contracts` row.

    The DB column is named `json` (PLAN.md §10.2, verbatim). The Python field here is
    `contract_json`, not `json`: pydantic's `BaseModel` still exposes a deprecated `.json()`
    method (removed only in v3), and a field literally named `json` overrides it with an
    incompatible type -- a real `mypy --strict` error, not a style nit, and calling the
    shadowed slot at runtime raises `PydanticDeprecatedSince20`, which `filterwarnings =
    ["error"]` turns into a test failure. The repository layer maps the `json` column to this
    field by name in the `SELECT`/model-validate boundary, not through pydantic aliasing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    path: str
    contract_json: str
    """Verbatim `DataContract.model_dump_json()`. Stored as text, never re-serialised.

    Chunk 04 established why: `CandidateSpec.spec_hash` is a sha256 over canonical JSON, so a
    store-then-reload path that re-serialises through a different code path can change a
    candidate's identity, silently breaking dedupe across a restart -- producing duplicate work
    and no error message. Treating stored JSON as opaque text makes that class of bug
    impossible rather than merely unlikely.
    """
    version: int
    created_at: str

    def contract(self) -> DataContract:
        """Parse the stored JSON.

        Raises
        ------
        PersistenceError
            On invalid JSON or a JSON document that no longer satisfies `DataContract`, which
            means the row predates a schema change or was hand-edited.
        """
        try:
            return DataContract.model_validate_json(self.contract_json)
        except ValidationError as exc:
            raise PersistenceError(
                f"contract row {self.id!r} holds invalid contract JSON", cause=exc
            ) from exc


class RunRow(BaseModel):
    """A `runs` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    contract_id: str
    mode: RunMode
    status: RunStatus
    current_node: str | None
    champion_model_id: str | None
    forked_from_run_id: str | None
    started_at: str
    ended_at: str | None
    config_json: str
    test_scored_at: str | None
    """§6.3: the durable half of D26's test-set-scored-exactly-once guarantee. `None` until
    `claim_test_scoring` wins the claim; set to the claim timestamp thereafter, never cleared.
    The other half of D26 ("test unreadable before node 12") needs no column -- it is already
    `champion_model_id IS NULL`."""
    test_fingerprint: str | None
    """R4: the fingerprint of the test set this run scored, or `None` before it scores. Chunk 21
    sets it once (write-once, via `set_test_fingerprint`) and `count_prior_test_scorings` uses it
    to count cross-run test exposures through forks (D32). Nullable: most runs never reach node
    12."""


class ArtifactRow(BaseModel):
    """An `artifacts` row (chunk 06)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    run_id: str
    kind: ArtifactKind
    path: str
    meta_json: str
    produced_by: str
    ts: str
    pruned_at: str | None
    """`None` until pruned. Set, never cleared, by `mark_pruned`/`prune_intermediates`.

    Pruning sets this instead of deleting the row: deleting would break `tool_calls`'
    reference to the artifact id, and a row whose `path` points at a missing file with no
    explanation would make deliberate pruning indistinguishable from data loss. Same reasoning
    that put `RunRow.test_scored_at` in the schema rather than in process memory.
    """
    split: SplitRole | None
    derived_from_test: bool

    def meta(self) -> ArtifactMeta:
        """Parse stored metadata and wrap corrupt rows as persistence errors."""
        try:
            return ArtifactMeta.model_validate_json(self.meta_json)
        except ValidationError as exc:
            raise PersistenceError(
                f"artifact row {self.id!r} holds invalid meta JSON", cause=exc
            ) from exc


class NodeExecutionRow(BaseModel):
    """A node attempt to which every dispatched tool call is attributed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    run_id: str
    candidate_id: str | None
    node_name: str
    seq: int
    attempt: int
    status: NodeExecutionStatus
    summary: str | None
    error: str | None
    concurrency_at_start: int | None
    started_at: str
    ended_at: str | None


class CandidateRow(BaseModel):
    """The engine's persisted candidate identity and human description."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    run_id: str
    label: str
    rationale: str
    spec_json: str
    varies_from_baseline_json: str
    wave: int
    status: str
    rank: int | None
    created_at: str


class GateRow(BaseModel):
    """An existing gate; recovery never creates or answers it."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    run_id: str
    node_execution_id: str
    question_json: str
    answer_json: str | None
    status: str
    ts: str

    def question(self) -> dict[str, JsonValue]:
        try:
            parsed = json.loads(self.question_json, parse_constant=_reject_json_constant)
            return TypeAdapter(dict[str, JsonValue]).validate_python(parsed, strict=True)
        except ValueError as exc:
            raise PersistenceError("invalid gate question JSON", cause=exc) from exc


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError("invalid JSON numeric constant")


class ToolCallRow(BaseModel):
    """Persisted request and response for one MCP dispatch attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    run_id: str
    node_execution_id: str
    tool_name: str
    args_json: str
    result_json: str
    duration_ms: int
    ts: str


class LlmCallRow(BaseModel):
    """Persisted accounting metadata for one structured-output request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    run_id: str
    node_execution_id: str
    signature: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float
    duration_ms: int
    ts: str


class MemoryRow(BaseModel):
    """An immutable project fact with optional run provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    project_id: str
    key: str
    value_json: str
    source_run_id: str | None
    ts: str

    def value(self) -> JsonValue:
        try:
            parsed = json.loads(self.value_json, parse_constant=_reject_json_constant)
            value: JsonValue = TypeAdapter(JsonValue).validate_python(parsed, strict=True)
            json.dumps(value, allow_nan=False)
            return value
        except ValueError, TypeError:
            raise PersistenceError("invalid project memory JSON") from None
