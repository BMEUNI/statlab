---
status: completed
dependencies: [68]
completion_date: 2026-10-09
implementation_notes: "repeated profiling columns are dropped, not rejected; the same run status is announced once; make check 100%"
---

# Chunk 69 — repeated profiling columns; duplicated pause/resume messages

User report 2026-10-09, run `r_2d7c595bc5228b0e`: after pause and resume, data profiling
failed; the chat showed "Run paused" and "Run running" twice.

Findings (read-only, user `statlab.db` and the run's `events.jsonl`):
1. Attempt 1 of `data_profiling` was interrupted by the pause (designed). Attempt 2's
   `ProfileDecision` named `time_management_score` twice in `correlations`;
   `_validate_profile` (`backend/statlab/domains/foundation.py` ~456-484) rejects repeats, the
   repair repeated it, the node failed ("rejected ['time_management_score'] (unknown, repeated
   or incompatible); allowed: [... 'time_management_score']"). A repeat is harmless.
2. Events 8/9 (`paused`) and 10/11 (`running`) are identical `run.status` pairs. Pause: the
   loop announces `paused` when it drains (`backend/statlab/graph/engine.py` ~356 / ~331 /
   `_finish_fanout` ~895), then `RunLifecycle._stop` (`backend/statlab/graph/lifecycle.py` ~138)
   announces it again. Resume: `RunLifecycle.resume` (~102) announces `running`, then
   `RunLoop._run` (~347) again. Every `run.status` goes through `RunLoop._status`
   (`engine.py` ~138; callers in engine, lifecycle, gates, reconciliation, rewind).

Decisions:
1. `ProfileDecision` drops repeated entries (first occurrence kept) in `numeric`,
   `categorical`, `correlations` and `outliers` with an after-validator; the JSON schema is
   unchanged (no gate drift). `_validate_profile` then checks only unknown/incompatible
   names; its message says "(unknown or incompatible)".
2. `RunLoop._status` always updates the run row, but appends the `run.status` event only
   when `(status, current_node)` differs from the last one this loop instance announced.
Non-goals: the chat "continue" behaviour on a failure gate, the unexplained cancel.

## Stage 69a (detailed spec)

Owned: `backend/statlab/domains/decisions.py`, `backend/statlab/domains/foundation.py`,
`backend/statlab/graph/engine.py`, `tests/test_nodes_foundation.py`,
`tests/test_graph_engine.py`. Not alone in the tree: preserve others' edits.

1. `decisions.py` `ProfileDecision`: add after `plots`:
   `@model_validator(mode="after")` `def _unique(self) -> ProfileDecision:` — return
   `self.model_copy(update={name: tuple(dict.fromkeys(getattr(self, name))) for name in
   ("numeric", "categorical", "correlations", "outliers")})`. Docstring: "Repeated column
   names are dropped; the first occurrence is kept (chunk 69)." (`model_validator` is already
   imported.) If mypy rejects `getattr` typing, use four explicit
   `tuple(dict.fromkeys(self.numeric))` etc. entries instead.
2. `foundation.py` `_validate_profile`: `bad = sorted({entry for entry in selected if entry
   not in compatible})` (drop the repeated-entry set); message text `"(unknown or
   incompatible)"` instead of `"(unknown, repeated or incompatible)"`. Nothing else changes.
3. `engine.py` `RunLoop`:
   - In `__init__` add `self._announced: tuple[str, str | None] | None = None`.
   - `_status`: keep `update_run_status(...)` as is; then `announced = (state.status,
     current_node)`; if `announced == self._announced`: return; else set
     `self._announced = announced` and append the event as now.
4. Tests:
   - `tests/test_nodes_foundation.py` `test_profile_check_feeds_llm_repair`: after the
     `x vs y` assertion add `assert ProfileDecision(rationale="r", numeric=("x", "x", "y"),
     correlations=("x", "y", "x")).numeric == ("x", "y")` and the same object's
     `.correlations == ("x", "y")`, and `assert check(ProfileDecision(rationale="dup",
     numeric=("x", "x"))) is None`. If the harness's `x` is not numeric there, stop and report.
   - `tests/test_graph_engine.py` new `test_status_event_is_announced_once(engine_world)`:
     `nodes = (node(),)`; `async def execute(invocation): return
     NodeResult(status="completed")`; `loop = engine_world.loop(nodes, execute)`; `state =
     engine_world.initial(nodes)`; call `await loop._status(state.model_copy(update=
     {"status": "paused"}), "alpha")` twice, then `await loop._status(state.model_copy(update=
     {"status": "running"}), "alpha")` twice, then once with `"paused"` again; `events =
     await loop.events.replay()`; assert `[e.payload["status"] for e in events if e.type ==
     "run.status"] == ["paused", "running", "paused"]` and `get_run(engine_world.conn,
     state.run_id).status == "paused"` (assert not None first). Use the node name that
     `node()` produces if it is not `"alpha"` (read `tests/test_graph_state.py` `node`).
     If `_status` needs a run row that the fixture lacks, stop and report.

Checks: `make test-fast TESTS="tests/test_nodes_foundation.py tests/test_graph_engine.py
tests/test_control_lifecycle.py tests/test_restart_reconciliation.py tests/test_control_gates.py"`
Ruff check, Ruff format --check
and mypy on the five owned Python files; `.venv/bin/python -m integration.run_scenarios
--llm offline --only S01 S07` (offline; S07 covers pause/resume). If an existing test fails
only because one of two identical consecutive `run.status` events is gone, stop and report the
test and assertion; do not edit it.

Main then: review, `make check`, commit.

### Addendum 69a-A (field validator instead of model_copy)

A top-level model validator must return `self`; returning `model_copy` warns (warnings are
errors). Replace `ProfileDecision._unique` with a field validator: add `field_validator` to the
existing `pydantic` import; `@field_validator("numeric", "categorical", "correlations",
"outliers")` `@classmethod` `def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:`
docstring as before, body `return tuple(dict.fromkeys(value))`. An after-mode field validator
does not change the JSON schema. Rerun the 69a checks.

## Acceptance receipt (2026-10-09)

Implementer (after addendum A): focused selectors (foundation, graph engine, control
lifecycle, restart reconciliation, control gates) exit 0; Ruff check/format and mypy on the
five files exit 0; offline S01 and S07 exit 0 PASS (`integration/out/20261009T155050Z-offline`);
no existing test depended on the duplicate `run.status`. Main reviewed the diff (matches the
recipe) and confirmed `ProfileDecision.model_json_schema()` still equals the stored schema of
the user's pending ProfileDecision gate (no startup drift). `make check` (`check69.log`,
temporary) exit 0: coverage 100 % (TOTAL 12604 stmts, 0 missed), mypy 200 files, Ruff clean.
