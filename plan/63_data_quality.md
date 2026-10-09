---
status: in_progress
dependencies: [62]
completion_date: null
implementation_notes: "63-0 accepted (runs keep captured spine, view-only fallback); 63a next"
---

# Chunk 63 — data quality check node (proposal)

User request 2026-10-09: integrate a data quality check, research the best library, make it a
new node and show it in the UI.

## Research (2026-10-09)

Hard constraint: Python 3.14, pandas 3.0.5, numpy 2.5.3, scikit-learn 1.9.0, strict mypy,
warnings as errors, no `warnings.catch_warnings`, `n_jobs=1`. Metadata from PyPI, resolution
by `uv pip compile --python-version 3.14` against the pinned stack.

| Library | Latest | Fits the stack | What it is | Verdict |
|---|---|---|---|---|
| Deepchecks | 0.19.1, 2024-12-15 | no 3.14 classifier, no release in 22 months | ML-specific `data_integrity` suite: mixed nulls, mixed types, string mismatch, duplicates, conflicting labels, single value, special characters, rare/outlier samples, feature-label PPS | Best functional match, but unmaintained. Use its check list as the model, not the package. |
| ydata-profiling | 4.18.4 | no: Python < 3.14, pandas < 3, numpy < 2.4 | HTML profiling report with alerts | Cannot install. |
| DataProfiler | 0.14.0 | no: pandas < 3 | profiler | Cannot install. |
| Great Expectations (GX Core) | 1.24.0, 2026-10-07 | resolves, adds ~25 packages (altair, cryptography, marshmallow, jsonschema, ruamel, ...) | pipeline data-contract platform: expectation suites, data context, checkpoints, Data Docs | Overkill for one in-process node; heavy ceremony; duplicates our contract validation. |
| Pandera | 0.34.1, 2026-10-06 | resolves, adds typeguard, typing-inspect | schema/value validation for DataFrames, lazy failure cases | Solid, but our `DataContract` + `validate_contract` already cover schema/roles; value rules are a few pandas lines. Not needed for v1. |
| Pointblank | 1.0.1 | resolves | validation reports; depends on `anthropic[bedrock]`, great-tables | Pulls an LLM SDK into compute; no. |
| Evidently | 0.7.23 | resolves, very heavy (litestar, uvicorn, plotly<6, nltk, ...) | drift/monitoring reports | Monitoring, not pre-training quality. |
| Soda Core | 4.26.0 | proprietary licence, SQL-oriented | warehouse checks | No. |
| cleanlab (core) | 2.9.0, 2026-01-13 | resolves; adds only termcolor, tqdm (Datalab needs the `datasets` extra: no) | confident learning: label issues from out-of-fold probabilities; outlier and near-duplicate scores | The one thing worth a dependency: label-noise detection is real research, not a few lines. Needs fitting, so it must run on train only (D32). Optional phase 2. |

Field consensus (DC-Check checklist, Deepchecks suite, cleanlab docs, Google data prep
checklist): before training check schema/types, missing values including disguised ones,
exact/near duplicates, conflicting or noisy labels, class imbalance and tiny classes, and
train/test overlap.

Already covered here: missingness (`report_missingness`), imbalance (`summarize_target`), IQR
outliers (`detect_outliers`), id columns, single-column leaks (`detect_leaky_columns`),
constant columns (preprocessing), contract/dtype mismatches (`validate_contract`), split
overlap and cross-split duplicates (`verify_split`), duplicate dropping (contract option).

