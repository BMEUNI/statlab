"""Validated, immutable skill policy and per-run byte snapshots."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from statlab.domains.base import NodeSpec
from statlab.errors import ConfigError
from statlab.mcp_client import ToolSpec

_REQUIRED = frozenset({"Purpose", "Heuristics", "Tools", "Outputs", "Pitfalls"})


@dataclass(frozen=True, slots=True)
class NodeSkill:
    node: str
    section: str
    purpose: str
    heuristics: str
    tools: str
    outputs: str
    pitfalls: str
    allowed: frozenset[str]
    model: str | None = None


@dataclass(frozen=True, slots=True)
class SkillPolicy:
    preamble: str
    nodes: tuple[NodeSkill, ...]
    original_bytes: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.original_bytes).hexdigest()

    def node(self, name: str) -> NodeSkill:
        for skill in self.nodes:
            if skill.node == name:
                return skill
        raise ConfigError("skill node is absent")


def load_skill(
    path: Path, *, spine: tuple[NodeSpec, ...], tools: Mapping[str, ToolSpec]
) -> SkillPolicy:
    try:
        source = path.read_bytes()
        text = source.decode("utf-8")
    except OSError, UnicodeError:
        raise ConfigError("could not read skill policy") from None
    headings = tuple(re.finditer(r"^## ([^\r\n]+)\r?$", text, re.MULTILINE))
    names = tuple(match.group(1) for match in headings)
    if len(set(names)) != len(names):
        raise ConfigError("duplicate skill node")
    if set(names) != {spec.name for spec in spine}:
        raise ConfigError("skill nodes do not match domain spine")
    nodes: list[NodeSkill] = []
    for index, heading in enumerate(headings):
        name = names[index]
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.start() : end]
        fields = tuple(re.finditer(r"^### ([^\r\n]+)\r?$", section, re.MULTILINE))
        values: dict[str, str] = {}
        for offset, field in enumerate(fields):
            key = field.group(1)
            if key in values:
                raise ConfigError("duplicate skill field")
            field_end = fields[offset + 1].start() if offset + 1 < len(fields) else len(section)
            values[key] = section[field.end() : field_end].strip()
        if not values.keys() >= _REQUIRED or values.keys() - (_REQUIRED | {"Model"}):
            raise ConfigError("invalid skill headings")
        model = values.get("Model")
        if model is not None and (
            not model or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model) is None
        ):
            raise ConfigError("malformed skill Model")
        allowed = frozenset(line.strip() for line in values["Tools"].splitlines() if line.strip())
        for tool_name in allowed:
            tool = tools.get(tool_name)
            if tool is None:
                raise ConfigError("skill lists an unknown tool")
            if name not in tool.allow_in and "*" not in tool.allow_in:
                raise ConfigError("skill lists a disallowed tool")
        nodes.append(
            NodeSkill(
                name,
                section,
                values["Purpose"],
                values["Heuristics"],
                values["Tools"],
                values["Outputs"],
                values["Pitfalls"],
                allowed,
                model,
            )
        )
    preamble = text[: headings[0].start()] if headings else text
    return SkillPolicy(preamble, tuple(nodes), source)


def snapshot_skill(policy: SkillPolicy, run_dir: Path) -> str:
    """Publish a complete file exclusively; never replace an existing run policy."""
    target = run_dir / "skill.snapshot.md"
    try:
        if target.exists():
            if target.read_bytes() != policy.original_bytes:
                raise ConfigError("run skill snapshot already differs")
            return policy.sha256
        with tempfile.NamedTemporaryFile(dir=run_dir, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(policy.original_bytes)
                handle.close()
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if target.read_bytes() != policy.original_bytes:
                        raise ConfigError("run skill snapshot already differs") from None
            finally:
                temporary.unlink(missing_ok=True)
    except OSError:
        raise ConfigError("could not snapshot skill policy") from None
    return policy.sha256


def load_run_skill(
    run_dir: Path, source_path: Path, *, spine: tuple[NodeSpec, ...], tools: Mapping[str, ToolSpec]
) -> SkillPolicy:
    target = run_dir / "skill.snapshot.md"
    if target.exists():
        return load_skill(target, spine=spine, tools=tools)
    policy = load_skill(source_path, spine=spine, tools=tools)
    snapshot_skill(policy, run_dir)
    return policy
