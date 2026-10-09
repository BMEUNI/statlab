---
status: completed
dependencies: [57]
completion_date: 2026-10-09
implementation_notes: "grid limit in TrainingDecision; candidate failure skips the gate; finished runs show no decisions; Agent calls table removed; make check 100%, npm 96/96"
---

# Chunk 58 — a failing model no longer stops the run

User report 2026-10-09, run `r_edd756461dd61f32` (project `online-test-1`, autonomous):
two of five models failed at model training with `param_grid has 64 points, more than
MAX_GRID_POINTS=50`; the run showed "Waiting for you (2)" with only "Review or change", the
other three models finished, nothing reached champion selection, and the cards stayed after
the user stopped the run. Also: the step details still show the "Agent calls" token table;
the user wants no per-step usage at all (the header total is enough).

Causes:
1. `TrainingDecision` (`backend/statlab/domains/decisions.py` ~179) checks grid keys and
   values (`tunable_grid_errors`) but not the grid size. The tool (`tune_estimator`,
   `mcp_server/statlab_tools/tools/training.py` ~646) rejects more than `MAX_GRID_POINTS = 50`
   combinations, so an oversized grid passes the decision check and fails at the tool, where
   no repair happens. The model is also never told the limit.
2. `GateService.failure` (`backend/statlab/graph/gates.py` ~228) turns every failed node,
   including one model's branch, into a failure gate with options `abort` (cancel the whole
   run) and `rewind`. A branch in `awaiting_user` blocks the fan-out join
   (`engine.py` ~768), so the finished models never reach champion selection. Without the
   gate the engine already handles this: a `failed` branch ends, the join continues when at
   least one branch is viable, and `eligible_candidates` (`champion.py` ~72) excludes it.
3. `RunPage.tsx` ~51 lists every `pending` gate even after the run ended, and a cancelled run
   leaves its gates pending.

Decision: (1) grid size becomes part of decision validation, so the LLM repair loop sees
"param_grid has 64 combinations; use at most 50" and the training question states the limit;
the limit lives in `statlab_contracts.vocabulary` and the tool imports it. (2) A failure in a
model's own step (invocation with a candidate) no longer opens a gate: the branch ends as
`failed`, the other models continue, champion selection excludes it; when every model fails
the run fails as before (no viable branch). Spine-step failures keep their gate. (3) The run
page shows waiting decisions only while the run is not finished. (4) Remove the Agent calls
table. Non-goals: closing stale gate rows in the database, a "retry this model" action.

## Stage 58a — backend

Owned: `contracts/statlab_contracts/vocabulary.py`,
`mcp_server/statlab_tools/tools/training.py`, `backend/statlab/domains/candidate_models.py`,
`backend/statlab/graph/gates.py`, `tests/test_candidate_vocabulary.py`,
`tests/test_candidate_fanout.py`. Preserve other edits.

1. `vocabulary.py`: add `MAX_GRID_POINTS = 50` with docstring "Largest tuning grid, in
   combinations of values (chunk 58; enforced in decisions and by `tune_estimator`)." placed
   right above `tunable_grid_errors`. In `tunable_grid_errors`, after the existing loop and
   before the return: `points = math.prod(len(values) for values in grid.values())`; when
   `grid` is non-empty and `points > MAX_GRID_POINTS`, append
   `f"param_grid has {points} combinations; use at most {MAX_GRID_POINTS} (fewer values per
   parameter)"`. (`math` is already imported.)
2. `training.py`: delete the local `MAX_GRID_POINTS = 50` and its docstring; add
   `MAX_GRID_POINTS` to the existing `statlab_contracts.vocabulary` import. The check at ~646
   and its message stay unchanged (so `test_tune_grid_over_50_points_is_tool_param` still
   matches "MAX_GRID_POINTS").
3. `candidate_models.py` ~197: the sentence "Only these keys may appear in params or
   param_grid; ..." gets, right after "param_grid", the words " (a param_grid has at most
   {MAX_GRID_POINTS} combinations in total)" as an f-string, importing `MAX_GRID_POINTS` from
   `statlab_contracts.vocabulary`. If an existing test pins this exact question text, update
   that assertion to the new text and report it.
4. `gates.py` `failure`: right after the `result.error is None` check, add
   `if invocation.candidate is not None:` `return result` with the comment
   `# One model's failure ends only its branch; siblings continue (chunk 58).`
5. Tests:
   - `test_candidate_vocabulary.py` `test_tunable_grid_errors`: add
     `grid = {"C": (0.1, 0.2, 0.3, 0.4), "tol": (1e-4, 1e-3, 1e-2, 1e-1), "max_iter": (100,
     200, 300, 400)}` -> `tunable_grid_errors(grid) == ["param_grid has 64 combinations; use at
     most 50 (fewer values per parameter)"]`, and a 50-combination grid (`{"C": tuple(0.1 * i
     for i in range(1, 51))}`) -> `[]`. If `tol` or `max_iter` are not valid tunable keys or
     values in the vocabulary, stop and report the valid alternatives.
   - `test_candidate_fanout.py`: new `test_candidate_failure_with_gates_ends_only_its_branch`
     modelled on `test_halted_sibling_isolated` (same nodes and `execute`, `c_01` returning
     `NodeResult(status="failed", error=Failure(error_class="tool_param", message="grid too
     large"))`): build `runner = loop(world, nodes, execute)`, `start = initial(world, nodes)`,
     install `GateService(RunLifecycle(runner, start))` (imports from
     `statlab.graph.gates` and `statlab.graph.lifecycle`), `state = await runner.run(start)`;
     assert `state.branches[0].status == "failed"`, the others `completed`,
     `state.status == "completed"`, `tail == ["compare"]`, and
     `list_gates(world.conn, start.run_id) == ()` (import from the repository). If
     `RunLifecycle(runner, start)` cannot be built with the fan-out `world`, stop and report
     the error.

Checks: `make test-fast TESTS="tests/test_candidate_vocabulary.py tests/test_candidate_fanout.py tests/test_tools_training.py tests/test_control_gates.py tests/test_nodes_candidate_models.py tests/test_decisions.py"`
(skip any of the last two that do not exist and say so); Ruff check, Ruff format --check and
mypy on the six owned files.

## Stage 58b — frontend (after 58a)

Owned: `frontend/src/pages/RunPage.tsx`, `frontend/src/run/NodeInspector.tsx`. Preserve
other edits.

1. `RunPage.tsx` ~51: `const finished = ['completed', 'failed', 'cancelled',
   'superseded'].includes(data.run.status);` and the `pending` filter becomes
   `gate.status === 'pending' && !finished`.
2. `NodeInspector.tsx`: delete the whole `{llmCalls.length > 0 && (...)}` block (the "Agent
   calls" table) and drop `llmCalls` from the `attempts.map` destructuring. Nothing else.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: `make check`, browser check of the step details, commit.

## Acceptance receipt (2026-10-09)

58a implementer: focused selectors (vocabulary, fanout, tools training, control gates,
candidate models) exit 0; Ruff/format/mypy on six files exit 0; `tests/test_decisions.py`
does not exist. The training-question substring pinned in `test_nodes_candidate_models.py`
is unchanged. 58b implementer: `npm test` 96/96, typecheck 0, build 0. Main reviewed both
diffs and ran `make check` exit 0: coverage 100 % (TOTAL 12200 stmts, 0 missed), mypy 197
files, Ruff clean (log `check58.log`, temporary). No browser check: 58b only deletes the
table and filters pending gates by run status; the user's next live run is the end-to-end
check.
