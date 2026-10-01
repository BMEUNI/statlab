"""The champion phase, `rank_models` (`PLAN.md` §6.3's `champion` phase, this chunk's
`14_tools_evaluation.md` §3.5). Pulled forward from chunk 19 so the chunk 16 skeleton gate is
self-contained; chunk 19 adds `select_champion` and the reports.

`rank_models` does not re-score (D-g): it reads a `compare_models` table and the contract
snapshot's `constraints.interpretability_required` and `objective.success_threshold`, applies the
contract, and carries D-f's distinguishability flag for the top two **eligible** candidates. The
`selection_note` states the winner's-curse warning D33a requires; node 12 later adds the wave count
W, which the tool cannot know.
"""

from __future__ import annotations

from typing import Annotated, cast

from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import Metric

from statlab_tools.artifacts import load_json, save_json
from statlab_tools.registry import greater_is_better
from statlab_tools.tool import ToolParamError, tool

_CHAMPION_PHASES = ("champion_selection",)


def _snapshot(paths: RunPaths) -> DataContract | None:
    """The contract snapshot, or `None` when none is present (tests without one)."""
    if paths.contract_snapshot_json.exists():
        return DataContract.model_validate_json(paths.contract_snapshot_json.read_text())
    return None


def _load_comparison(
    paths: RunPaths, comparison: ArtifactRef
) -> tuple[str, list[dict[str, JsonValue]]]:
    """A `compare_models` table: `(metric, ranking rows)`, or `tool_param` if it is not one."""
    loaded = load_json(paths.artifact(comparison, "table"))
    if not isinstance(loaded, dict) or "metric" not in loaded or "ranking" not in loaded:
        raise ToolParamError(
            "comparison is not a compare_models table",
            field_errors={"comparison": "not a compare_models table"},
        )
    ranking = cast("list[dict[str, JsonValue]]", loaded["ranking"])
    return str(loaded["metric"]), ranking


def _meets_threshold(value: float, threshold: float, metric: str) -> bool:
    """Direction-aware success check: greater-is-better metrics clear a floor, others clear a
    ceiling."""
    if greater_is_better(cast(Metric, metric)):
        return value >= threshold
    return value <= threshold


@tool(phase="champion", allow_in=_CHAMPION_PHASES)
def rank_models(
    paths: RunPaths,
    comparison: Annotated[
        ArtifactRef,
        Field(description="A compare_models table artifact to rank under the contract."),
    ],
    estimators: Annotated[
        dict[str, str],
        Field(description="Candidate label -> its registry estimator name."),
    ],
) -> ToolSuccess:
    """Apply the contract to a comparison ranking, flagging eligibility and success (D-g).

    `estimators` maps every candidate label in `comparison` to its registry name; a label absent
    from the map, or a name absent from the registry, is `tool_param`. Each candidate is flagged
    `eligible` (false for a non-interpretable estimator when
    `constraints.interpretability_required`), and `meets_success_threshold` (direction-aware
    against `objective.success_threshold`, only when the ranking metric is the primary metric or
    the primary metric is unset -- a threshold on one metric says nothing about another).

    `top_eligible` is the highest-ranked eligible candidate, or `null` with a warning when none is
    eligible (not an error; node 12 decides). `top_two_eligible_distinguishable` carries D-f's
    flag for the top two eligible, read off their CIs. The `selection_note` states the winner's
    curse (D33a) naming the candidate count N; node 12 adds the wave count W. Writes a `table`
    artifact.
    """
    from statlab_tools.registry import estimators as registry_estimators

    metric, ranking = _load_comparison(paths, comparison)
    labels = [str(row["candidate"]) for row in ranking]
    reg = registry_estimators()

    missing_labels = [label for label in labels if label not in estimators]
    if missing_labels:
        raise ToolParamError(
            f"estimators is missing registry names for candidates {missing_labels!r}",
            field_errors={"estimators": f"no estimator name for: {missing_labels!r}"},
        )
    unknown_names = sorted({name for name in estimators.values() if name not in reg})
    if unknown_names:
        raise ToolParamError(
            f"estimator names {unknown_names!r} are not in the registry; valid: {sorted(reg)!r}",
            field_errors={"estimators": f"unknown estimator names: {unknown_names!r}"},
        )

    snapshot = _snapshot(paths)
    interpretability_required = (
        snapshot.constraints.interpretability_required if snapshot is not None else False
    )
    success_threshold = snapshot.objective.success_threshold if snapshot is not None else None
    primary_metric = snapshot.objective.primary_metric if snapshot is not None else None
    threshold_applies = primary_metric is None or primary_metric == metric

    out_rows: list[JsonValue] = []
    eligible_rows: list[dict[str, JsonValue]] = []
    for row in ranking:
        label = str(row["candidate"])
        name = estimators[label]
        entry = reg[name]
        eligible = True
        ineligible_reason: str | None = None
        if interpretability_required and not entry.interpretable:
            eligible = False
            ineligible_reason = "interpretability_required but estimator is not interpretable"
        value = cast(float, row["value"])
        meets: bool | None = None
        if success_threshold is not None and threshold_applies:
            meets = _meets_threshold(value, success_threshold, metric)
        out_row: dict[str, JsonValue] = {
            "rank": row["rank"],
            "candidate": label,
            "estimator": name,
            "value": row["value"],
            "eligible": eligible,
            "ineligible_reason": ineligible_reason,
            "meets_success_threshold": meets,
        }
        out_rows.append(out_row)
        if eligible:
            eligible_rows.append({**row, "estimator": name})

    warnings: list[str] = []
    top_eligible: str | None
    if eligible_rows:
        top_eligible = str(eligible_rows[0]["candidate"])
    else:
        top_eligible = None
        warnings.append("no candidate is eligible under the contract; top_eligible is null")

    top_two_distinguishable: bool | None = None
    if len(eligible_rows) >= 2:
        a, b = eligible_rows[0], eligible_rows[1]
        top_two_distinguishable = _distinguishable(a, b)

    n = len(labels)
    selection_note = (
        f"the top validation score is a selection score over {n} candidates; "
        f"only the test score estimates performance"
    )
    payload: dict[str, JsonValue] = {"metric": metric, "ranking": out_rows}
    ref = new_artifact_ref()
    meta = save_json(paths.artifact(ref, "table"), payload, ref=ref, kind="table", label="ranking")
    summary: dict[str, JsonValue] = {
        "ranking": out_rows,
        "top_eligible": top_eligible,
        "top_two_eligible_distinguishable": top_two_distinguishable,
        "selection_note": selection_note,
        "n_candidates": n,
    }
    return ToolSuccess(summary=summary, artifacts=(meta,), warnings=tuple(warnings))


def _distinguishable(a: dict[str, JsonValue], b: dict[str, JsonValue]) -> bool:
    """Whether two ranking rows' CIs do NOT overlap (D-f). Overlap means not distinguishable."""
    a_lo, a_hi = cast(float, a["ci_low"]), cast(float, a["ci_high"])
    b_lo, b_hi = cast(float, b["ci_low"]), cast(float, b["ci_high"])
    overlap = a_lo <= b_hi and b_lo <= a_hi
    return not overlap
