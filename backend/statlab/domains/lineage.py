"""Pure domain rules for the declared tabular-supervised spine (D27, D32)."""

from __future__ import annotations

from collections.abc import Mapping

from statlab.errors import ArtifactLockedError, EngineError

SPINE_TOOLS_NODES: tuple[str, ...] = (
    "intake",
    "problem_definition",
    "data_profiling",
    "data_splitting",
    "experiment_design",
    "inferential_analysis",
    "preprocessing",
    "feature_engineering",
    "model_training",
    "model_evaluation",
    "model_interpretation",
    "champion_selection",
    "artifact_persistence",
)
_CHAMPION_INDEX = SPINE_TOOLS_NODES.index("champion_selection")


def check_lineage(
    node: str,
    tool: str,
    tainted_params: Mapping[str, str],
    test_scored: bool,
    *,
    transform_only: bool,
    phase: str,
    champion_selected: bool = False,
) -> None:
    """Refuse test-derived inputs that could influence fitting or champion choice."""
    if node not in SPINE_TOOLS_NODES:
        raise EngineError(f"unknown spine node {node!r}")
    if not tainted_params:
        return
    ref = next(iter(tainted_params.values()))
    if "train" in tainted_params:
        raise ArtifactLockedError(ref, node)
    if tool.startswith("fit_"):
        raise ArtifactLockedError(ref, node)
    if tool.startswith("score_") and test_scored:
        raise ArtifactLockedError(ref, node)
    index = SPINE_TOOLS_NODES.index(node)
    if index < _CHAMPION_INDEX:
        if tool != "verify_split":
            raise ArtifactLockedError(ref, node)
        return
    if index > _CHAMPION_INDEX:
        return
    if not champion_selected:
        raise ArtifactLockedError(ref, node)
    reporting_after_score = phase == "reporting" and test_scored
    if tool == "select_champion" or not (
        transform_only or tool.startswith("score_") or reporting_after_score
    ):
        raise ArtifactLockedError(ref, node)