Gaps the new node closes (Deepchecks' integrity checks, written in pandas):
1. `disguised_missing`: text cells like `?`, `-`, `NA`, `n/a`, `null`, `none`, `unknown`,
   `missing`, empty or whitespace-only; numeric sentinels -999, -9999, 9999, 99999.
2. `mixed_types`: a text column where 5–95 % of values parse as numbers.
3. `category_variants`: values of one column equal after trim, case-fold and dropping
   punctuation (`Male`, `male `, `MALE`).
4. `duplicate_rows`: exact duplicates over non-id columns (count and share).
5. `conflicting_labels`: identical feature rows with different targets.
6. `near_constant`: one value covers at least 99 % of a column.
7. `rare_categories`: category levels with fewer than 5 rows.
8. `target_health`: null targets; classes smaller than the CV fold count (stratified split
   and CV break).
9. `duplicate_columns`: two columns with identical values.

Each check returns `status` (`pass`/`warn`/`fail`), affected row and column counts, share, and
at most 5 examples. No fitting, so it may read the whole input like profiling does.

## Proposed design

Node `data_quality`, SHARED, skippable, gate policy `always`, placed after `data_profiling` and
before `data_splitting` (so fixes reach the split and every model). New tool phase `quality`.

1. Tools (`mcp_server/statlab_tools/tools/quality.py`, no new dependency):
   - `check_data_quality(frame, target, id_columns)` runs the nine checks.
   - `apply_quality_fixes(frame, target, fixes)` with fixes from a closed set:
     `mark_missing` (listed tokens/sentinels to NaN per column) and `merge_variants` (map each
     variant group to its most frequent spelling). Produces a new frame artifact.
2. Decision `QualityDecision`: findings, chosen fixes (validated against the check output),
   disposition `proceed`/`escalate`. Normal approval gate; any `fail` status escalates in
   supervised mode, autonomous continues and records it.
3. `data_splitting` reads the quality frame when present (else the profiled frame, as now);
   experiment design and the report see the findings through the existing digest.
4. UI: timeline step with icon `ShieldCheck`; a "Data quality" card in the step details (one
   row per check: status badge, plain-language name, rows/columns affected, examples); a
   one-line summary in the run chat; a `## Data quality` table in the report.
5. Plumbing: spine + lineage + executor maps, D12 allow-list, `skills/modelling.md` section,
   offline stand-in answer for `QualityDecision`, integration `_SHARED_NODES`, layout/skills
   tests.

Stages (each fully specified before delegation): 63a tools, 63b node + split wiring, 63c UI and
report, then offline S01/S07 and the user's live run.

Phase 2 (chunk 64, optional): `label_quality` with cleanlab core `find_label_issues` on train
only, out-of-fold probabilities from a fixed simple model (`n_jobs=1`). First an implementer
spike under warnings-as-errors with pandas 3; drop it if cleanlab warns.

## User choices (2026-10-09)

1. v1 applies the two safe fixes (`mark_missing`, `merge_variants`), not report only.
2. The cleanlab label-noise check is in scope: chunk 64, after chunk 63.

## Amendment A — old runs must open and work without errors (user request 2026-10-09)

Facts (read-only):
- Opening a run (`GET /api/runs/{id}`, `runs.py` `snapshot` ~540) always binds it:
  `RunRegistry.binding` (~222) loads the run's skill snapshot with the *current* spine
  (`load_run_skill(..., spine=TABULAR_DOMAIN.nodes)` ~302) and builds `RunState.initial` from
  the current spine (~321); `bind_tabular` (`domains/binding.py` ~73) builds the loop with the
  current nodes.
- `load_skill` raises `skill nodes do not match domain spine` (`llm/skills.py` ~62) when the
  snapshot's node headings differ; `RunLoop.load_state` (`engine.py` ~152) and
  `Checkpointer.load` (`checkpoint.py` ~55) reject a checkpoint whose `spine` differs.
- Startup recovery of open runs goes through the same binding (`boot` ~354). Rewind children
  copy the parent's captured spine (`rewind.py` ~174). Contract-edit rewinds pick the owner
  node from the current spine (`runs.py` ~667).
- All 10 stored runs (`projects/*/runs/*/state.json`) share one captured spine today.

Consequence without this amendment: adding `data_quality` makes every existing run return
500 on open (skill snapshot and checkpoint spine mismatch), and an open old run breaks
startup recovery. Chunk 64 would do the same to runs made under chunk 63.

Decision: **a run keeps the spine it was created with ("spine of record").**
1. `binding` reads the run's checkpoint before binding. No checkpoint (a pending run that
   never started): current spine. Otherwise the run's nodes are the current `NodeSpec`s whose
   names appear in the captured spine, in captured order; they must reproduce the captured
   `SpineEntry` tuple exactly. These nodes go to `load_run_skill`, `RunState.initial`,
   `bind_tabular` and the contract-edit owner lookup (owner not in the run's spine: 409
   "this setting belongs to a step this run does not have").
