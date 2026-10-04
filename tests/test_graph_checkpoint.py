"""Checkpoint validation and atomic replace failure injection."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from statlab.errors import PersistenceError
from statlab.graph import Checkpointer, RunState
from statlab_contracts.layout import RunPaths

from tests.test_graph_state import node


def initial(paths: RunPaths) -> RunState:
    return RunState.initial(
        run_id=paths.run_id, spine_id="synthetic", nodes=(node(),), mode="guided"
    )


def test_missing_and_roundtrip(run_paths: RunPaths) -> None:
    checkpoint, state = Checkpointer(run_paths), initial(run_paths)
    assert checkpoint.load(spine_id=state.spine_id, spine=state.spine, allow_missing=True) is None
    with pytest.raises(PersistenceError, match="missing"):
        checkpoint.load(spine_id=state.spine_id, spine=state.spine)
    checkpoint.save(state)
    assert checkpoint.load(spine_id=state.spine_id, spine=state.spine) == state


@pytest.mark.parametrize("change", ["run_id", "spine_id", "spine"])
def test_identity_mismatch(run_paths: RunPaths, change: str) -> None:
    checkpoint, state = Checkpointer(run_paths), initial(run_paths)
    payload = state.model_dump(mode="json")
    payload[change] = [] if change == "spine" else "foreign"
    run_paths.state_json.write_text(RunState.model_validate(payload).model_dump_json())
    with pytest.raises(PersistenceError, match="identity"):
        checkpoint.load(spine_id=state.spine_id, spine=state.spine)
    with pytest.raises(PersistenceError, match="identity"):
        checkpoint.save(state.model_copy(update={"run_id": "foreign"}))


@pytest.mark.parametrize("data", [b"{", b'{"run_id":1}', b"\xff"])
def test_invalid_checkpoint(run_paths: RunPaths, data: bytes) -> None:
    state = initial(run_paths)
    run_paths.state_json.write_bytes(data)
    with pytest.raises(PersistenceError):
        Checkpointer(run_paths).load(spine_id=state.spine_id, spine=state.spine)


def test_failed_replace_preserves_prior_boundary(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, state = Checkpointer(run_paths), initial(run_paths)
    checkpoint.save(state)
    before = run_paths.state_json.read_bytes()

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("injected atomic replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(PersistenceError, match="save"):
        checkpoint.save(state.model_copy(update={"status": "running"}))
    assert run_paths.state_json.read_bytes() == before
    assert list(run_paths.root.glob(".state-*")) == []


def test_cleanup_failure_preserves_primary_save_error(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, state = Checkpointer(run_paths), initial(run_paths)
    checkpoint.save(state)
    before = run_paths.state_json.read_bytes()
    primary = OSError("injected atomic replace failure")
    cleanup_attempts: list[Path] = []

    def fail_replace(self: Path, target: Path) -> Path:
        raise primary

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        assert missing_ok
        cleanup_attempts.append(self)
        raise PermissionError("injected cleanup failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(PersistenceError, match="save") as caught:
        checkpoint.save(state)
    assert caught.value.cause is primary
    assert caught.value.__cause__ is primary
    assert run_paths.state_json.read_bytes() == before
    assert cleanup_attempts == list(run_paths.root.glob(".state-*"))
    assert len(cleanup_attempts) == 1


def test_read_and_open_failures(run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint, state = Checkpointer(run_paths), initial(run_paths)
    run_paths.state_json.mkdir()
    with pytest.raises(PersistenceError, match="read"):
        checkpoint.load(spine_id=state.spine_id, spine=state.spine)

    def fail_open(*args: object, **kwargs: object) -> None:
        raise OSError("cannot create temporary file")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_open)
    with pytest.raises(PersistenceError, match="save"):
        checkpoint.save(state)
