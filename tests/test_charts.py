"""Chart spec v1 helpers (chunk 55 stage 55a): index thinning, box statistics, series and note."""

from __future__ import annotations

import numpy as np
from statlab_tools.charts import box_group, chart, panel, sample_note, thin_indices, xy_series


def test_thin_indices_keeps_every_index_under_cap() -> None:
    np.testing.assert_array_equal(thin_indices(10, 20), np.arange(10))


def test_thin_indices_spreads_evenly_over_cap() -> None:
    assert thin_indices(1000, 5).tolist() == [0, 250, 500, 749, 999]


def test_box_group_whiskers_stop_at_furthest_point_within_1_5_iqr() -> None:
    values = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 100.0])
    assert box_group("g", values) == {
        "name": "g",
        "n": 10,
        "low": 1.0,
        "q1": 3.25,
        "median": 5.5,
        "q3": 7.75,
        "high": 9.0,
    }


def test_chart_maps_nan_to_null_and_fills_unused_lists() -> None:
    spec = chart(
        "t",
        [
            panel(
                "line",
                x_label="a",
                y_label="b",
                series=[xy_series("s", np.array([0.0, 1.0]), np.array([1.0, float("nan")]), 500)],
            )
        ],
    )
    assert spec == {
        "version": 1,
        "title": "t",
        "note": None,
        "panels": [
            {
                "kind": "line",
                "title": None,
                "x_label": "a",
                "y_label": "b",
                "categories": [],
                "series": [{"name": "s", "x": [0.0, 1.0], "y": [1.0, None]}],
                "groups": [],
                "hline": None,
            }
        ],
    }


def test_sample_note_is_none_when_nothing_was_thinned() -> None:
    assert sample_note(5, 5) is None


def test_sample_note_names_shown_and_total_points() -> None:
    assert sample_note(2000, 2500) == "showing 2000 of 2500 points"
