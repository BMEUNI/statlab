"""Chart specs (v1) for interactive report plots, written beside each PNG (chunk 55)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
from pydantic import JsonValue

LINE_CAP = 500
POINT_CAP = 2000


def thin_indices(n: int, cap: int) -> np.ndarray:
    """Every index when `n <= cap`, else `cap` evenly spaced indices (deterministic)."""
    if n <= cap:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, cap).round().astype(int))


def _num(value: float) -> float | None:
    return None if not math.isfinite(value) else value


def _floats(values: np.ndarray) -> list[JsonValue]:
    return [_num(float(v)) for v in values]


def xy_series(name: str, x: np.ndarray, y: np.ndarray, cap: int) -> dict[str, JsonValue]:
    keep = thin_indices(len(x), cap)
    return {"name": name, "x": _floats(x[keep]), "y": _floats(y[keep])}


def bar_series(name: str, values: Sequence[float]) -> dict[str, JsonValue]:
    return {"name": name, "values": _floats(np.asarray(values, dtype="float64"))}


def box_group(name: str, values: np.ndarray) -> dict[str, JsonValue]:
    v = np.asarray(values, dtype="float64")
    q1, median, q3 = np.percentile(v, [25, 50, 75])
    iqr = q3 - q1
    low = v[v >= q1 - 1.5 * iqr].min()
    high = v[v <= q3 + 1.5 * iqr].max()
    return {
        "name": name,
        "n": len(v),
        "low": _num(float(low)),
        "q1": _num(float(q1)),
        "median": _num(float(median)),
        "q3": _num(float(q3)),
        "high": _num(float(high)),
    }


def panel(
    kind: Literal["line", "scatter", "bar", "box"],
    *,
    x_label: str,
    y_label: str,
    title: str | None = None,
    categories: Sequence[str] = (),
    series: Sequence[dict[str, JsonValue]] = (),
    groups: Sequence[dict[str, JsonValue]] = (),
    hline: float | None = None,
) -> dict[str, JsonValue]:
    return {
        "kind": kind,
        "title": title,
        "x_label": x_label,
        "y_label": y_label,
        "categories": list(categories),
        "series": list(series),
        "groups": list(groups),
        "hline": hline,
    }


def chart(
    title: str, panels: Sequence[dict[str, JsonValue]], note: str | None = None
) -> dict[str, JsonValue]:
    return {"version": 1, "title": title, "note": note, "panels": list(panels)}


def sample_note(shown: int, total: int) -> str | None:
    if shown == total:
        return None
    return f"showing {shown} of {total} points"
