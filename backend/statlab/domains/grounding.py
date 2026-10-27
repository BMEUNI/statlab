"""Cut LLM-written report sentences whose numbers are not in the evidence (chunk 87, PLAN D38)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import JsonValue
from statlab_contracts.reporting import ReportSection

# A written number: optional thousands commas, decimals, exponent, percent.
# Not part of a word (c_01, F1, v1.2).
_NUMBER: Final = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?!\w)%?"
)
_MARKER: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class Grounding:
    """Kept sections (unchanged objects when nothing was cut) and the cut text, in order.

    A dropped heading is recorded as ``"heading: <heading>"``.
    """

    sections: tuple[ReportSection, ...]
    removed: tuple[str, ...]


def _parse(token: str) -> tuple[float, int, bool, bool]:
    """Return (value, decimals, percent, exponent) for one written number."""
    percent = token.endswith("%")
    core = token.removesuffix("%").replace(",", "")
    exponent = "e" in core.lower()
    mantissa = re.split(r"[eE]", core)[0]
    decimals = len(mantissa.partition(".")[2])
    value = abs(float(core))
    return value, decimals, percent, exponent


def _values(source: JsonValue) -> list[float]:
    """Collect every number in a source; bools, nulls and keys are ignored."""
    if isinstance(source, bool) or source is None:
        return []
    if isinstance(source, int | float):
        return [abs(float(source))]
    if isinstance(source, str):
        return [_parse(match.group())[0] for match in _NUMBER.finditer(source)]
    if isinstance(source, list):
        return [value for item in source for value in _values(item)]
    return [value for item in source.values() for value in _values(item)]


def _grounded(token: str, known: Sequence[float]) -> bool:
    """Match a written number to evidence, rounded to its written decimals."""
    value, decimals, percent, exponent = _parse(token)
    if not percent and not exponent and decimals == 0 and value <= 10:
        return True
    if exponent:
        return any(abs(item - value) <= 0.005 * value for item in known)
    tolerance = 0.5 * 10.0**-decimals
    targets = [(value, tolerance)]
    if percent:
        targets.append((value / 100, tolerance / 100))
    return any(abs(item - target) <= bound + 1e-12 for target, bound in targets for item in known)


def _keep(text: str, known: Sequence[float]) -> bool:
    """True when every written number in the text is grounded."""
    return all(_grounded(match.group(), known) for match in _NUMBER.finditer(text))


def _filter_body(body: str, known: Sequence[float]) -> tuple[str, list[str]]:
    """Cut ungrounded sentences and table rows from a section body."""
    lines: list[str] = []
    removed: list[str] = []
    for line in body.split("\n"):
        if line.lstrip().startswith("|"):
            if _keep(line, known):
                lines.append(line)
            else:
                removed.append(line.strip())
            continue
        marker = _MARKER.match(line)
        prefix = marker.group() if marker else ""
        units = _SENTENCE.split(line[len(prefix) :])
        flags = [_keep(unit, known) for unit in units]
        kept = [unit for unit, ok in zip(units, flags, strict=True) if ok]
        removed.extend(unit.strip() for unit, ok in zip(units, flags, strict=True) if not ok)
        if len(kept) == len(units):
            lines.append(line)
        elif kept:
            lines.append(prefix + " ".join(kept))
    return "\n".join(lines), removed


def ground_sections(sections: Sequence[ReportSection], sources: Sequence[JsonValue]) -> Grounding:
    """Keep only report text whose numbers appear in the sources.

    Numbers match rounded to their written decimals; ``%`` also matches as a fraction; signs are
    ignored; exponents match within 0.5 %; integers 0-10 always pass. A cut never fails the step.
    """
    known = [value for source in sources for value in _values(source)]
    kept: list[ReportSection] = []
    removed: list[str] = []
    for section in sections:
        if not _keep(section.heading, known):
            removed.append(f"heading: {section.heading}")
            continue
        body, cut = _filter_body(section.body, known)
        removed.extend(cut)
        if not cut:
            kept.append(section)
        elif body.strip():
            kept.append(ReportSection(heading=section.heading, body=body))
    return Grounding(sections=tuple(kept), removed=tuple(removed))
