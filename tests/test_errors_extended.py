"""Tests for the chunk-02 extension of backend.statlab.errors: X1-X10 per
plan/02_core_types.md §4.4.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from statlab.errors import (
    ArtifactError,
    ArtifactLockedError,
    EngineError,
    StatlabError,
    ToolCallError,
    ToolNotAllowedError,
)
from statlab_contracts.envelope import Failure
from statlab_contracts.envelope import retry_budget as _retry_budget

_FAILURE = Failure(error_class="bug", message="boom")


def test_tool_call_error_message() -> None:
    """X1."""
    exc = ToolCallError("split_random", _FAILURE)
    assert "'split_random'" in str(exc)
    assert "boom" in str(exc)


def test_tool_call_error_class_delegates() -> None:
    """X2."""
    exc = ToolCallError("split_random", _FAILURE)
    assert exc.error_class == _FAILURE.error_class


def test_tool_call_error_retry_budget_delegates() -> None:
    """X3."""
    exc = ToolCallError("split_random", _FAILURE)
    assert exc.retry_budget == _retry_budget(_FAILURE.error_class)


def test_tool_call_error_cause_preserved() -> None:
    """X4."""
    original = ValueError("underlying")
    exc = ToolCallError("split_random", _FAILURE, cause=original)
    assert exc.cause is original


def test_tool_not_allowed_error_stores_and_names_all_three() -> None:
    """X5."""
    allowed = frozenset({"load_csv", "infer_column_roles"})
    exc = ToolNotAllowedError("train_estimator", "intake", allowed)
    assert exc.tool == "train_estimator"
    assert exc.node == "intake"
    assert exc.allowed == allowed
    message = str(exc)
    assert "train_estimator" in message
    assert "intake" in message
    for name in allowed:
        assert name in message


def test_artifact_error_stores_ref() -> None:
    """X6."""
    exc = ArtifactError("a_0123456789abcdef", "not found")
    assert exc.ref == "a_0123456789abcdef"
    assert "a_0123456789abcdef" in str(exc)


def test_artifact_locked_error_is_artifact_error() -> None:
    """X7."""
    assert issubclass(ArtifactLockedError, ArtifactError)


def test_artifact_locked_error_message_mentions_test_set_and_node() -> None:
    """X8."""
    exc = ArtifactLockedError("a_0123456789abcdef", "model_training")
    message = str(exc)
    assert "test" in message.lower()
    assert "model_training" in message
    assert exc.current_node == "model_training"


@pytest.mark.parametrize(
    "cls",
    [
        ToolCallError,
        ToolNotAllowedError,
        ArtifactError,
        ArtifactLockedError,
        EngineError,
    ],
)
def test_every_new_class_is_statlab_error(cls: type[StatlabError]) -> None:
    """X9 (5 of 6 — EngineError has no required args; ConfigError already covered by chunk 01)."""
    assert issubclass(cls, StatlabError)


def test_no_error_module_imports_a_heavy_library() -> None:
    """X10: subprocess check. The test session already imports pandas/httpx/mcp elsewhere, so
    checking sys.modules in-process would be contaminated by other tests' imports. A subprocess
    gives a clean interpreter where only statlab.errors (and its statlab_contracts dependency)
    has been imported.
    """
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import statlab.errors\n"
        "after = set(sys.modules)\n"
        "added = after - before\n"
        "heavy = {'pandas', 'httpx', 'mcp'}\n"
        "leaked = {m.split('.')[0] for m in added} & heavy\n"
        "assert not leaked, f'errors.py import pulled in heavy libs: {leaked}'\n"
        "print('ok')\n"
    )
    result = subprocess.run(  # noqa: S603  # `script` is a hardcoded literal defined above, not untrusted input; sys.executable is the running interpreter
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
