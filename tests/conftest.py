"""Shared fixtures. Every test starts from a known-empty environment."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import (
    CandidateSet,
    CandidateSpec,
    EstimatorSpec,
    FeatureSpec,
    PreprocessingSpec,
)
from statlab_contracts.contract import DataContract
from statlab_contracts.vocabulary import ClassWeight, ParamValue, VariationDimension

_STATLAB_VARS = (
    "STATLAB_MCP_TOKEN",
    "STATLAB_MCP_URL",
    "STATLAB_DATA_DIR",
    "STATLAB_DB_PATH",
    "STATLAB_API_HOST",
    "STATLAB_API_PORT",
    "STATLAB_ALLOW_NONLOCAL_BIND",
    "STATLAB_MAX_UPLOAD_MB",
    "STATLAB_KEEP_INTERMEDIATES",
    "STATLAB_MAX_CONCURRENT_RUNS",
    "STATLAB_MCP_HOST",
    "STATLAB_MCP_PORT",
    "STATLAB_FIT_PERMITS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every STATLAB_* var so tests cannot be influenced by the real environment."""
    for var in _STATLAB_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Drop the lru_cache singletons before and after each test."""
    from statlab.config import get_settings
    from statlab_tools.settings import get_tool_settings

    get_settings.cache_clear()
    get_tool_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_tool_settings.cache_clear()


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    """An env file with nothing in it, so `.env` on disk cannot leak into a test."""
    path = tmp_path / "empty.env"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Minimum viable valid configuration pointing at a temporary data directory."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "test-token-not-a-real-secret")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("STATLAB_DB_PATH", str(tmp_path / "statlab.db"))
    return tmp_path


@pytest.fixture
def ref() -> ArtifactRef:
    """A syntactically valid, properly typed artifact ref for tests that need one.

    Shared here rather than per test file: `ArtifactRef` is a `NewType`, so
    `init_typed = true` (chunk 02 §6) makes mypy enforce it at every `ArtifactMeta`/
    `NodeResult` construction site, and every chunk from 06 onward constructs artifacts.
    """
    return ArtifactRef("a_0123456789abcdef")


_CONTRACT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "contracts"


@pytest.fixture
def minimal_contract() -> DataContract:
    """The D8 minimum: target.column and nothing else. Loaded from disk so the same JSON
    exercises the real parse path and doubles as an example file."""
    text = (_CONTRACT_FIXTURES_DIR / "minimal.json").read_text(encoding="utf-8")
    return DataContract.model_validate_json(text)


@pytest.fixture
def full_contract() -> DataContract:
    """Every field populated with a mutually consistent set of values, satisfying every rule
    in chunk 03 §3.4-3.5. Used as the base for cross-group tests: copy the underlying dict with
    one field changed via `model_validate(full_dict | {...})` so a failure names exactly one
    deviation."""
    text = (_CONTRACT_FIXTURES_DIR / "full.json").read_text(encoding="utf-8")
    return DataContract.model_validate_json(text)


@pytest.fixture
def full_contract_dict() -> dict[str, JsonValue]:
    """The raw parsed `full.json`, for tests that construct a one-field deviation via
    `DataContract.model_validate({**full_contract_dict, ...})`. Kept separate from
    `full_contract` (a `DataContract` instance) because negative tests need a plain dict to
    mutate, not a frozen model."""
    text = (_CONTRACT_FIXTURES_DIR / "full.json").read_text(encoding="utf-8")
    result: dict[str, JsonValue] = json.loads(text)
    return result


_CANDIDATE_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "candidates"


def make_candidate(
    *,
    label: str = "candidate",
    rationale: str = "a placeholder rationale long enough to satisfy the length floor",
    is_baseline: bool = False,
    preprocessing: PreprocessingSpec | None = None,
    features: FeatureSpec | None = None,
    estimator_name: str = "logistic_regression",
    estimator_params: dict[str, ParamValue] | None = None,
    class_weight: ClassWeight = "none",
    calibrate: bool = False,
    varies_from_baseline: tuple[VariationDimension, ...] = (),
) -> CandidateSpec:
    """Typed keyword-parameter helper, not `**overrides: object` -- that shape forces a
    `**dict` construction and an `arg-type` suppression under `init_typed = true` (see
    plan/00_index.md's test-fixture-pattern note; `test_domains_base.py`'s surviving
    `**overrides` helper is not the pattern to copy here)."""
    return CandidateSpec(
        label=label,
        rationale=rationale,
        is_baseline=is_baseline,
        preprocessing=preprocessing if preprocessing is not None else PreprocessingSpec(),
        features=features if features is not None else FeatureSpec(),
        estimator=EstimatorSpec(
            name=estimator_name,
            params=estimator_params if estimator_params is not None else {},
            class_weight=class_weight,
            calibrate=calibrate,
        ),
        varies_from_baseline=varies_from_baseline,
    )


@pytest.fixture
def baseline_candidate() -> CandidateSpec:
    """A cheap interpretable baseline: logistic_regression, one-hot, no engineering."""
    return make_candidate(
        label="baseline",
        rationale="cheap interpretable baseline: reference point for whether complexity pays",
        is_baseline=True,
    )


@pytest.fixture
def shortlist() -> CandidateSet:
    """Loaded from tests/fixtures/candidates/shortlist.json.

    Five candidates mirroring PLAN.md §4.2's worked example on an imbalanced binary target:
    baseline logreg; logreg + balanced class weight; random forest; hist GB + target encoding;
    logreg + an age x income interaction. Must satisfy all seven set rules, and each
    `varies_from_baseline` must match its actual diff -- if it does not, the fixture is wrong.
    """
    text = (_CANDIDATE_FIXTURES_DIR / "shortlist.json").read_text(encoding="utf-8")
    return CandidateSet.model_validate_json(text)


@pytest.fixture
def shortlist_dict() -> dict[str, JsonValue]:
    """The raw parsed `shortlist.json`, for tests that construct a one-field deviation."""
    text = (_CANDIDATE_FIXTURES_DIR / "shortlist.json").read_text(encoding="utf-8")
    result: dict[str, JsonValue] = json.loads(text)
    return result
