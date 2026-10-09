---
status: completed
dependencies: [59]
completion_date: 2026-10-09
implementation_notes: "search.started/finished events; champion step shows running while comparing; make check 100%, npm 97/97"
---

# Chunk 60 — show the "compare models" pause before champion selection

User report 2026-10-09: after every model finishes, the UI shows nothing for about a minute
before champion selection starts, so the run looks frozen.

Cause: after the candidate join, `RunLoop` (`backend/statlab/graph/engine.py` ~794) calls
`candidate_planner` for the next search wave. `CandidateSearchPlanner`
(`domains/candidate_search.py`) makes an LLM decision (`SearchDecision`) on the
experiment-design role, which uses reasoning. No node execution starts and no event is
emitted, so the timeline shows champion selection "Not started" and the chat line says
"Starting the next step…".

Decision: the engine emits `search.started` `{"wave": n}` right before that planner call and
`search.finished` `{"wave": n, "added": k}` right after it returns (k = number of proposed
candidates, 0 ends the search). The frontend folds these into `searching`; while the run is
running and searching, the chat status line reads "Comparing the models and deciding whether
to try more…", and the timeline shows champion selection as running with the note
"Comparing the models first." Non-goals: a new spine step, changing the planner.

## Stage 60a — backend

Owned: `backend/statlab/graph/engine.py`, `tests/test_candidate_fanout.py`. Preserve other
edits.

1. `engine.py` ~794: replace `proposal = await self.candidate_planner(self._latest,
   self._latest.wave + 1)` with: `wave = self._latest.wave + 1`;
   `await self.events.append("search.started", {"wave": wave})`;
   `proposal = await self.candidate_planner(self._latest, wave)`;
   `await self.events.append("search.finished", {"wave": wave, "added": len(proposal)})`.
   Nothing else changes (if a local named `wave` already exists in that scope, stop and
   report).
2. `test_candidate_fanout.py` `test_search_and_retune_caps_append_attempts`: after the
   existing assertions, `searches = [(e.type, e.payload) for e in await
   runner.events.replay() if e.type.startswith("search.")]`; assert `searches ==
   [("search.started", {"wave": 2}), ("search.finished", {"wave": 2, "added": 1})]`. If the
   events model's attribute names differ from `type`/`payload`, use the real names and
   report.

Checks: `make test-fast TESTS="tests/test_candidate_fanout.py tests/test_graph_engine.py tests/test_api_runs.py"`;
Ruff check, Ruff format --check and mypy on the two owned files.

## Stage 60b — frontend (after 60a)

Owned: `frontend/src/run/model.ts`, `frontend/src/run/RunTimeline.tsx`,
`frontend/src/pages/RunPage.tsx`, `frontend/tests/run-model.test.ts`. Preserve other edits.

1. `model.ts`:
   - `RunFeed` gains `searching: boolean`. In `foldEvents`, `let searching = false;`; new
     cases `'search.started'` sets `searching = true` and pushes an activity item
     `{ ...base, kind: 'activity', text: 'Comparing the models' }`; `'search.finished'` sets
     `searching = false` and pushes activity text `typeof p.added === 'number' && p.added > 0
     ? \`Trying ${p.added} more model${p.added === 1 ? '' : 's'}\` : 'No more models
     needed'`. Return `{ items, drafts, completedSeq, searching }`.
   - `nowLine` gains a fifth parameter `searching = false`; right after the `pending.length
     > 0` branch: `if (searching && status === 'running') return 'Comparing the models and
     deciding whether to try more…';`.
2. `RunTimeline.tsx`: prop `searching?: boolean` (default `false`). In `shared(node)`,
   `const comparing = searching && node.name === 'champion_selection' && statusOf(node.name,
   null) === 'pending';`, `const status = comparing ? 'running' : statusOf(node.name, null);`
   and `const note = comparing ? 'Comparing the models first.' : (outcome?.skipReason ??
   outcome?.error?.message ?? null);`. Nothing else changes.
3. `RunPage.tsx`: `const searching = feed.searching && data.run.status === 'running';`; pass
   it as the fifth argument of `nowLine(...)` and as `searching={searching}` to
   `RunTimeline`.
4. Tests in `run-model.test.ts`:
   - In `foldEvents builds the feed` (or a new test next to it): folding
     `[event('search.started', { wave: 2 })]` gives `searching === true` and a last item
     `{ kind: 'activity', text: 'Comparing the models' }` (compare kind and text);
     folding `[event('search.started', { wave: 2 }), event('search.finished', { wave: 2,
     added: 0 })]` gives `searching === false` and last item text `'No more models needed'`;
     with `added: 2` the text is `'Trying 2 more models'`. `foldEvents([]).searching ===
     false`.
   - In `nowLine`: `nowLine('running', view, [], [], true) === 'Comparing the models and
     deciding whether to try more…'` and `nowLine('completed', view, [], [], true) ===
     'Completed'`.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: `make check`, offline browser check if a pause is observable, commit.

### Addendum 60a-A (one `wave` local)

`_fanout` already has `wave = self._latest.wave + 1` at ~810, and nothing between ~794 and
~810 changes `self._latest.wave`. Define `wave = self._latest.wave + 1` once, right before the
`search.started` event (step 1), and delete the later line ~810; its uses (~815, 826, 829)
keep reading the same `wave`. `self.events` exists on the loop (`engine.py` ~108).

## Acceptance receipt (2026-10-09)

60a implementer (after addendum A): fanout, engine and API runs tests exit 0; Ruff/format
and mypy on both files exit 0. 60b implementer: `npm test` 97/97, typecheck 0, build 0; test
deviation accepted (feed item narrowed on `kind` before reading `text`, same expectations).
Main reviewed both diffs; the SSE client reads unnamed messages and accepts any event type.
`make check` exit 0: coverage 100 % (TOTAL 12212 stmts, 0 missed), mypy 198 files, Ruff clean
(log `check60.log`, temporary). No browser check: the offline stand-in answers instantly, so
the pause is not observable offline; the user's next live run shows it.
