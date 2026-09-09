"""Task 0's verification, kept as a permanent regression test. D1-D5 per §4.5."""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

_RUNTIME_MODULES = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic_settings",
    "python_multipart",
    "httpx",
    "litellm",
    "mcp",
    "pandas",
    "pyarrow",
    "numpy",
    "sklearn",
    "statsmodels",
    "matplotlib",
    "joblib",
    "pytest",
    "mypy",
    "ruff",
)

_PROVIDER_NAMES = ("deepseek", "anthropic", "openai", "claude", "gpt")
_CONFIG_MODULE_PATHS = (
    _REPO_ROOT / "backend" / "statlab" / "config.py",
    _REPO_ROOT / "mcp_server" / "statlab_tools" / "settings.py",
)


def _load_pyproject() -> dict[str, object]:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


@pytest.mark.parametrize("module_name", _RUNTIME_MODULES)
def test_d1_every_dependency_imports(module_name: str) -> None:
    """D1."""
    importlib.import_module(module_name)


def test_d2_interpreter_matches_requires_python() -> None:
    """D2."""
    data = _load_pyproject()
    project = data["project"]
    assert isinstance(project, dict)
    requires_python = str(project["requires-python"])
    match = re.match(r">=\s*(\d+)\.(\d+)", requires_python)
    assert match is not None, f"Unparseable requires-python: {requires_python!r}"
    minimum = (int(match.group(1)), int(match.group(2)))
    assert sys.version_info[:2] >= minimum


def _iter_dependency_strings(data: dict[str, object]) -> list[str]:
    project = data["project"]
    assert isinstance(project, dict)
    deps = list(project["dependencies"])  # dict value type is `object`, list() is fine as-is

    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        dev = groups.get("dev")
        if isinstance(dev, list):
            deps.extend(dev)
    return [str(d) for d in deps]


def test_d3_every_dependency_is_pinned_exactly() -> None:
    """D3."""
    data = _load_pyproject()
    deps = _iter_dependency_strings(data)
    assert deps, "expected at least one dependency to check"
    for dep in deps:
        assert "==" in dep, f"{dep!r} is not pinned with =="
        for forbidden in (">=", "~=", "*", "^"):
            assert forbidden not in dep, f"{dep!r} contains a range operator {forbidden!r}"


_NAME_VERSION_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([A-Za-z0-9_.+-]+)$")


def test_d4_installed_versions_match_the_pins() -> None:
    """D4."""
    data = _load_pyproject()
    deps = _iter_dependency_strings(data)
    checked = 0
    for dep in deps:
        match = _NAME_VERSION_RE.match(dep)
        assert match is not None, f"could not parse name/version from {dep!r}"
        name, pinned_version = match.group(1), match.group(2)
        installed_version = importlib.metadata.version(name)
        assert installed_version == pinned_version, (
            f"{name}: pinned {pinned_version!r} but installed {installed_version!r}"
        )
        checked += 1
    assert checked == len(deps)


def test_d5_no_provider_names_in_config_modules() -> None:
    """D5. D18: no provider or model name outside llm/."""
    for path in _CONFIG_MODULE_PATHS:
        text = path.read_text(encoding="utf-8").lower()
        for provider in _PROVIDER_NAMES:
            assert provider not in text, f"{path} mentions provider name {provider!r}"
