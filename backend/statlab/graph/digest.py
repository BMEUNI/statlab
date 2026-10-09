"""Compact run history for prompt context only; code never reads it back (chunks 47, 59)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import JsonValue

from statlab.graph.state import BoundaryResult, CandidateProgress

_DIGEST_DROP: frozenset[str] = frozenset({"registry", "candidate_set", "sections"})
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _brief(text: str) -> str:
    short = " ".join(_SENTENCE.split(text.strip())[:2])
    return short if len(short) <= 400 else short[:399] + "…"


def _prune(value: JsonValue, drop: frozenset[str]) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: _brief(item)
            if key == "rationale" and isinstance(item, str)
            else _prune(item, drop)
            for key, item in value.items()
            if key not in drop
        }
    if isinstance(value, list):
        return [_prune(item, drop) for item in value]
    return value


def boundary_digest(boundaries: Sequence[BoundaryResult], *, keep_tools: bool) -> list[JsonValue]:
    """Compact shared results for prompt context only; code never reads it back."""
    drop = _DIGEST_DROP if keep_tools else _DIGEST_DROP | {"tools"}
    latest: dict[str, BoundaryResult] = {}
    for boundary in boundaries:
        latest[boundary.node_name] = boundary
    digest: list[JsonValue] = []
    for boundary in latest.values():
        result = boundary.result
        entry: dict[str, JsonValue] = {
            "node": boundary.node_name,
            "status": result.status,
            "summary": _prune(result.summary, drop),
        }
        if result.warnings:
            entry["warnings"] = list(result.warnings)
        if result.skip_reason is not None:
            entry["skip_reason"] = result.skip_reason
        if result.error is not None:
            entry["error"] = result.error.model_dump(mode="json")
        digest.append(entry)
    return digest


def candidate_digest(branch: CandidateProgress) -> dict[str, JsonValue]:
    """A candidate's progress with its earlier steps digested, for prompt context only."""
    return {
        **branch.model_dump(mode="json", exclude={"results", "pending_result"}),
        "results": boundary_digest(branch.results, keep_tools=False),
    }
