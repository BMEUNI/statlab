"""Tests for statlab_contracts.vocabulary: B1-B10 per plan/03_data_contract.md §4.2."""

from __future__ import annotations

from typing import get_args

from statlab_contracts.vocabulary import (
    METRICS_FOR_TASK,
    TASK_FOR_TARGET_TYPE,
    ClassificationMetric,
    Metric,
    RegressionMetric,
    TargetType,
    Task,
)


def test_task_for_target_type_exhaustive() -> None:
    """B1: keys == set(get_args(TargetType))."""
    assert set(TASK_FOR_TARGET_TYPE.keys()) == set(get_args(TargetType))


def test_task_for_target_type_no_extra_keys() -> None:
    """B2: equality, not just containment."""
    assert TASK_FOR_TARGET_TYPE.keys() == set(get_args(TargetType))
    assert len(TASK_FOR_TARGET_TYPE) == len(get_args(TargetType))


def test_metrics_for_task_exhaustive() -> None:
    """B3: METRICS_FOR_TASK exhaustive over Task, via get_args."""
    assert set(METRICS_FOR_TASK.keys()) == set(get_args(Task))


def test_classification_and_regression_metric_sets_disjoint() -> None:
    """B4."""
    assert METRICS_FOR_TASK["classification"].isdisjoint(METRICS_FOR_TASK["regression"])


def test_metric_equals_union_of_both_sets() -> None:
    """B5: Metric == union of both, via get_args.

    `Metric = ClassificationMetric | RegressionMetric` is a union of two Literal aliases, so
    `get_args(Metric)` returns those two aliases, not a flattened tuple of string values.
    Flatten one level further to compare against the value sets.
    """
    flattened = {value for branch in get_args(Metric) for value in get_args(branch)}
    assert flattened == METRICS_FOR_TASK["classification"] | METRICS_FOR_TASK["regression"]


def test_every_classification_metric_maps_to_classification() -> None:
    """B6."""
    for metric in get_args(ClassificationMetric):
        assert metric in METRICS_FOR_TASK["classification"]


def test_every_regression_metric_maps_to_regression() -> None:
    """B7."""
    for metric in get_args(RegressionMetric):
        assert metric in METRICS_FOR_TASK["regression"]


def test_binary_and_multiclass_map_to_classification() -> None:
    """B8."""
    assert TASK_FOR_TARGET_TYPE["binary"] == "classification"
    assert TASK_FOR_TARGET_TYPE["multiclass"] == "classification"


def test_continuous_and_count_map_to_regression() -> None:
    """B9."""
    assert TASK_FOR_TARGET_TYPE["continuous"] == "regression"
    assert TASK_FOR_TARGET_TYPE["count"] == "regression"


def test_vocabularies_contain_no_duplicates() -> None:
    """B10: each get_args tuple has unique members."""
    for vocabulary in (
        TargetType,
        Task,
        ClassificationMetric,
        RegressionMetric,
    ):
        values = get_args(vocabulary)
        assert len(values) == len(set(values)), f"{vocabulary!r} has duplicate members"


def test_split_role_members() -> None:
    """R4: `SplitRole` is the closed vocabulary the D32 lineage lock keys on. Pin its exact
    members so a tool cannot invent a fourth split role, and so the DDL CHECK drift test has a
    fixed truth to derive against."""
    from statlab_contracts.vocabulary import SplitRole

    assert set(get_args(SplitRole)) == {"train", "valid", "test"}
