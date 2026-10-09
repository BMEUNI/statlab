"""Skill headings, real served registry and immutable byte snapshots."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel, SecretStr
from statlab.config import Settings
from statlab.domains.base import NodeSpec
from statlab.errors import ConfigError
from statlab.llm.skills import load_run_skill, load_skill, snapshot_skill
from statlab.mcp_client import McpClient, ToolSpec
from statlab_tools import tools as served_tools
from statlab_tools.tool import server

_NAMES = (
    "intake",
    "problem_definition",
    "data_profiling",
    "data_quality",
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


def _spine(names: tuple[str, ...]) -> tuple[NodeSpec, ...]:
    return tuple(NodeSpec(name, "SHARED", False, BaseModel, (), "never") for name in names)


@pytest_asyncio.fixture
async def registry(migrated_db: sqlite3.Connection, tmp_path: Path) -> dict[str, ToolSpec]:
    assert served_tools is not None

    class ServedSession:
        async def list_tools(self) -> object:
            return type("Listing", (), {"tools": await server.list_tools()})()

    settings = Settings(
        mcp_token=SecretStr("test-token"), data_dir=tmp_path, db_path=tmp_path / "db"
    )
    async with McpClient(settings, migrated_db, session=ServedSession()) as client:
        return client.tools.copy()


def _text(name: str = "alpha", tools: str = "", model: str | None = None) -> str:
    text = f"## {name}\n\n"
    for field in ("Purpose", "Heuristics", "Tools", "Outputs", "Pitfalls"):
        text += f"### {field}\n{tools if field == 'Tools' else field + ' content'}\n\n"
    if model is not None:
        text += f"### Model\n{model}\n"
    return text


def test_modelling_policy(registry: dict[str, ToolSpec]) -> None:
    policy = load_skill(Path("skills/modelling.md"), spine=_spine(_NAMES), tools=registry)
    assert tuple(skill.node for skill in policy.nodes) == _NAMES
    for skill in policy.nodes:
        assert skill.heuristics and skill.pitfalls
        assert "TODO:" not in skill.heuristics and "TODO:" not in skill.pitfalls
        assert skill.purpose and skill.outputs
        assert skill.allowed <= registry.keys()
    assert "split_temporal" not in policy.original_bytes.decode()
    assert "split_grouped" not in policy.original_bytes.decode()
    with pytest.raises(ConfigError, match="absent"):
        policy.node("absent")


def test_wildcard_model_and_isolation(tmp_path: Path, registry: dict[str, ToolSpec]) -> None:
    path = tmp_path / "skill.md"
    path.write_text(
        "Shared preamble\n"
        + _text(tools="describe_artifact", model="reasoning")
        + _text("beta", model="vendor/model")
    )
    policy = load_skill(path, spine=_spine(("alpha", "beta")), tools=registry)
    assert policy.node("alpha").allowed == frozenset({"describe_artifact"})
    assert policy.node("alpha").model == "reasoning"
    assert policy.node("beta").model == "vendor/model"
    assert "beta" not in policy.node("alpha").section
    assert policy.preamble == "Shared preamble\n"


@pytest.mark.parametrize(
    "text, message",
    [
        (_text() + _text(), "duplicate skill node"),
        (_text("wrong"), "domain spine"),
        ("preamble only", "domain spine"),
        (_text().replace("### Purpose", "### Unknown"), "headings"),
        (_text().replace("### Heuristics", "### Purpose"), "duplicate skill field"),
        (_text(tools="phantom"), "unknown tool"),
        (_text(tools="load_csv"), "disallowed tool"),
        (_text(model=""), "malformed"),
        (_text(model="reasoning\nextra"), "malformed"),
        (_text() + "### Unknown\ntext", "headings"),
    ],
)
def test_invalid_sections(
    text: str, message: str, tmp_path: Path, registry: dict[str, ToolSpec]
) -> None:
    path = tmp_path / "skill.md"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_skill(path, spine=_spine(("alpha",)), tools=registry)


def test_snapshots_and_restart(tmp_path: Path, registry: dict[str, ToolSpec]) -> None:
    path = tmp_path / "source.md"
    original = ("Preamble\r\n" + _text().replace("\n", "\r\n")).encode()
    path.write_bytes(original)
    policy = load_run_skill(tmp_path, path, spine=_spine(("alpha",)), tools=registry)
    assert policy.original_bytes == original
    assert policy.sha256 == hashlib.sha256(original).hexdigest()
    assert (tmp_path / "skill.snapshot.md").read_bytes() == original
    assert snapshot_skill(policy, tmp_path) == policy.sha256
    path.write_text(_text(model="default"))
    assert load_run_skill(tmp_path, path, spine=_spine(("alpha",)), tools=registry) == policy
    changed = load_skill(path, spine=_spine(("alpha",)), tools=registry)
    with pytest.raises(ConfigError, match="differs"):
        snapshot_skill(changed, tmp_path)
    assert (tmp_path / "skill.snapshot.md").read_bytes() == original


def test_real_io_errors_and_no_partial_snapshot(
    tmp_path: Path, registry: dict[str, ToolSpec]
) -> None:
    for path in (tmp_path / "missing", tmp_path):
        with pytest.raises(ConfigError, match="read"):
            load_skill(path, spine=_spine(("alpha",)), tools=registry)
    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="read"):
        load_skill(invalid, spine=_spine(("alpha",)), tools=registry)
    path = tmp_path / "source.md"
    path.write_text(_text())
    policy = load_skill(path, spine=_spine(("alpha",)), tools=registry)
    missing_dir = tmp_path / "absent-dir"
    with pytest.raises(ConfigError, match="snapshot"):
        snapshot_skill(policy, missing_dir)
    assert not (missing_dir / "skill.snapshot.md").exists()
    (tmp_path / "skill.snapshot.md").mkdir()
    before = {p.name for p in tmp_path.iterdir()}
    with pytest.raises(ConfigError, match="snapshot"):
        snapshot_skill(policy, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == before


def test_empty_domain_preamble(tmp_path: Path) -> None:
    path = tmp_path / "empty-domain.md"
    path.write_text("Only preamble")
    policy = load_skill(path, spine=(), tools={})
    assert policy.preamble == "Only preamble" and policy.nodes == ()


@pytest.mark.parametrize("same", [True, False])
def test_snapshot_publication_race(
    tmp_path: Path, same: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "source.md"
    path.write_text(_text())
    policy = load_skill(path, spine=_spine(("alpha",)), tools={})

    def publish_first(source: Path, destination: Path) -> None:
        destination.write_bytes(policy.original_bytes if same else b"other run policy")
        raise FileExistsError("already published")

    monkeypatch.setattr("statlab.llm.skills.os.link", publish_first)
    if same:
        assert snapshot_skill(policy, tmp_path) == policy.sha256
    else:
        with pytest.raises(ConfigError, match="differs"):
            snapshot_skill(policy, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"source.md", "skill.snapshot.md"}


def test_snapshot_publication_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "source.md"
    path.write_text(_text())
    policy = load_skill(path, spine=_spine(("alpha",)), tools={})

    def denied(source: Path, destination: Path) -> None:
        raise PermissionError("private filesystem detail")

    monkeypatch.setattr("statlab.llm.skills.os.link", denied)
    with pytest.raises(ConfigError, match="snapshot") as caught:
        snapshot_skill(policy, tmp_path)
    assert "private filesystem detail" not in str(caught.value)
    assert {p.name for p in tmp_path.iterdir()} == {"source.md"}
