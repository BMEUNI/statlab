"""Chunk 06 §4.2: layout dataclasses. L1-L8, plus per-property path assertions.

Hand-written expected paths are correct here -- this test file IS the specification of the
layout (§10.1), the one deliberate exception to "derive, don't hand-list".
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from statlab_contracts.artifacts import ArtifactKind, ArtifactRef
from statlab_contracts.layout import EXTENSION_FOR_KIND, ProjectPaths, RunPaths

_DATA_DIR = Path("/data")
_SLUG = "proj"
_RUN_ID = "r_0000000000000000"


@pytest.fixture
def project_paths() -> ProjectPaths:
    return ProjectPaths(data_dir=_DATA_DIR, slug=_SLUG)


@pytest.fixture
def run_paths() -> RunPaths:
    return RunPaths(data_dir=_DATA_DIR, slug=_SLUG, run_id=_RUN_ID)


# --- ProjectPaths ----------------------------------------------------------------------------


def test_project_root(project_paths: ProjectPaths) -> None:
    assert project_paths.root == Path("/data/proj")


def test_project_json(project_paths: ProjectPaths) -> None:
    assert project_paths.project_json == Path("/data/proj/project.json")


def test_project_datasets(project_paths: ProjectPaths) -> None:
    assert project_paths.datasets == Path("/data/proj/datasets")


def test_project_contracts(project_paths: ProjectPaths) -> None:
    assert project_paths.contracts == Path("/data/proj/contracts")


def test_project_memory_json(project_paths: ProjectPaths) -> None:
    assert project_paths.memory_json == Path("/data/proj/memory.json")


def test_project_runs(project_paths: ProjectPaths) -> None:
    assert project_paths.runs == Path("/data/proj/runs")


def test_project_run_returns_run_paths(project_paths: ProjectPaths) -> None:
    rp = project_paths.run(_RUN_ID)
    assert isinstance(rp, RunPaths)
    assert rp.data_dir == _DATA_DIR
    assert rp.slug == _SLUG
    assert rp.run_id == _RUN_ID


def test_project_paths_frozen(project_paths: ProjectPaths) -> None:
    with pytest.raises(AttributeError):
        project_paths.slug = "other"  # type: ignore[misc]  # deliberately mutating a frozen dataclass to prove it raises


# --- RunPaths ----------------------------------------------------------------------------------


def test_run_root(run_paths: RunPaths) -> None:
    assert run_paths.root == Path("/data/proj/runs/r_0000000000000000")


def test_run_state_json(run_paths: RunPaths) -> None:
    assert run_paths.state_json == run_paths.root / "state.json"


def test_run_events_jsonl(run_paths: RunPaths) -> None:
    assert run_paths.events_jsonl == run_paths.root / "events.jsonl"


def test_run_contract_snapshot_json(run_paths: RunPaths) -> None:
    assert run_paths.contract_snapshot_json == run_paths.root / "contract.snapshot.json"


def test_run_skill_snapshot_md(run_paths: RunPaths) -> None:
    assert run_paths.skill_snapshot_md == run_paths.root / "skill.snapshot.md"


def test_run_comparison_json(run_paths: RunPaths) -> None:
    assert run_paths.comparison_json == run_paths.root / "comparison.json"


def test_run_manifest_json(run_paths: RunPaths) -> None:
    assert run_paths.manifest_json == run_paths.root / "manifest.json"


def test_run_pipeline_py(run_paths: RunPaths) -> None:
    assert run_paths.pipeline_py == run_paths.root / "pipeline.py"


def test_run_artifacts_dir(run_paths: RunPaths) -> None:
    assert run_paths.artifacts == run_paths.root / "artifacts"


def test_run_nodes_dir(run_paths: RunPaths) -> None:
    assert run_paths.nodes == run_paths.root / "nodes"


def test_run_candidates_dir(run_paths: RunPaths) -> None:
    assert run_paths.candidates == run_paths.root / "candidates"


def test_run_report_dir(run_paths: RunPaths) -> None:
    assert run_paths.report == run_paths.root / "report"


def test_l1_node_dir_is_zero_padded(run_paths: RunPaths) -> None:
    """L1."""
    assert run_paths.node(9, "model_training") == run_paths.nodes / "09_model_training"


def test_l2_lexical_order_is_spine_order(run_paths: RunPaths) -> None:
    """L2: catches the un-padded bug that puts `10_` before `09_`."""
    names = [
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
    ]
    numbered = [run_paths.node(i + 1, name).name for i, name in enumerate(names)]
    assert sorted(numbered) == numbered


def test_run_candidate(run_paths: RunPaths) -> None:
    assert run_paths.candidate("c_01") == run_paths.candidates / "c_01"


def test_run_candidate_node(run_paths: RunPaths) -> None:
    result = run_paths.candidate_node("c_01", 7, "preprocessing")
    assert result == run_paths.candidates / "c_01" / "nodes" / "07_preprocessing"


def test_run_candidate_node_for_different_candidates_differ(run_paths: RunPaths) -> None:
    a = run_paths.candidate_node("c_01", 7, "preprocessing")
    b = run_paths.candidate_node("c_02", 7, "preprocessing")
    assert a != b
    assert a.parent.parent.name == "c_01"
    assert b.parent.parent.name == "c_02"


def test_node_13_is_the_last_zero_padded_two_digit_boundary(run_paths: RunPaths) -> None:
    """13 is the highest spine node number (PLAN.md §4); still two digits, no special-casing
    needed at the boundary."""
    result = run_paths.node(13, "artifact_persistence")
    assert result.name == "13_artifact_persistence"


def test_l5_artifact_composes_ref_and_extension(run_paths: RunPaths) -> None:
    """L5."""
    ref = ArtifactRef("a_0000000000000001")
    result = run_paths.artifact(ref, "dataframe")
    assert result == run_paths.artifacts / "a_0000000000000001.parquet"


def test_run_paths_frozen(run_paths: RunPaths) -> None:
    with pytest.raises(AttributeError):
        run_paths.run_id = "other"  # type: ignore[misc]  # deliberately mutating a frozen dataclass to prove it raises


# --- EXTENSION_FOR_KIND ------------------------------------------------------------------------


def test_l3_extension_for_kind_exhaustive() -> None:
    """L3: derived from `get_args(ArtifactKind)`, not hand-listed."""
    assert set(EXTENSION_FOR_KIND) == set(get_args(ArtifactKind))


@pytest.mark.parametrize("kind", get_args(ArtifactKind))
def test_l4_every_extension_starts_with_dot(kind: ArtifactKind) -> None:
    """L4."""
    assert EXTENSION_FOR_KIND[kind].startswith(".")


@pytest.mark.parametrize("kind", get_args(ArtifactKind))
def test_artifact_path_uses_correct_extension_per_kind(
    run_paths: RunPaths, kind: ArtifactKind
) -> None:
    ref = ArtifactRef("a_0000000000000001")
    result = run_paths.artifact(ref, kind)
    assert result.suffix == EXTENSION_FOR_KIND[kind]


def test_extension_for_kind_has_no_duplicate_values_beyond_expected_sharing() -> None:
    """`.parquet` is shared by `dataframe`/`predictions`, `.joblib` by three model-like kinds,
    `.json` by `curve`/`table`. Pinning the exact grouping catches an accidental merge or
    split of these sets."""
    by_extension: dict[str, set[str]] = {}
    for kind, ext in EXTENSION_FOR_KIND.items():
        by_extension.setdefault(ext, set()).add(kind)
    assert by_extension[".parquet"] == {"dataframe", "predictions"}
    assert by_extension[".joblib"] == {"transformer", "model", "inferential_fit"}
    assert by_extension[".json"] == {"curve", "table"}
    assert by_extension[".png"] == {"plot"}
    assert by_extension[".md"] == {"report"}


# --- module-level boundary checks ---------------------------------------------------------------


def test_l6_no_literal_projects_string_in_module() -> None:
    """L6: `"projects"` is `STATLAB_DATA_DIR`'s default value, not a path component."""
    import statlab_contracts.layout as layout_mod

    source = Path(layout_mod.__file__).read_text(encoding="utf-8")
    assert '"projects"' not in source
    assert "'projects'" not in source


