"""Artifact identity and kinds. Wire types only — no IO, no storage policy."""

from __future__ import annotations

import re
import secrets
from typing import Final, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

ArtifactRef = NewType("ArtifactRef", str)
"""An artifact ID. Opaque to the LLM; resolved to a path by chunk 06's registry."""

ARTIFACT_ID_PATTERN: Final = re.compile(r"^a_[0-9a-f]{16}\Z")
"""`a_` followed by exactly 16 lowercase hex characters. Chunk 06 mints these.

Anchored with `\\Z`, not a bare `$`: `$` matches immediately before a trailing newline as well
as at the true end of string, so `re.match(r"...$")` would wrongly accept `"a_...\\n"`. `\\Z`
matches only the absolute end.
"""

ArtifactKind = Literal[
    "dataframe",  # parquet
    "predictions",  # parquet: one column of predictions, or class probabilities
    "transformer",  # a fitted sklearn transformer (imputer, encoder, scaler)
    "model",  # a fitted sklearn estimator
    "inferential_fit",  # a fitted statsmodels results object
    "curve",  # json: roc / pr / calibration curve data (compute, not render)
    "table",  # json: coefficient table, metrics table, ranking
    "plot",  # png
    "report",  # markdown
]
"""Every artifact kind the tool catalogue (PLAN.md §6.3) can produce.

Deliberately closed. Adding a kind is a one-line change here plus one line in chunk 06's
extension map — which is the point: a new kind cannot be introduced by accident.
"""


def parse_artifact_ref(value: str) -> ArtifactRef:
    """Validate and narrow a raw string to an ArtifactRef.

    Raises
    ------
    ValueError
        If `value` does not match ARTIFACT_ID_PATTERN. Callers at a boundary must wrap this
        into a domain error; this module raises no domain errors because it must stay
        importable by both processes.
    """
    if not ARTIFACT_ID_PATTERN.match(value):
        raise ValueError(f"{value!r} is not a valid artifact ref (expected a_<16 hex chars>)")
    return ArtifactRef(value)


def new_artifact_ref() -> ArtifactRef:
    """Mint a fresh artifact ref. Lives here, beside `ARTIFACT_ID_PATTERN` and
    `parse_artifact_ref`, because both processes mint refs and this is the only package both
    may import. `secrets` is stdlib, so the contracts boundary holds.

    An artifact ref is born exactly once, in the process that writes the file -- usually the
    MCP server, which must not import `statlab.persistence.ids.new_id`.
    """
    return ArtifactRef(f"a_{secrets.token_hex(8)}")


class ArtifactMeta(BaseModel):
    """What a tool reports about an artifact it produced.

    Small by design: this travels in every tool response and is read by the LLM. It carries
    enough to reason about shape, never enough to reconstruct data (PLAN.md §3.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # `ref` is NOT validated against ARTIFACT_ID_PATTERN here. NewType is a static-only
    # construct, so runtime validation would need a separate Annotated type, and the only
    # producer of `ref` values is chunk 06's registry, which mints them via parse_artifact_ref
    # (or equivalent) at creation time. Re-validating an already-minted ref on every response
    # is cost with no new information — do not "fix" this by adding a validator here.
    ref: ArtifactRef
    kind: ArtifactKind
    label: str | None = Field(default=None, max_length=120)
    rows: int | None = Field(default=None, ge=0)
    cols: int | None = Field(default=None, ge=0)
