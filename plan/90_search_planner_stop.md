---
status: completed
dependencies: [89]
completion_date: 2026-10-27
implementation_notes: "wave-2 planner BoundaryStopError becomes a lifecycle stop"
---

# Chunk 90 — a stop during the wave-2 search decision pauses the run instead of crashing it

Found by the chunk 89a pytest suite (2026-10-27): S16 "API restart mid-run" failed G1 in 2 of 5
pytest runs (0 of 5 under the old runner, same code path, different timing). `api.log`:
`ERROR statlab.api.runs Run r_… execution failed with BoundaryStopError`, raised from
`RunControl.check` (`backend/statlab/graph/candidates.py:106`) inside `DomainRuntime.decide`
(`domains/runtime.py:122`), called by the wave-2 search planner (`domains/candidate_search.py:124`)
from `RunLoop._fanout` (`backend/statlab/graph/engine.py:801`:
`proposal = await self.candidate_planner(self._latest, wave)`).

Cause: the wave-1 path guards the planner (`_bootstrap_candidates`, `engine.py:854-866`: a
`BoundaryStopError` sets `self._lifecycle_stop = True` and returns, so `_finish_fanout` saves
the run as `paused`/`cancelled`). The wave-2 call has no guard, so a pause, cancel or shutdown
that lands during the search LLM decision escapes as a task failure.

User approved fixing it now (2026-10-27).

Owned files: `backend/statlab/graph/engine.py`, `tests/test_candidate_fanout.py`. Not alone:
preserve others' edits (chunk 89a is uncommitted in `integration/`, `pyproject.toml`, `uv.lock`,
`Makefile`).

## 1. `engine.py` `_fanout`

Replace the single line `proposal = await self.candidate_planner(self._latest, wave)` with:
```
try:
    proposal = await self.candidate_planner(self._latest, wave)
except BoundaryStopError:
    self._lifecycle_stop = True
    return await self._finish_fanout()
```
(pseudocode shape; same as the wave-1 guard). `BoundaryStopError` is already imported
(`engine.py:27`). No `search.finished` event on this path (the wave did not finish; G3 checks
node events only). Nothing else changes.

Edge: `cancelled` control → `_finish_fanout` stores `cancelled` (it uses `control.status`);
`budget_exhausted` is raised elsewhere (`candidates.py:149`), never by the planner's
`control.check`, so not reachable here.

## 2. Test — `tests/test_candidate_fanout.py`

New test after `test_planner_empty_or_pause`, same fixtures/helpers (`world`, `node`, `loop`,
`initial`, `RunControl`, `TraversalPlan`):
`test_planner_stop_inside_search_decision_pauses` (`@pytest.mark.asyncio`):
nodes `(node("design"), node("fit", candidate=True), node("tail"))`; `control = RunControl()`;
`seen: list[str]`; `execute` appends `inv.spec.name` and returns `NodeResult(status="completed")`;
`planner(state, wave)`: `assert wave == 2`; `control.request_pause()`; `control.check()` (raises
`BoundaryStopError("paused")`); after it, `return ()` (unreachable line for mypy: write the
planner so `control.check()` is followed by `return ()`). Run `runner.run(initial(world,
nodes))` directly (no task/milestone needed). Assert `result.status == "paused"`,
`result.wave == 1`, `len(result.branches) == 3`, `"tail" not in seen`,
`runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result`.
Before the fix this test raises `BoundaryStopError` out of `runner.run`.

## 3. Checks (implementer)

- `make test-fast TESTS="tests/test_candidate_fanout.py"`
- Ruff check, Ruff format --check, mypy on the two owned files.

Main acceptance: `make check`; S16 offline under pytest ×5 with no G1 failure.

## Acceptance receipt (2026-10-27)

`engine.py` +5/-1, one new fan-out test (51 fan-out tests pass). `make check` exit 0 (shared with
89a receipt: 3348 passed, 1 skip, 100 %). S16 offline under pytest: 5/5 PASS (was 3/5).