def test_l7_no_resolve_call_in_module() -> None:
    """L7: `data_dir` arrives absolute; a second resolve would silently follow symlinks."""
    import statlab_contracts.layout as layout_mod

    source = Path(layout_mod.__file__).read_text(encoding="utf-8")
    assert ".resolve(" not in source


def test_l8_layout_imports_only_stdlib_and_statlab_contracts() -> None:
    """L8: no pydantic needed, no pandas possible."""
    import sys

    import statlab_contracts.layout as layout_mod

    stdlib_paths = {p for p in sys.path if "site-packages" not in p}
    for name, value in vars(layout_mod).items():
        if name.startswith("__"):
            continue
        module_name = getattr(value, "__module__", None)
        if module_name is None:
            continue
        if module_name == layout_mod.__name__ or module_name.startswith("statlab_contracts."):
            continue
        assert not module_name.startswith("statlab."), f"{name} resolves to {module_name}"
        assert not module_name.startswith("statlab_tools"), f"{name} resolves to {module_name}"
        assert "pandas" not in module_name, f"{name} resolves to {module_name}"
        top_level = module_name.split(".", 1)[0]
        module_obj = sys.modules.get(top_level)
        is_stdlib = module_obj is not None and (
            getattr(module_obj, "__file__", None) is None
            or any(
                str(Path(module_obj.__file__).parent) == p  # type: ignore[arg-type]  # __file__ checked not-None above
                for p in stdlib_paths
            )
            or top_level in sys.stdlib_module_names
        )
        assert is_stdlib, f"{name} resolves to third-party module {module_name!r}"
