"""Run-folder layout: PLAN.md §10.1. Pure `pathlib`, stdlib only — both processes resolve the
same paths (D13), so this module is the contract, not a copy of one.

`data_dir` arrives already absolute (`Settings._absolute`). Nothing here re-resolves it
again — a second resolution pass would silently follow a symlink the caller did not ask to
follow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, get_args

from statlab_contracts.artifacts import ArtifactKind, ArtifactRef

EXTENSION_FOR_KIND: Final[dict[ArtifactKind, str]] = {
    "dataframe": ".parquet",
    "predictions": ".parquet",
    "transformer": ".joblib",
    "model": ".joblib",
    "inferential_fit": ".joblib",
    "curve": ".json",
    "table": ".json",
    "plot": ".png",
    "report": ".md",
}
"""File extension per `ArtifactKind`. Exhaustive over `get_args(ArtifactKind)` — a test derives
that exhaustiveness rather than hand-listing the kinds a second time. A new kind with no entry
here must fail the suite, not produce a file with no suffix."""

assert set(EXTENSION_FOR_KIND) == set(get_args(ArtifactKind))  # noqa: S101  # exhaustiveness guard evaluated at import time, not a runtime test double; mirrors chunk 04's DIMENSION_FOR_FIELD pattern


@dataclass(frozen=True, slots=True)
class RunPaths:
    """Paths under one run: `<data_dir>/<slug>/runs/<run_id>/...`."""

    data_dir: Path
    slug: str
    run_id: str

    @property
    def root(self) -> Path:
        return self.data_dir / self.slug / "runs" / self.run_id

    @property
    def state_json(self) -> Path:
        return self.root / "state.json"

    @property
    def events_jsonl(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def contract_snapshot_json(self) -> Path:
        return self.root / "contract.snapshot.json"

    @property
    def skill_snapshot_md(self) -> Path:
        return self.root / "skill.snapshot.md"

    @property
    def comparison_json(self) -> Path:
        return self.root / "comparison.json"

    @property
    def manifest_json(self) -> Path:
        return self.root / "manifest.json"

    @property
    def pipeline_py(self) -> Path:
        return self.root / "pipeline.py"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def nodes(self) -> Path:
        return self.root / "nodes"

    @property
    def candidates(self) -> Path:
        return self.root / "candidates"

    @property
    def report(self) -> Path:
        return self.root / "report"

    def node(self, seq: int, node_name: str) -> Path:
        """`<root>/nodes/NN_<node_name>`, zero-padded to two digits so lexical order is spine
        order — unpadded, `10_model_evaluation` sorts before `09_model_training`."""
        return self.nodes / f"{seq:02d}_{node_name}"

    def candidate(self, candidate_id: str) -> Path:
        return self.candidates / candidate_id

    def candidate_node(self, candidate_id: str, seq: int, node_name: str) -> Path:
        return self.candidate(candidate_id) / "nodes" / f"{seq:02d}_{node_name}"

    def artifact(self, ref: ArtifactRef, kind: ArtifactKind) -> Path:
        return self.artifacts / f"{ref}{EXTENSION_FOR_KIND[kind]}"


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Paths under one project: `<data_dir>/<slug>/...`."""

    data_dir: Path
    slug: str

    @property
    def root(self) -> Path:
        return self.data_dir / self.slug

    @property
    def project_json(self) -> Path:
        return self.root / "project.json"

    @property
    def datasets(self) -> Path:
        return self.root / "datasets"

    @property
    def contracts(self) -> Path:
        return self.root / "contracts"

    @property
    def memory_json(self) -> Path:
        return self.root / "memory.json"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    def run(self, run_id: str) -> RunPaths:
        return RunPaths(data_dir=self.data_dir, slug=self.slug, run_id=run_id)