2. A captured spine the current code cannot reproduce (unknown node, or a changed entry):
   the run is view-only. `GET` builds the snapshot from `state.json` and the database without
   binding; resume, gate answers, messages, rewind and fork return 409 "this run was made with
   an earlier version of the workflow and can only be viewed"; startup recovery skips it with
   one warning log line. The UI shows that sentence and hides the action controls.
3. Code that reads the new steps tolerates their absence: the split uses the `data_quality`
   frame only when a completed `data_quality` boundary exists, else the profiled frame as
   now; the report's data quality section and the UI card appear only when the step ran.
   Same rule for chunk 64's step.
4. The UI already draws the timeline from the run's own `state.spine`, so old runs show no
   quality step; `NodeIcon` falls back for unknown names.
5. No DB migration (node names are text); old contracts need no new field (the new steps add
   no required contract setting); stored reports and artifacts are unchanged.

Stage order: this becomes **stage 63-0, before any spine change**, so it is proven alone.
Tests (63-0): (a) a run whose checkpoint and skill snapshot lack one current node opens (200),
resumes, answers a gate and rewinds on its own spine; (b) a captured spine with an unknown
node opens view-only (200) and every action returns the 409 above; (c) startup recovery with
such an open run logs and continues; (d) a pending run without a checkpoint binds to the
current spine. Acceptance adds a manual check: copy the user's `statlab.db` and `projects/`
to the scratchpad, start the offline stack (port 8766) on the copy after chunk 63, open all 10
existing runs, their reports, artifacts and charts (all 200), and rewind one.

## Chunk 64 outline — label noise (cleanlab)

Node `label_quality`, SHARED, skippable, after `data_splitting`, before `experiment_design`.
Train split only (D32): out-of-fold class probabilities from a fixed logistic-regression
pipeline (`cross_val_predict`, `n_jobs=1`, contract seed), then cleanlab core
`find_label_issues` (no Datalab, no `datasets` extra). Classification only; regression skips
with a reason. Fix: the decision may drop flagged train rows, at most 5 % of train, through
the normal approval; valid and test are never touched. First an implementer spike under
warnings-as-errors with pandas 3; if cleanlab warns or fails there, stop and report before
any product code. Amendment A covers runs made before chunk 64.

## Stage 63-0 — runs keep their captured spine (detailed spec)

Goal: Amendment A, before any spine change. Non-goals: the quality node itself, contract-edit
owners for nodes a run lacks (every `SETTING_OWNERS` value exists in every stored spine; the
first chunk that maps a setting to a new node handles it), changing `Checkpointer.load`
behaviour.

Owned: `backend/statlab/graph/checkpoint.py`, `backend/statlab/graph/reconciliation.py`,
`backend/statlab/domains/binding.py`, `backend/statlab/api/runs.py`,
`backend/statlab/api/stream.py`, `tests/test_api_runs.py`, `tests/test_domain_binding.py`,
`frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/pages/RunPage.tsx`,
`frontend/src/run/RunHeader.tsx`, `frontend/src/chat/RunChat.tsx`,
`frontend/tests/client.test.ts`, plus any frontend test file in which `npm run typecheck`
reports a `RunSnapshot` literal missing `view_only` (add `view_only: false` there, nothing
else). Preserve other edits.

