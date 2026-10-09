"""Atomic validated input and completed-node checkpoints."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

from pydantic import ValidationError
from statlab_contracts.layout import RunPaths

from statlab.errors import PersistenceError
from statlab.graph.state import RunState, SpineEntry


class Checkpointer:
    def __init__(self, paths: RunPaths) -> None:
        self.paths = paths

    def save(self, state: RunState) -> None:
        if state.run_id != self.paths.run_id:
            raise PersistenceError("checkpoint run identity mismatch")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.paths.root, prefix=".state-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(state.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.paths.state_json)
        except OSError as exc:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            raise PersistenceError("cannot save run checkpoint", cause=exc) from exc

    def read(self) -> RunState | None:
        """The stored checkpoint without identity checks, or None before the first save
        (chunk 63)."""
        try:
            text = self.paths.state_json.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise PersistenceError("cannot read run checkpoint", cause=exc) from exc
        try:
            return RunState.model_validate_json(text, strict=True)
        except ValidationError as exc:
            raise PersistenceError("invalid run checkpoint", cause=exc) from exc

    def load(
        self, *, spine_id: str, spine: tuple[SpineEntry, ...], allow_missing: bool = False
    ) -> RunState | None:
        state = self.read()
        if state is None:
            if allow_missing:
                return None
            raise PersistenceError("run checkpoint is missing")
        if state.run_id != self.paths.run_id or state.spine_id != spine_id or state.spine != spine:
            raise PersistenceError("checkpoint run/spine identity mismatch")
        return state