1. `checkpoint.py`: add `def read(self) -> RunState | None:` (docstring "The stored
   checkpoint without identity checks, or None before the first save (chunk 63).") holding
   the current read/parse code of `load`: `FileNotFoundError` returns None; `(OSError,
   UnicodeError)` raises `PersistenceError("cannot read run checkpoint", cause=exc)`;
   `ValidationError` raises `PersistenceError("invalid run checkpoint", cause=exc)`.
   `load` becomes: `state = self.read()`; if None: return None when `allow_missing`, else raise
   `PersistenceError("run checkpoint is missing")`; then the unchanged identity check and
   return.
2. `reconciliation.py`: `type BindingResolver = Callable[[RunRow], RecoveryBinding | None]`;
   right after `binding = self.resolver(row)` add `if binding is None:` `continue` (comment
   `# A view-only run is left as stored (chunk 63).`).
3. `binding.py`: `bind_tabular` gains keyword `nodes: tuple[NodeSpec, ...]` (after
   `memory`); `RunLoop(nodes=nodes, ...)` instead of `TABULAR_DOMAIN.nodes`. Import
   `NodeSpec` from `statlab.domains.base`. `tests/test_domain_binding.py`: both
   `bind_tabular(` calls pass `nodes=TABULAR_DOMAIN.nodes` (import it if absent).
4. `runs.py`:
   - Constant `VIEW_ONLY = "this run was made with an earlier version of the workflow and can
     only be viewed"`.
   - `class ViewOnlyRunError(StatlabError):` docstring "A stored run whose captured spine the
     current domain cannot rebuild (chunk 63)."; `__init__(self, state: RunState) -> None`
     calls `super().__init__(VIEW_ONLY)` and sets `self.state = state`. Import
     `StatlabError` from `statlab.errors` if not imported.
   - Module function `def run_nodes(state: RunState | None) -> tuple[NodeSpec, ...]:`
     docstring "The nodes a run executes on: its captured spine, or the current one before
     its first checkpoint (chunk 63)." Body: None returns `TABULAR_DOMAIN.nodes`; else
     `current = {spec.name: spec for spec in TABULAR_DOMAIN.nodes}`, `nodes =
     tuple(current[entry.name] for entry in state.spine if entry.name in current)`; if
     `state.spine_id != TABULAR_DOMAIN.name or tuple(SpineEntry.from_spec(spec) for spec in
     nodes) != state.spine` raise `ViewOnlyRunError(state)`; return `nodes`. Import
     `NodeSpec` (`statlab.domains.base`) and `SpineEntry` (`statlab.graph.state`).
   - `RunSnapshot` gains `view_only: bool = False`.
   - `binding`: right after the `for child in (...)` path-escape loop, `nodes =
     run_nodes(initial if initial is not None else Checkpointer(paths).read())`. Then
     `load_run_skill(..., spine=nodes, ...)`, `RunState.initial(..., nodes=nodes)` and
     `bind_tabular(..., nodes=nodes)`. Nothing else in `binding` changes.
   - `run`: wrap `return self.binding(row)` in `try`; `except ViewOnlyRunError: raise
     HTTPException(409, VIEW_ONLY) from None`.
   - New method after `run`: `def events(self, run_id: str) -> EventBus:` docstring "The
     run's event log; a view-only run's log is read without binding it (chunk 63)."
     `row = get_run(...)`, None -> `HTTPException(404, "run not found")`; `try: return
     self.binding(row).loop.events`; `except ViewOnlyRunError:` `project =
     require_project(self.resources.conn, row.project_id)`; `return
     EventBus(RunPaths(self.resources.settings.data_dir, project.slug, row.id))`. Import
     `EventBus` from `statlab.events`.
   - `boot` resolver: return type `RecoveryBinding | None`; `try: binding =
     self.binding(row)` `except ViewOnlyRunError:` `_LOGGER.warning("Run %s was made with an
     earlier workflow version; it can only be viewed", row.id)`; `return None`.
   - `snapshot`: `row = get_run(...)`, None -> 404 as in `run`; `try: binding =
     self.binding(row)` `except ViewOnlyRunError as exc: return RunSnapshot(run=row,
     state=exc.state, gates=list_gates(self.resources.conn, run_id),
     execution_failed=run_id in self.failed_tasks, view_only=True)`; the rest unchanged.
5. `stream.py`: `stream_events(owner.events(run_id), ...)` instead of binding through
   `owner.run(run_id)`.
6. Frontend:
   - `types.ts` `RunSnapshot` gains `view_only: boolean;`.
   - `client.ts` `parseRunSnapshot`: add `|| typeof value.view_only !== 'boolean'` to the
     invalid check; return `view_only: value.view_only`.
   - `RunHeader.tsx`: prop `viewOnly?: boolean` (default `false`); `const controls = viewOnly
     ? { pause: false, resume: false, cancel: false, rewind: false } :
     runControls(run.status);`.
   - `RunChat.tsx`: prop `viewOnly?: boolean` (default `false`); render `<Composer ... />`
     only when `!viewOnly`, otherwise `<p className="help">This run can only be
     viewed.</p>`.
   - `RunPage.tsx`: `const viewOnly = data.view_only;`; pending filter `gate.status ===
     'pending' && !finished && !viewOnly`; pass `viewOnly={viewOnly}` to `RunChat` and
     `RunHeader`; after the `execution_failed` alert add `{viewOnly && <p className="help">This
     run was made with an earlier version of the workflow. You can view it, but not
     continue, change or rewind it.</p>}`.
   - `tests/client.test.ts`: the snapshot fixture gets `view_only: false`; the invalid list
     gets `{ ...snapshot, view_only: 'no' }`; add `assert.equal(parseRunSnapshot({
     ...snapshot, view_only: true }, 'r1').view_only, true);`.
7. Tests in `tests/test_api_runs.py` (after `test_autonomous_paused_restart`):
   - Helper `def _rewrite_spine(paths: RunPaths, node: str, replacement: str | None) -> None`:
     load `paths.state_json` with `json.loads`; when `replacement` is None drop the `spine`
     entry named `node`, else set that entry's `"name"` to `replacement`; write back with
     `json.dumps`. When `replacement` is None also cut the skill section: `text =
     paths.skill_snapshot_md.read_text(encoding="utf-8")`, `start = text.index(f"\n##
     {node}\n")`, `end = text.find("\n## ", start + 1)`, `text = text[:start] + (text[end:] if
     end != -1 else "\n")`, write back.
   - `test_run_nodes_follow_captured_spine` (sync): `run_nodes(None) ==
     TABULAR_DOMAIN.nodes`; with `RunState.initial(run_id="r_0000000000000001",
     mode="guided", spine_id=TABULAR_DOMAIN.name, nodes=TABULAR_DOMAIN.nodes[:-1])` the result
     is `TABULAR_DOMAIN.nodes[:-1]`; the same with `spine_id="other"` and with nodes
     `(TABULAR_DOMAIN.nodes[1], TABULAR_DOMAIN.nodes[0], *TABULAR_DOMAIN.nodes[2:])` each raise
     `ViewOnlyRunError` (and `exc.state` is the given state for one of them).
   - `test_old_run_keeps_its_captured_spine`: as `test_gate_action_outcomes` start a run with
     `FixtureRecords(replies=deque([_decision()]))`, `gate = await _gate(api, run_id)`, keep
     `paths = RunPaths(api.settings.data_dir, "demo", run_id)`; after the block
     `_rewrite_spine(paths, "artifact_persistence", None)`. Reopen with
     `FixtureRecords(replies=deque([_decision("Child")]))`, `setup=False,
     existing_run_id=run_id`: GET the run -> 200, `view_only` false, spine names ==
     `[n.name for n in TABULAR_DOMAIN.nodes[:-1]]`; answer the gate with `{"action":
     "rewind", "target": TABULAR_DOMAIN.nodes[0].name}` -> 200; `child_id =
     answered.json()["new_run_id"]`; `await _gate(restarted, child_id)`; the child's
     `lifecycle.state()` spine names equal the same list.
   - `test_view_only_run_opens_and_refuses_actions(tmp_path, monkeypatch, caplog)`: same
     start, then `_rewrite_spine(paths, "artifact_persistence", "retired_step")`. Reopen with
     `caplog.at_level(logging.WARNING, logger="statlab.api.runs")`: startup succeeds; a log
     record contains `run_id` and "earlier workflow version"; `run_id not in
     restarted.registry.bindings`. GET -> 200, `view_only` true, `"retired_step"` in the
     spine names, one gate. POST `/messages` `{"text": "hello"}`, POST `/control`
     `{"action": "pause"}`, POST `/gates/{gate.id}` `{"action": "approve"}` each -> 409 with
     `detail == VIEW_ONLY`. `bus = restarted.registry.events(run_id)`: `bus.paths.run_id ==
     run_id` and `await bus.replay()` is non-empty.
   If any existing test pins the old `bind_tabular` signature or snapshot shape beyond the
   listed edits, stop and report.

Checks: `make test-fast TESTS="tests/test_api_runs.py tests/test_api_stream.py tests/test_restart_reconciliation.py tests/test_graph_checkpoint.py tests/test_domain_binding.py tests/test_registry.py"`;
Ruff check, Ruff format --check and mypy on every owned Python file; from `frontend/`:
`npm test`, `npm run typecheck`, `npm run build`.

Main then: `make check`, receipt, commit. The real-data check (copy of the user's DB) runs
after stage 63b, when the spine actually changes.

### Addendum 63-0-A (captured order must follow the current order)

The implementer found that `run_nodes` accepts a reordered captured spine. Decision: a run
keeps its own node *set*, never its own order; the captured names must appear in the
current spine's order (code such as the split reading the quality frame relies on it). In
`run_nodes` add a third alternative to the raise condition: `or nodes != tuple(spec for spec
in TABULAR_DOMAIN.nodes if spec in nodes)`. Then write `test_run_nodes_follow_captured_spine`
exactly as specified in step 7 (the reordered case now raises). Checks: `make test-fast
TESTS="tests/test_api_runs.py"`; Ruff check, Ruff format --check and mypy on
`backend/statlab/api/runs.py` and `tests/test_api_runs.py`.

### Addendum 63-0-B (resolver type in reconciliation tests)

`make check` typecheck: `tests/test_restart_reconciliation.py` lines ~219, 412, 465, 471, 486,
508 call `reconciler.resolver(row).loop.events`, which is now `RecoveryBinding | None`.
Owned: `tests/test_restart_reconciliation.py`. Add after the `service` helper:
`def _events(reconciler: RestartReconciler, row: RunRow) -> EventBus:` with `binding =
reconciler.resolver(row)`, `assert binding is not None`, `return binding.loop.events`
(import `EventBus` from `statlab.events`). Replace each `reconciler.resolver(X).loop.events`
with `_events(reconciler, X)`, `X` unchanged. Nothing else. Checks: `make test-fast
TESTS="tests/test_restart_reconciliation.py"`; Ruff check, Ruff format --check and mypy on
that file; then `make typecheck` (whole-project mypy).

### Stage 63-0 acceptance receipt (2026-10-09)

Implementer (after addenda A, B): six focused selectors exit 0 (176 tests before addendum A;
`tests/test_api_runs.py` and `tests/test_restart_reconciliation.py` rerun exit 0 after the
addenda), Ruff check/format and mypy on the owned Python files exit 0, `npm test` 97/97,
typecheck 0, build 0. Main reviewed the full diff against the spec. First `make check`
(`check63-0.log`): coverage 100 %, typecheck failed (six `RecoveryBinding | None` uses in
`tests/test_restart_reconciliation.py`) -> addendum B. Final `make check` (`check63-0b.log`)
exit 0: coverage 100 % (TOTAL 12258 stmts, 0 missed), mypy 198 files, Ruff clean; frontend
`npm test` 97/97 and typecheck 0 rerun by main. Logs temporary. Proven: a run whose captured
spine lacks a current node opens, keeps that spine and rewinds to a child with the same
spine; an unknown captured node opens view-only, its event log reads, every action returns
409 `VIEW_ONLY`, and startup recovery logs and continues; a reordered spine is view-only.
