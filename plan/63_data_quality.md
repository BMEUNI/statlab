---
status: completed
dependencies: [62]
completion_date: 2026-10-09
implementation_notes: "runs keep their captured spine (old runs open, unreproducible ones view-only); data_quality step with nine checks and two safe fixes before the split; step card and report section; make check 100%, npm 99/99"
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

## Stage 63a — quality tools (detailed spec)

Goal: two MCP tools in a new phase `quality`, `allow_in=["data_quality"]` (the node arrives in
63b; `allow_in` names are free strings and nothing validates them against the spine). No new
dependency. Non-goals: the node, skill section, UI, report.

Owned: new `mcp_server/statlab_tools/tools/quality.py`, `mcp_server/statlab_tools/tools/__init__.py`
(one import line, alphabetical: `from statlab_tools.tools import quality as quality` after
`profiling`), new `tests/test_tools_quality.py`. Preserve other edits.

Reuse from `statlab_tools.tools._frame`: `is_numeric_column`, `is_class_target`,
`observed_counts`, `json_scalar`, `json_list`, `as_str`, `as_str_scalar`, `require_columns`,
`save_frame`; `load_dataframe` from `statlab_tools.artifacts`; `ToolParamError`, `tool` from
`statlab_tools.tool`; `ToolSuccess` from `statlab_contracts.envelope`. Every parameter is
`Annotated[..., Field(description=...)]` (the decorator and `test_server_mcp_session` M4
require it); docstrings follow the module style.

### Module `quality.py`

Docstring: "Data quality checks before the split (chunk 63): Deepchecks-style integrity checks
written in pandas. No fitting, so they may read the whole input like profiling does. Two
tools: `check_data_quality` reports, `apply_quality_fixes` applies the two safe fixes. Output
frames keep the input's index labels and row order."

Constants (`Final`, each with a one-line docstring):
- `MISSING_TOKENS = frozenset({"", "?", "-", "--", "na", "n/a", "nan", "null", "none",
  "missing", "unknown", "undefined", "not available"})` (compared after `strip()` and
  `casefold()`).
- `SENTINELS = (-999.0, -9999.0, 9999.0, 99999.0, 999999.0)`.
- `MIXED_LOW = 0.05`, `MIXED_HIGH = 0.95`, `NEAR_CONSTANT_SHARE = 0.99`, `RARE_LEVEL_ROWS = 5`,
  `ID_LIKE_SHARE = 0.5`, `EXAMPLES = 5`.

Private helpers:
- `_text(series) -> bool`: `not is_numeric_column(series) and not
  pd.api.types.is_bool_dtype(series) and not pd.api.types.is_datetime64_any_dtype(series)`.
- `_check(status, rows, columns, details) -> dict[str, JsonValue]`: returns `{"status": status,
  "rows": rows, "columns": columns, "details": details}`; `status` is `"pass"`, `"warn"`,
  `"fail"` or `"skipped"`.
- `_variant_groups(series) -> list[list[str]]`: `strings = as_str(series.dropna())`; `keys =
  strings.str.casefold().str.replace(r"[\W_]+", "", regex=True)`; group the distinct spellings
  by key, ignoring the empty key; keep groups with at least two spellings; inside a group
  order spellings by count descending, then text ascending (so element 0 is canonical);
  order groups by their canonical spelling. Return the list.

### `check_data_quality(paths, frame, target, id_columns=(), min_class_rows=5)`

`@tool(phase="quality", allow_in=["data_quality"])`. Params: `frame: ArtifactRef` ("The
frame to check, usually the profiled frame."), `target: str` (min_length 1, "The target
column."), `id_columns: tuple[str, ...] = ()` ("Identifier columns; excluded from every
check."), `min_class_rows: int = 5` (ge=2, "Smallest class size cross-validation needs;
pass the contract's CV fold count.").

Load the frame; `require_columns(data, (target, *id_columns), "columns")`. `n = len(data)`.
`features = [c for c in data.columns if c != target and c not in id_columns]` (column order
kept). Checks, in this order, each producing `_check(...)`; `rows` is the number of distinct
row labels affected; `columns` a list of column names in frame order:
1. `disguised_missing`: for each feature column, `values = data[c].dropna()`; text columns:
   `hit = values[as_str(values).str.strip().str.casefold().isin(MISSING_TOKENS)]`; numeric
   columns: `hit = values[values.isin(SENTINELS)]`; other dtypes: none. Per column with hits a
   detail `{"column": c, "count": len(hit), "values": sorted(set(as_str(hit)))}`. Status
   `warn` when any detail, else `pass`.
2. `mixed_types`: text feature columns with `len(values) > 0`: `numeric =
   pd.to_numeric(values.astype("str"), errors="coerce").notna()`; `share = numeric.mean()`;
   when `MIXED_LOW <= share <= MIXED_HIGH`: minority mask = `~numeric` if `share >= 0.5` else
   `numeric`; detail `{"column": c, "numeric_share": round(share, 3), "examples":
   sorted(set(as_str(values[minority])))[:EXAMPLES]}`; affected rows are the minority rows.
   `warn`/`pass`.
3. `category_variants`: text feature columns with groups from `_variant_groups`: detail
   `{"column": c, "groups": groups[:EXAMPLES]}`; affected rows: rows whose spelling is in a
   group but is not its element 0. `warn`/`pass`.
4. `duplicate_rows`: `mask = data.duplicated(subset=[c for c in data.columns if c not in
   id_columns], keep="first")`; details `{"share": round(rows / n, 4) if n else 0.0,
   "example_rows": json_list(data.index[mask][:EXAMPLES])}`; `columns` `[]`. `warn` when any.
5. `conflicting_labels`: when `not is_class_target(data[target])`: `_check("skipped", 0, [],
   {"reason": "continuous target"})`. Otherwise, with no features: `pass`, 0 rows. Else
   `keys = pd.util.hash_pandas_object(data[features], index=False)`; `per_key =
   data[target].groupby(keys).nunique(dropna=False)`; `mask = keys.isin(per_key.index[per_key
   > 1])`; details `{"groups": int((per_key > 1).sum()), "example_rows":
   json_list(data.index[mask.to_numpy()][:EXAMPLES])}`. `warn` when any row.
6. `near_constant`: feature columns with `n > 0`: `counts = observed_counts(data[c])`; when
   `len(counts) > 0` and `counts.iloc[0] / n >= NEAR_CONSTANT_SHARE`: detail `{"column": c,
   "value": json_scalar(counts.index[0]), "share": round(counts.iloc[0] / n, 4)}`; affected
   rows are rows equal to that value. `warn`/`pass`.
7. `rare_categories`: text feature columns where `counts = observed_counts(data[c])` has more
   than one level and `data[c].nunique() <= ID_LIKE_SHARE * n`: `rare = counts[counts <
   RARE_LEVEL_ROWS]`; when non-empty, detail `{"column": c, "levels": len(rare), "rows":
   int(rare.sum()), "examples": json_list(rare.index[:EXAMPLES])}`; affected rows are rows
   whose value is a rare level. `warn`/`pass`.
8. `target_health`: `nulls = int(data[target].isna().sum())`; class target: `small =
   counts[counts < min_class_rows]` with `counts = observed_counts(data[target].dropna())`,
   else empty. Details `{"nulls": nulls, "small_classes": {as_str_scalar(k): int(v) for k, v
   in small.items()}}`; `columns` `[target]` when not `pass`. Status `fail` when `small`
   non-empty, else `warn` when `nulls`, else `pass`; affected rows: null-target rows plus
   rows in small classes.
9. `duplicate_columns`: pairs `(a, b)`, `a` before `b` in `features`, with
   `data[a].equals(data[b])` (comment `# ponytail: O(k^2) column compare; hash columns if
   very wide frames matter`); detail `{"pairs": [[a, b], ...][:EXAMPLES]}` only when pairs
   exist; `columns` the distinct names in those pairs; `rows` `0`. `warn`/`pass`.

Summary: `{"rows": n, "passed": <no check has status fail>, "checks": {name: check, ...}
(names in the order above), "fixable": {"mark_missing": {column: values} from
disguised_missing details, "merge_variants": [columns] from category_variants details}}`.
Warnings: `tuple(f"{name} check status is {status}" for name, check in checks if status in
("warn", "fail"))`. No artifact.

### `apply_quality_fixes(paths, frame, target, mark_missing=None, merge_variants=())`

`@tool(phase="quality", allow_in=["data_quality"])`. Params: `frame` ("The frame to fix,
the one `check_data_quality` read."), `target` (as above), `mark_missing: dict[str,
tuple[str, ...]] | None = None` ("Per column, the exact cell texts (as reported under
fixable.mark_missing) to turn into missing values."), `merge_variants: tuple[str, ...] = ()`
("Text columns whose spelling variants merge into the most frequent spelling.").

Validation, in order, each a `ToolParamError` with the given field: no fixes (`not
mark_missing and not merge_variants`) -> field `mark_missing`, "no fixes requested";
`require_columns` for `tuple(mark_missing or {})` (field `mark_missing`) and
`merge_variants` (field `merge_variants`); target listed in either -> that field, "the target
is never changed here"; a `merge_variants` column that is not `_text` -> field
`merge_variants`, "only text columns have spelling variants".

Then on `data.copy()`: for each `mark_missing` column, `wanted = {v.strip().casefold() for v
in values}`; `mask = data[c].notna() & as_str(data[c]).str.strip().str.casefold().isin(wanted)`;
`data[c] = data[c].mask(mask)`; record `marked[c] = int(mask.sum())`. Then for each
`merge_variants` column: `mapping = {spelling: group[0] for group in
_variant_groups(data[c]) for spelling in group[1:]}`; `categorical =
isinstance(data[c].dtype, pd.CategoricalDtype)`; `merged_column = as_str(data[c]).replace(mapping)`
with original nulls kept null (`.where(data[c].notna())`); if `categorical`,
`.astype("category")`; assign; record `merged[c] = mapping`. Summary `{"rows": len(data),
"marked_missing": marked, "merged": merged}`; artifact `save_frame(paths, data,
"quality_fixed")`.

### Tests `tests/test_tools_quality.py`

Use the `run_paths` fixture and a `_save(paths, frame) -> ArtifactRef` helper copied from
`tests/test_tools_cleaning_fit.py`, plus a `_check(res, name) -> dict[str, JsonValue]`
narrowing helper. Cases (inputs -> exact expectations):
1. Disguised: `{"city": ["a", "?", " unknown ", "b", None], "n": [1.0, -999.0, 3.0, 4.0, 5.0],
   "y": [0, 1, 0, 1, 0]}`, target `y`: status `warn`, rows 2, columns `["city", "n"]`,
   details `[{"column": "city", "count": 2, "values": [" unknown ", "?"]}, {"column": "n",
   "count": 1, "values": ["-999"]}]`; `fixable["mark_missing"] == {"city": [" unknown ",
   "?"], "n": ["-999"]}`.
2. Mixed: `{"code": ["1", "2", "3", "4", "x"], "y": [0, 1, 0, 1, 0]}`: `warn`, rows 1,
   details `[{"column": "code", "numeric_share": 0.8, "examples": ["x"]}]`.
3. Variants: `{"sex": ["Male", "Male", "male ", "MALE", "Female"], "y": [0, 1, 0, 1, 0]}`:
   `warn`, rows 2, details `[{"column": "sex", "groups": [["Male", "MALE", "male "]]}]`;
   `fixable["merge_variants"] == ["sex"]`.
4. Duplicates and conflicts: `{"id": [1, 2, 3, 4], "x": [1, 1, 1, 2], "y": ["a", "a", "b",
   "a"]}`, `id_columns=("id",)`: `duplicate_rows` `warn` rows 1 (`example_rows == [1]`);
   `conflicting_labels` `warn` rows 3, `details["groups"] == 1`.
5. Hundred rows: `c = ["k"] * 99 + ["z"]`, `e = [float(i) for i in range(100)]`, `f = e`
   copy, `y = ["a"] * 50 + ["b"] * 47 + ["c"] * 3`: `near_constant` details `[{"column": "c",
   "value": "k", "share": 0.99}]`; `rare_categories` details `[{"column": "c", "levels": 1,
   "rows": 1, "examples": ["z"]}]`; `duplicate_columns` details `{"pairs": [["e", "f"]]}`;
   `target_health` `fail`, details `{"nulls": 0, "small_classes": {"c": 3}}`; `passed` false.
6. Continuous target: `x = [1.0] * 25`, `y = [float(i) for i in range(25)]`:
   `conflicting_labels` status `skipped`, details `{"reason": "continuous target"}`.
7. Clean: `{"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": ["p", "q"] * 3}`, `min_class_rows=2`:
   every check status `pass`, `passed` true, `res.warnings == ()`.
8. Fixes: `{"city": pd.Categorical(["Budapest", "budapest ", "?", "Szeged"]), "n": [1.0,
   -999.0, 3.0, 4.0], "y": [0, 1, 0, 1]}`; `apply_quality_fixes(..., target="y",
   mark_missing={"city": ("?",), "n": ("-999",)}, merge_variants=("city",))`: output `city`
   is categorical with values `["Budapest", "Budapest", NaN, "Szeged"]`, `n` is `[1.0, NaN,
   3.0, 4.0]`, index unchanged; summary `marked_missing == {"city": 1, "n": 1}`, `merged ==
   {"city": {"budapest ": "Budapest"}}`; artifact label `quality_fixed`.
9. Fix errors, each `ToolParamError` with the field named: no fixes; unknown column in
   `mark_missing`; target in `merge_variants`; numeric column `n` in `merge_variants`.
If a computed value differs from an expectation above because of a pandas fact the spec did
not foresee (dtype of a result, ordering), stop and report the actual value; do not change
the algorithm.

Checks: `make test-fast TESTS="tests/test_tools_quality.py tests/test_server_mcp_session.py tests/test_tool_decorator.py"`;
Ruff check, Ruff format --check and mypy on the three owned Python files.

### Addendum 63a-A (conflicting labels with no features)

The no-features branch of `conflicting_labels` returns `_check("pass", 0, [], {"groups": 0,
"example_rows": []})` (same keys as the normal branch). Add test 10: `{"id": [1, 2], "y":
["a", "b"]}` with `id_columns=("id",)`: `conflicting_labels` is exactly `{"status": "pass",
"rows": 0, "columns": [], "details": {"groups": 0, "example_rows": []}}`.

### Addendum 63a-B (tool name; test assertion)

1. Every `apply_*` tool is transform-only, meaning it applies fitted state and may touch test
   data in node 12 (D32, `test_every_apply_tool_is_transform_only`). The quality fix fits
   nothing and runs only before the split, so it is not an `apply_*` tool: rename
   `apply_quality_fixes` to `fix_data_quality` everywhere (function, tests, error-message
   field names unchanged). The decorator stays `@tool(phase="quality",
   allow_in=["data_quality"])`.
2. Test 8 assertions, without list `iloc` (mypy overloads): `isinstance(out["city"].dtype,
   pd.CategoricalDtype)`; `out["city"].isna().tolist() == [False, False, True, False]`;
   `out["city"].dropna().tolist() == ["Budapest", "Budapest", "Szeged"]`;
   `out["n"].isna().tolist() == [False, True, False, False]`; `out["n"].dropna().tolist() ==
   [1.0, 3.0, 4.0]`; `out.index.tolist() == [0, 1, 2, 3]`.
3. Accepted: `duplicate_columns` details `{}` when no pairs.
Checks: the 63a Checks again.

### Addendum 63a-C (coverage)

`make check` (`check63a.log`): tests pass; `quality.py` misses 160, 182, 259, 284, 310, 391
and branch 422->424. All reachable; add tests in `tests/test_tools_quality.py`, no product
change:
11. Other dtypes: ten rows, `flag = [True, False] * 5`, `when = pd.date_range("2024-01-01",
    periods=10)`, `empty = pd.Series([None] * 10, dtype="str")`, `grp = ["a"] * 5 + ["b"] *
    5`, `y = [0, 1] * 5`: `disguised_missing`, `mixed_types`, `near_constant` and
    `rare_categories` all status `pass` (covers 160, 182, 259, 284).
12. Target nulls: `x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]`, `y = ["p", "q", "p", "q", "p", None]`,
    `min_class_rows=2`: `target_health == {"status": "warn", "rows": 1, "columns": ["y"],
    "details": {"nulls": 1, "small_classes": {}}}` (310).
13. In test 9 add: target in `mark_missing` (`mark_missing={"y": ("1",)}`) raises
    `ToolParamError` with field `mark_missing` (391).
14. Plain text merge: `{"sex": ["Male", "male "], "y": [0, 1]}`, `merge_variants=("sex",)`:
    output `sex.tolist() == ["Male", "Male"]`, dtype not categorical, `merged == {"sex":
    {"male ": "Male"}}` (422->424).
If a listed line stays uncovered, report which; do not add pragmas. Checks: `make test-fast
TESTS="tests/test_tools_quality.py"`; Ruff check, Ruff format --check, mypy on that file.

### Stage 63a acceptance receipt (2026-10-09)

Implementer (after addenda A, B, C): `tests/test_tools_quality.py` 13 tests exit 0;
`test_server_mcp_session.py` and `test_tool_decorator.py` with it exit 0 (86); Ruff
check/format and mypy on the owned files exit 0. Main reviewed `quality.py` against the
spec. `make check` first run (`check63a.log`): tests pass, coverage 99.92 % (seven
reachable gaps in `quality.py`) -> addendum C. Final `make check` (`check63a2.log`) exit 0:
coverage 100 % (TOTAL 12486 stmts, 0 missed; `quality.py` 227 stmts, 78 branches), mypy 200
files, Ruff clean. Logs temporary. Tool names: `check_data_quality`, `fix_data_quality`
(phase `quality`, `allow_in` `data_quality`, not transform-only).

## Stage 63b — the `data_quality` step (detailed spec)

Goal: the new SHARED step between `data_profiling` and `data_splitting`: run
`check_data_quality` on the profiled frame, let the model choose fixes from the reported
`fixable` lists, apply them with `fix_data_quality`, and hand the resulting frame to the split.
Non-goals: UI, report section (63c); fixes outside the reported lists; label noise (64).

Owned: `backend/statlab/domains/decisions.py`, `backend/statlab/domains/tabular.py`,
`backend/statlab/domains/lineage.py`, `backend/statlab/domains/executor.py`,
`backend/statlab/domains/foundation.py`, `skills/modelling.md`, `integration/offline_llm.py`,
`integration/checks.py`, `tests/test_nodes_foundation.py`, `tests/test_skills.py`,
`tests/test_api_runs.py`. Preserve other edits.

1. `decisions.py`, after `ProfileFindings`:
   `class QualityDecision(Decision):` docstring "Which reported quality fixes to apply (chunk
   63); values come only from `check_data_quality`'s `fixable` lists." Fields: `findings:
   tuple[str, ...] = ()`, `mark_missing: dict[str, tuple[str, ...]] =
   Field(default_factory=dict)`, `merge_variants: tuple[str, ...] = ()`.
2. `tabular.py`: import `QualityDecision`; insert right after the `data_profiling` NodeSpec:
   `NodeSpec("data_quality", "SHARED", True, QualityDecision, ("quality",), "always")`.
3. `lineage.py`: `"data_quality"` after `"data_profiling"` in `SPINE_TOOLS_NODES`.
4. `executor.py`: `"data_quality": foundation,` after `"data_profiling"`.
5. `foundation.py`:
   - Import `QualityDecision`.
   - Module function after `_ref`: `def _validate_quality(decision: QualityDecision, fixable:
     dict[str, JsonValue]) -> None:` `reported = _object(fixable.get("mark_missing"),
     "quality fixable mark_missing")`; for each `column, values` in
     `decision.mark_missing.items()`: `options = reported.get(column)`; when not a list raise
     `_RefusalError(f"mark_missing column {column!r} has no reported values; allowed columns:
     {sorted(reported)!r}")`; `bad = sorted(set(values) - {item for item in options if
     isinstance(item, str)})`; when `bad` raise `_RefusalError(f"mark_missing[{column!r}]
     values {bad!r} were not reported; allowed: {options!r}")`. Then `allowed_merge =
     _strings(fixable.get("merge_variants"), "quality fixable merge_variants")`; `bad =
     sorted(set(decision.merge_variants) - set(allowed_merge))`; when `bad` raise
     `_RefusalError(f"merge_variants {bad!r} were not reported; allowed:
     {list(allowed_merge)!r}")`.
   - `__call__` methods map: `"data_quality": self._quality,` after `"data_profiling"`.
   - New method after `_profile`:
     ```
     async def _quality(self, invocation, evidence) -> NodeResult:
         problem = problem_from_state(invocation.state)
         frame = _ref(shared_result(invocation.state, "data_profiling").summary, "frame")
         features = self.runtime.contract.features
         ignored = [c for c in dict.fromkeys((*features.id_columns, *features.exclude))
                    if c != problem.target]
         check = await self._call(invocation, evidence, "check_data_quality", {
             "frame": str(frame), "target": problem.target, "id_columns": ignored,
             "min_class_rows": self.runtime.contract.modelling.tuning.cv_folds})
         fixable = _object(check.summary.get("fixable"), "quality fixable")
         decision = await self.runtime.decide(invocation, QualityDecision,
             evidence=tuple(evidence.tools), question=<text below>,
             check=_rejecting(lambda d: _validate_quality(d, fixable)))
         _validate_quality(decision, fixable)
         summary = {"frame": str(frame), "decision": decision.model_dump(mode="json"),
                    "passed": check.summary.get("passed"), "checks": check.summary.get("checks")}
         approved = await self._approve(invocation, evidence, decision)
         if approved is None: return evidence.result("awaiting_user", summary)
         _validate_quality(approved, fixable)
         summary["decision"] = approved.model_dump(mode="json")
         if approved.mark_missing or approved.merge_variants:
             fixed = await self._call(invocation, evidence, "fix_data_quality", {
                 "frame": str(frame), "target": problem.target,
                 "mark_missing": {c: list(v) for c, v in approved.mark_missing.items()},
                 "merge_variants": list(approved.merge_variants)})
             summary["frame"] = str(_frame(fixed))
         return evidence.result("completed", summary)
     ```
     (pseudocode; write it with the module's typing, `dict[str, JsonValue]` for `summary`,
     and `JsonValue`-typed params as `_profile` does). Question text: "Review the data
     quality checks (whole input, before the split). Choose fixes only from the reported
     fixable lists: mark_missing maps a column to cell texts that stand for a missing value;
     merge_variants lists text columns whose spelling variants name the same category. Leave
     both empty to change nothing. Name every failing check in findings."
   - `_split`: replace the `profiled = next(...)` lookup with `prepared = next((boundary.result
     for boundary in reversed(invocation.state.shared_results) if boundary.node_name in
     ("data_quality", "data_profiling") and boundary.result.status == "completed"), None)`;
     `frame = _ref(prepared.summary, "frame") if prepared is not None else raw`; the later
     `if profiled is None and ...` becomes `if prepared is None and ...`. Nothing else.
6. `skills/modelling.md`: insert between the `data_profiling` and `data_splitting` sections:
   ```
   ## data_quality

   ### Purpose
   Check data integrity before the split and apply only safe, reported fixes.

   ### Heuristics
   - Read every check's status and name failing checks in the findings.
   - Mark reported placeholder texts (such as "?" or "unknown") as missing when they clearly stand for a missing value; keep numeric sentinels unless the column cannot take that value.
   - Merge spelling variants only when they clearly name the same category.
   - Report duplicates, conflicting labels and rare levels; the split and preprocessing handle them.

   ### Tools
   check_data_quality
   fix_data_quality

   ### Outputs
   Quality report and, when fixes are chosen, the fixed frame the split uses.

   ### Pitfalls
   - Never change the target or apply values the check did not report.
   - The checks read the whole input before the split; describe them as such.
   ```
7. `integration/offline_llm.py`: add `"QualityDecision"` to `_RATIONALE_ONLY` (after
   `"ProfileFindings"`). `integration/checks.py`: `"data_quality"` after `"data_profiling"`
   in `_SHARED_NODES`.
8. Tests:
   - `tests/test_skills.py` `_NAMES`: `"data_quality"` after `"data_profiling"`.
   - `tests/test_api_runs.py` ~367: `len(...["spine"]) == 14`.
   - `tests/test_nodes_foundation.py`:
     - `LEGACY_SPINE = tuple(spec for spec in TABULAR_SPINE if spec.name != "data_quality")`
       (module level, comment `# Runs made before chunk 63 have no data_quality step.`).
       `Harness` gains field `spine: tuple[NodeSpec, ...]` (last field); `invocation` uses
       `self.spine[position]`; `harness(...)` gains keyword `spine: tuple[NodeSpec, ...] =
       LEGACY_SPINE`, uses it for `RunState.initial(nodes=spine)` and passes it to `Harness`.
       All existing tests keep their positions and now also prove the old-run path (split
       reads the profiled frame).
     - `policy()` allowed set adds `"check_data_quality"` and `"fix_data_quality"`.
     - `FIXED = ArtifactRef("a_0000000000000007")`. `OfflineTools`: `self.fixable:
       dict[str, JsonValue] = {"mark_missing": {"city": ["?"]}, "merge_variants": ["city"]}`;
       `check_data_quality` returns `ToolSuccess(summary={"rows": 240, "passed": True,
       "checks": {}, "fixable": self.fixable})`; `fix_data_quality` returns
       `ToolSuccess(summary={"rows": 240}, artifacts=(ArtifactMeta(ref=FIXED,
       kind="dataframe", label="quality_fixed"),))`.
     - `OfflineLlm` defaults: `QualityDecision: QualityDecision(rationale="checks reviewed")`.
     - `test_declared_spine_and_deferred_schema`: `len(TABULAR_SPINE) == 14`; first five
       names `intake, problem_definition, data_profiling, data_quality, data_splitting`;
       `TABULAR_SPINE[3].decision_schema is QualityDecision and TABULAR_SPINE[3].skippable`;
       shift the later index assertions by one (`[5]` Design, `[6]` Inference, `[7:12]`,
       `[12:]`).
     - Helper `def _full(conn, project, root) -> Harness:` returns `harness(conn, project,
       root, contract=DataContract(target=TargetSpec(column="y", type="binary"),
       features=FeatureRules(id_columns=("id",), exclude=("excluded",))), spine=TABULAR_SPINE)`.
       If this contract fails validation in intake with the fixture tools, stop and report.
     - `test_quality_without_fixes_hands_profiled_frame_to_split(migrated_db, project,
       tmp_path)`: `h = _full(...)`; `await h.advance(5)`; quality =
       `shared_result(h.state, "data_quality")`: `summary["frame"] == str(FRAME)`,
       `summary["passed"] is True`; the `check_data_quality` call params equal `{"frame":
       str(FRAME), "target": "y", "id_columns": ["id", "excluded"], "min_class_rows": 5}`; no
       `fix_data_quality` call; the first call with `ctx.node == "data_splitting"` and a
       `"frame"` param has `params["frame"] == str(FRAME)`.
     - `test_quality_fixes_feed_split`: as above with `h.llm.responses[QualityDecision] =
       QualityDecision(rationale="fix", mark_missing={"city": ("?",)},
       merge_variants=("city",))`; `fix_data_quality` params `{"frame": str(FRAME), "target":
       "y", "mark_missing": {"city": ["?"]}, "merge_variants": ["city"]}`; quality
       `summary["frame"] == str(FIXED)` and `FIXED in quality.artifacts`; the first
       data_splitting frame param is `str(FIXED)`.
     - `test_quality_check_rejects_unreported_fixes`: `h = _full(...)`; `await h.advance(3)`;
       `await h.step()`; `check = h.llm.checks[QualityDecision]`: `check(QualityDecision(
       rationale="ok", mark_missing={"city": ("?",)})) is None`; `pytest.raises(
       DecisionRejectedError, match="were not reported")` for `mark_missing={"city":
       ("n/a",)}`; `match="no reported values"` for `mark_missing={"other": ("?",)}`;
       `match="merge_variants"` for `merge_variants=("other",)`.
     - `test_quality_refuses_unreported_fix_after_decision`: `h = _full(...)`;
       `h.llm.responses[QualityDecision] = QualityDecision(rationale="bad",
       merge_variants=("other",))`; `await h.advance(3)`; `result = await h.step()`:
       `result.status == "failed"`, `result.error` is not None and `"merge_variants" in
       result.error.message`.
     If other existing foundation tests fail after the `LEGACY_SPINE` switch, stop and report.

Checks: `make test-fast TESTS="tests/test_nodes_foundation.py tests/test_skills.py tests/test_api_runs.py tests/test_graph_engine_tools.py tests/test_candidate_fanout_tools.py tests/test_mcp_client.py tests/test_layout.py tests/test_skeleton.py"`;
Ruff check, Ruff format --check and mypy on every owned Python file; then
`.venv/bin/python -m integration.run_scenarios --llm offline --only S01 S07` and
`.venv/bin/python -m integration.run_scenarios --llm offline-rich --only S01` (offline, no
cost) with exit codes and PASS/FAIL lines. If a scenario fails, stop and report the failing
check names.

Main then: `make check`, the real-data check from Amendment A, commit.

### Addendum 63b-A (profiled frame is RAW in the quality tests)

Main's error: with the `_full` contract (no `features.roles`) profiling does not cast, so the
profiled frame is `RAW`. In `test_quality_without_fixes_hands_profiled_frame_to_split` and
`test_quality_fixes_feed_split` replace every expected `str(FRAME)` with `str(RAW)` (quality
summary frame, `check_data_quality` and `fix_data_quality` `frame` params, and the first
data_splitting frame in the no-fix test). `FIXED` expectations stay. Then rerun all 63b
Checks, including both offline scenario commands.

### Addendum 63b-B (skipped profiling)

Review finding: `data_profiling` is skippable. Then `_quality`'s
`shared_result(..., "data_profiling")` raises `EngineError` (a crash, not a refusal), and if
quality did run on the raw frame, `_split` would skip `cast_dtypes` because a quality boundary
exists, so contract roles would never be applied. Resolution in `foundation.py`:
1. Module function after `shared_result`: `def _completed(state: RunState, node: str) ->
   NodeResult | None:` returning the latest completed shared boundary result for `node`, or
   None (the generator the old `profiled = next(...)` used, with the node as a parameter).
2. `_quality`: `profiled = _completed(invocation.state, "data_profiling")`; `frame =
   _ref(profiled.summary, "frame") if profiled is not None else
   _ref(shared_result(invocation.state, "intake").summary, "raw")`.
3. `_split`: `profiled = _completed(invocation.state, "data_profiling")`; `quality =
   _completed(invocation.state, "data_quality")`; `frame = _ref(quality.summary, "frame") if
   quality is not None else (_ref(profiled.summary, "frame") if profiled is not None else
   raw)`; the cast condition is again `if profiled is None and contract.features.roles:`
   (roles are applied by profiling; without it the split casts whatever frame it got). Remove
   the `prepared` variable.
4. Test `test_quality_after_skipped_profile_checks_raw_and_split_casts`, modelled on
   `test_split_after_skipped_profile_casts_and_respects_no_duplicate_drop` but with
   `spine=TABULAR_SPINE` and the same contract: after `advance(2)` replace the state with
   `cursor: 3` and the skipped `data_profiling` boundary (same construction);
   `h.llm.responses[QualityDecision] = QualityDecision(rationale="fix", mark_missing={"city":
   ("?",)})`; `await h.step()` is `completed`; the `check_data_quality` and `fix_data_quality`
   calls have `params["frame"] == str(RAW)`; then `offset = len(h.tools.calls)`, `await
   h.step()` is `completed`, and the first new call is `("cast_dtypes", {"frame": str(FIXED),
   "target": "y", "roles": {"x": "numeric"}})`.
Checks: the 63b Checks again (both offline scenarios included).

### Note 63b-C (offline-rich S01 is a pre-existing failure)

Implementer's offline-rich S01: FAIL 13/24, run cancelled at a failure gate on
`experiment_design` attempt 2 (search wave 2): "approved shortlist must match existing
candidate specifications". Main ran the same command on committed `2363078` (no 63b) in a
scratch worktree: identical failure (`wt/integration/out/20261009T132912Z-offline-rich`).
Last pass `integration/out/20261008T084855Z-offline-rich`; first seen failing
`20261009T111705Z-offline-rich`. Not caused by 63b; tracked separately. For 63b, the
offline-rich command is dropped from the Checks; offline S01 and S07 remain.

### Addendum 63b-D (positional spine use in later node tests)

`make check` (`check63b.log`): 84 failures, all in tests that build on the foundation
`Harness` (now on `LEGACY_SPINE`) but index `TABULAR_SPINE` by position (`[index]`,
`position=6/8/10/11`, `cursor=6/11/12`, `.index(...)`): `tests/test_domain_executor.py` (2),
`test_nodes_candidate_models.py` (16), `test_nodes_candidate_pipeline.py` (21),
`test_nodes_candidate_search.py` (5), `test_nodes_champion.py` (24),
`test_nodes_persistence.py` (16). Errors: "assert 'data_splitting' == 'data_quality'" and
"candidate invocation identity mismatch". Resolution: these node-level tests keep proving
behaviour on the pre-63 spine positions. Owned: those six test files only. In each, replace
every `TABULAR_SPINE` reference with `LEGACY_SPINE` imported from `tests.test_nodes_foundation`
(add it to the existing `from tests.test_nodes_foundation import ...` line, or add that import
where the file has none, e.g. `test_domain_executor.py`); drop `TABULAR_SPINE` from the
`statlab.domains.tabular` import where it becomes unused (keep `TABULAR_TRAVERSAL`). No other
change. Checks: `make test-fast TESTS="tests/test_domain_executor.py tests/test_nodes_candidate_models.py tests/test_nodes_candidate_pipeline.py tests/test_nodes_candidate_search.py tests/test_nodes_champion.py tests/test_nodes_persistence.py tests/test_nodes_design_inference.py tests/test_nodes_foundation.py"`;
Ruff check, Ruff format --check and mypy on the six files. If any failure remains, stop and
report the test ids and first error lines.

Bisect for Note 63b-C (main, scratch worktree, offline-rich S01): `651d2a2` PASS, `c0cc6de`
("cheaper prompts, code-built fixed steps, stepper ...") FAIL; `a595775`, `d19d412`,
`2363078` FAIL. The regression came with `c0cc6de` (2026-10-08); a separate chunk fixes it.

### Addendum 63b-E (dispatch map covers the current spine)

`tests/test_domain_executor.py::test_dispatches_all_thirteen_declared_nodes`: line ~43 compares
the dispatcher map with the *current* spine, so it becomes `assert tuple(dispatcher.executors)
== tuple(node.name for node in TABULAR_SPINE)` (re-import `TABULAR_SPINE` from
`statlab.domains.tabular`), followed by `assert dispatcher.executors["data_quality"] is
dispatcher.executors["data_profiling"]`. The dispatch loop stays on `LEGACY_SPINE` with the
13-entry `expected` (the harness state is legacy). Nothing else. Checks: `make test-fast
TESTS="tests/test_domain_executor.py"`; Ruff check, Ruff format --check, mypy on that file.

### Addendum 63b-F (coverage: guided hold)

`make check` (`check63b2.log`): all tests pass; `foundation.py` line ~620 (`_quality`'s
`awaiting_user` return) uncovered. Add `test_quality_guided_hold_before_fixes(migrated_db,
project, tmp_path)` modelled on `test_guided_hold_retains_evidence_before_actions`: `h =
_full(...)`; `await h.advance(3)`; replace `h.state` with the same state in `mode="guided"`
(same construction as that test); `h.llm.responses[QualityDecision] =
QualityDecision(rationale="fix", mark_missing={"city": ("?",)})`; `before =
len(h.tools.calls)`; `result = await h.step()`: `result.status == "awaiting_user"`,
`"decision" in result.summary`, `result.summary["frame"] == str(RAW)`, and
`len(h.tools.calls) == before + 1` (only `check_data_quality`, no fix). Checks: `make
test-fast TESTS="tests/test_nodes_foundation.py"`; Ruff check, Ruff format --check, mypy on
that file.

### Stage 63b acceptance receipt (2026-10-09)

Implementer (after addenda A, B, D, E, F): the 63b focused selectors exit 0; Ruff
check/format and mypy on owned files exit 0; offline `run_scenarios --only S01 S07` exit 0,
both PASS (`integration/out/20261009T133044Z-offline`). Offline-rich S01 fails identically on
the pre-63b commit (Note 63b-C; regression from `c0cc6de`, separate chunk). Main reviewed the
product diff (decision, spine, lineage, executor, `_quality`, `_completed`, split frame/cast
logic, skill section). `make check`: `check63b.log` 84 failures (positional spine in six node
test files) -> addendum D, E; `check63b2.log` 99.99 % (guided hold line) -> addendum F;
final `check63b3.log` exit 0: coverage 100 % (TOTAL 12529 stmts, 0 missed), mypy 200 files,
Ruff clean. Logs temporary.

Real-data check (Amendment A): the user's `statlab.db` (sqlite backup) and `projects/`
copied to the scratchpad, stored paths rewritten to the copy, MCP (port 8767) and API (port
8766) started on the copy with a dummy provider key (no paid call possible). All 10 stored
runs: snapshot 200, `view_only` false, 13 captured steps without `data_quality`; executions,
artifact lists and SSE events 200; reports 200 where a report exists (404 for the cancelled,
superseded and awaiting runs, which have none); every artifact preview 200, or the designed
422 (non-dataframe kind) / 403 (locked test artifact); plot files identical to the user's
live server for the two runs compared. Rewinding `r_ca4d7d9637afd45e` to `data_profiling`
created `r_ef57d3f60dcf4a50` with the parent's 13-step spine; its intake LLM call failed on
the dummy key (401) as intended. Servers stopped; copy left in the scratchpad.

## Stage 63c — UI card and report section (detailed spec)

Goal: the run page shows the quality checks of a `data_quality` step; the final report gets a
`## Data quality` section. Both appear only when the step ran (old runs: nothing changes).
Non-goals: chat changes (the decision's `user_summary` already narrates the step), tool
changes, new CSS, timeline changes (`nodeLabel` already gives "Data quality").
Prerequisite: 63b accepted (`a35dbb7`). Data shapes: `check_data_quality` summary `checks`
(`mcp_server/statlab_tools/tools/quality.py` ~150-345: each entry `{status, rows, columns,
details}`, status `pass|warn|fail|skipped`), `_quality` boundary summary
(`backend/statlab/domains/foundation.py` ~612-636: `passed`, `checks`, `decision`, optional
`frame`). Not alone in the tree: preserve others' edits.

Owned files: `backend/statlab/domains/champion.py`, `tests/test_nodes_champion.py`,
`frontend/src/ui/icons.tsx`, `frontend/src/run/model.ts`, `frontend/src/run/NodeInspector.tsx`,
`frontend/tests/run-model.test.ts`.

### Backend: `champion.py`

1. Imports: add `_completed` and `_strings` to the existing `from statlab.domains.foundation
   import (...)` list (keep alphabetical order as Ruff sorts it). Add `from collections.abc
   import Sequence` only if Ruff/mypy need it for `_names` (prefer extending the existing
   `from collections.abc import Mapping` line).
2. Module constants after `TIMING_CAVEAT`:
   - `_QUALITY_LABELS: dict[str, str]` = `disguised_missing` "Placeholder values for missing
     data", `mixed_types` "Numbers and text mixed in a column", `category_variants` "Same
     category spelled differently", `duplicate_rows` "Duplicate rows", `conflicting_labels`
     "Identical rows with different targets", `near_constant` "Almost constant columns",
     `rare_categories` "Very rare categories", `target_health` "Target problems",
     `duplicate_columns` "Duplicate columns".
   - `_QUALITY_RESULTS: dict[str, str]` = `pass` "passed", `warn` "warning", `fail` "failed",
     `skipped` "skipped".
3. Private functions (module level, after the constants), each with a one-line docstring:
   - `_short(text: str) -> str`: `text` if `len(text) <= 40`, else `text[:39] + "…"`.
   - `_names(columns: Sequence[str]) -> str`: `", ".join(_short(c) for c in columns[:5])`,
     plus `f" and {len(columns) - 5} more"` appended when `len(columns) > 5`.
   - `_cell(text: str) -> str`: `" ".join(text.splitlines()).replace("|", "\\|")` (same rule
     as `reporting.cell`; the backend cannot import the MCP package).
   - `_quality_sections(state: RunState) -> tuple[ReportSection, ...]`:
     1. `result = _completed(state, "data_quality")`; `None` -> return `()`.
     2. `checks = _object(result.summary.get("checks"), "quality checks")`.
     3. `lines = ["The checks ran on the whole dataset before the split.", "", "| Check |
        Result | Rows affected | Columns |", "| --- | --- | --- | --- |"]` (the header is one
        string `"| Check | Result | Rows affected | Columns |"`).
     4. For `name, value in checks.items()`: `entry = _object(value, "quality check")`;
        `columns = _strings(entry.get("columns"), "quality check columns")`;
        `status = str(entry.get("status"))`; append
        `f"| {_QUALITY_LABELS.get(name, name)} | {_QUALITY_RESULTS.get(status, status)} | "
        f"{entry.get('rows')} | {_cell(_names(columns)) if columns else '—'} |"`.
     5. `decision = _object(result.summary.get("decision"), "quality decision")`;
        `marked = _object(decision.get("mark_missing"), "quality mark_missing")`;
        `merged = _strings(decision.get("merge_variants"), "quality merge_variants")`.
        `fixes: list[str] = []`; if `marked`: append
        `f"Placeholder values were marked as missing in: {_names(list(marked))}."`; if `merged`:
        append `f"Spelling variants were merged in: {_names(merged)}."`.
     6. `lines += ["", " ".join(fixes) if fixes else "No fixes were applied."]`; return
        `(ReportSection(heading="Data quality", body="\n".join(lines)),)`.
     Body size: at most 9 rows of capped names plus two capped lines, far below the 8000
     limit of `ReportSection.body`.
4. In `ChampionExecutor`, the `assemble_report` arguments (~376): `"sections"` becomes
   `[section.model_dump(mode="json") for section in (*choice.sections,
   *_quality_sections(state), caveats)]`. Nothing else changes.

### Backend tests: `tests/test_nodes_champion.py`

1. `test_champion_selects_before_exact_replay_and_one_task_score`: after the `"not
   comparable"` assertion add `assert "Data quality" not in str(report["sections"])` (old
   spine, no quality step: no section).
2. New `@pytest.mark.asyncio @pytest.mark.parametrize(("mark_missing", "merge_variants",
   "line"), [({"c": ["?"]}, [], "Placeholder values were marked as missing in: c."), ({},
   ["d"], "Spelling variants were merged in: d."), ({}, [], "No fixes were applied.")])`
   `async def test_report_lists_data_quality_checks_and_fixes(migrated_db, project, tmp_path,
   mark_missing: dict[str, list[str]], merge_variants: list[str], line: str) -> None`
   (fixture types as in the neighbouring tests):
   - `h, tools = await champion_harness(migrated_db, project, tmp_path)`.
   - `long = "x" * 50`; `checks: dict[str, JsonValue] = {"disguised_missing": {"status":
     "warn", "rows": 3, "columns": ["a|b", "c"], "details": []}, "duplicate_rows": {"status":
     "pass", "rows": 0, "columns": [], "details": {}}, "near_constant": {"status": "warn",
     "rows": 9, "columns": [long, "k2", "k3", "k4", "k5", "k6", "k7"], "details": []}}`.
   - `quality = BoundaryResult(node_name="data_quality", execution_id="quality-execution",
     result=NodeResult(status="completed", summary={"passed": True, "checks": checks,
     "decision": {"rationale": "r", "findings": [], "mark_missing": mark_missing,
     "merge_variants": merge_variants}}))`; `h.state = h.state.model_copy(update=
     {"shared_results": (*h.state.shared_results, quality)})`.
   - `output = await ChampionExecutor(h.runtime)(tail_invocation(h))`; assert
     `output.status == "completed"`.
   - `report = next(arguments for _, name, arguments in tools.calls if name ==
     "assemble_report")`; `sections = report["sections"]`; assert it is a list; take
     `section = sections[-2]` and `last = sections[-1]`, assert both are dicts,
     `section["heading"] == "Data quality"`, `last["heading"] == "Selection and execution
     caveats"`; `body = section["body"]`, assert it is a str and:
     `"| Placeholder values for missing data | warning | 3 | a\\|b, c |" in body`,
     `"| Duplicate rows | passed | 0 | — |" in body`,
     `"x" * 39 + "…" in body`, `"and 2 more" in body`, `line in body`.
   If the executor refuses the extra boundary, stop and report the error; do not change the
   executor beyond the recipe.

### Frontend

1. `frontend/src/ui/icons.tsx`: import `ShieldCheck` from `lucide-react` (keep the import
   list alphabetical); add `data_quality: ShieldCheck,` to `NODE_ICONS` directly after
   `data_profiling`.
2. `frontend/src/run/model.ts` (append after `stepShortLabel`; `JsonValue`, `JsonObject`,
   `isJsonObject`, `isStrings`, `humanize` are already in the module):
   - `export type QualityStatus = 'pass' | 'warn' | 'fail' | 'skipped';`
   - `export type QualityRow = { name: string; label: string; status: QualityStatus; rows:
     number; columns: string[]; note: string };`
   - `export const QUALITY_LABELS: Record<string, string>`: the nine labels of backend
     step 2, identical text.
   - `export const QUALITY_STATUS_LABELS: Record<QualityStatus, string> = { pass: 'Passed',
     warn: 'Warning', fail: 'Failed', skipped: 'Skipped' };`
   - private `isQualityStatus(value: unknown): value is QualityStatus` (same style as
     `isPhase`: four `===` comparisons).
   - private `text(value: JsonValue | undefined): string`: a string as is; a number or
     boolean via `String(value)`; anything else `''`.
   - private `texts(value: JsonValue | undefined): string[]`: not an array -> `[]`; else
     `value.map(text).filter((item) => item !== '').slice(0, 5)`.
   - private `percent(value: JsonValue | undefined): string`: a number ->
     `` `${(value * 100).toFixed(1)}%` ``; else `''`.
   - `export function qualityNote(name: string, details: JsonValue | undefined): string`:
     `items = Array.isArray(details) ? details.filter(isJsonObject) : []`;
     `one = isJsonObject(details) ? details : {}`; `switch (name)`:
     - `disguised_missing`: items mapped to `` `${text(i.column)}: ${texts(i.values).join(', ')}` ``, joined `'; '`.
     - `mixed_types`: same with `i.examples`.
     - `category_variants`: items mapped to `` `${text(i.column)}: ${groups}` `` where
       `groups = (Array.isArray(i.groups) ? i.groups : []).map((g) => texts(g).join(' / ')).join(', ')`; joined `'; '`.
     - `rare_categories`: `` `${text(i.column)}: ${text(i.levels)} rare levels (${texts(i.examples).join(', ')})` ``, joined `'; '`.
     - `near_constant`: `` `${text(i.column)}: ${text(i.value)} in ${percent(i.share)} of rows` ``, joined `'; '`.
     - `duplicate_rows`: `` `${percent(one.share)} of rows` ``.
     - `conflicting_labels`: `one.reason !== undefined` -> `text(one.reason)`; else
       `` `${text(one.groups)} groups of identical rows with different targets` ``.
     - `target_health`: `parts: string[] = []`; if `typeof one.nulls === 'number' &&
       one.nulls > 0` push `` `missing target values: ${one.nulls}` ``; `small =
       isJsonObject(one.small_classes) ? Object.entries(one.small_classes) : []`; if
       non-empty push `` `classes with too few rows: ${small.map(([k, v]) => `${k} (${text(v)})`).join(', ')}` ``; return `parts.join('; ')`.
     - `duplicate_columns`: `(Array.isArray(one.pairs) ? one.pairs : []).map((p) => texts(p).join(' = ')).join(', ')`.
     - default: `''`.
   - `export function qualityRows(summary: JsonObject): QualityRow[]`: `const { checks } =
     summary`; not `isJsonObject(checks)` -> `[]`; else `Object.entries(checks).flatMap(([name,
     entry]) => ...)`: skip (`[]`) when `!isJsonObject(entry) || !isQualityStatus(entry.status)`;
     otherwise one row `{ name, label: QUALITY_LABELS[name] ?? humanize(name), status,
     rows: typeof entry.rows === 'number' ? entry.rows : 0, columns: isStrings(entry.columns)
     ? entry.columns : [], note: status === 'pass' ? '' : qualityNote(name, entry.details) }`.
3. `frontend/src/run/NodeInspector.tsx`:
   - import `QUALITY_STATUS_LABELS`, `qualityRows`, `type QualityStatus` from `./model.ts`
     (extend the existing import).
   - module constant `const QUALITY_PILLS: Record<QualityStatus, string> = { pass: 'pill
     signal', warn: 'pill gate', fail: 'pill fault', skipped: 'pill' };`
   - in the component after `const attempts = ...`: `const quality = outcome === null || node
     !== 'data_quality' ? [] : qualityRows(outcome.summary);`
   - inside the outcome fragment, directly after the Metrics `table-wrap` block and before the
     Files block: when `quality.length > 0`, a `<div className="table-wrap"><table>` with
     `<caption>Data quality checks</caption>`, header cells (`scope="col"`) Check, Result,
     Rows, Columns, Details, and one `<tr key={row.name}>` per row: `row.label`; `<span
     className={QUALITY_PILLS[row.status]}>{QUALITY_STATUS_LABELS[row.status]}</span>`;
     `row.rows`; `row.columns.length === 0 ? '—' : row.columns.join(', ')`; `row.note === ''
     ? '—' : row.note`.

### Frontend tests: `frontend/tests/run-model.test.ts`

Add `qualityNote` and `qualityRows` to the import list (alphabetical position). New tests:
1. `test('qualityRows reads the checks and skips malformed entries', ...)`: summary
   `{ checks: { disguised_missing: { status: 'warn', rows: 3, columns: ['a'], details:
   [{ column: 'a', count: 3, values: ['?', 'n/a'] }] }, duplicate_rows: { status: 'pass',
   rows: 0, columns: [], details: { share: 0, example_rows: [] } }, conflicting_labels:
   { status: 'skipped', rows: 0, columns: [], details: { reason: 'continuous target' } },
   target_health: { status: 'fail', rows: 2, columns: ['y'], details: { nulls: 1,
   small_classes: { b: 1 } } }, bogus: { status: 'odd', rows: 1, columns: [], details: [] },
   extra_check: { status: 'warn', rows: 'x', columns: [1], details: null } } }`;
   `assert.deepEqual(qualityRows(summary), [...])` with, in order:
   `{ name: 'disguised_missing', label: 'Placeholder values for missing data', status: 'warn',
   rows: 3, columns: ['a'], note: 'a: ?, n/a' }`,
   `{ name: 'duplicate_rows', label: 'Duplicate rows', status: 'pass', rows: 0, columns: [],
   note: '' }`,
   `{ name: 'conflicting_labels', label: 'Identical rows with different targets', status:
   'skipped', rows: 0, columns: [], note: 'continuous target' }`,
   `{ name: 'target_health', label: 'Target problems', status: 'fail', rows: 2, columns:
   ['y'], note: 'missing target values: 1; classes with too few rows: b (1)' }`,
   `{ name: 'extra_check', label: 'Extra check', status: 'warn', rows: 0, columns: [], note:
   '' }`. Also `assert.deepEqual(qualityRows({}), [])` and
   `assert.deepEqual(qualityRows({ checks: [] }), [])`.
2. `test('qualityNote explains each finding', ...)` with `assert.equal`:
   - `qualityNote('mixed_types', [{ column: 'age', numeric_share: 0.9, examples: ['ten'] }])` -> `'age: ten'`
   - `qualityNote('category_variants', [{ column: 'city', groups: [['Paris', 'paris'], ['Rome', 'ROME']] }])` -> `'city: Paris / paris, Rome / ROME'`
   - `qualityNote('rare_categories', [{ column: 'c', levels: 2, rows: 3, examples: ['x', 'y'] }])` -> `'c: 2 rare levels (x, y)'`
   - `qualityNote('near_constant', [{ column: 'k', value: 0, share: 0.995 }])` -> `'k: 0 in 99.5% of rows'`
   - `qualityNote('duplicate_rows', { share: 0.02, example_rows: [1] })` -> `'2.0% of rows'`
   - `qualityNote('conflicting_labels', { groups: 2, example_rows: [] })` -> `'2 groups of identical rows with different targets'`
   - `qualityNote('duplicate_columns', { pairs: [['a', 'b']] })` -> `'a = b'`
   - `qualityNote('disguised_missing', [{ column: 'x', count: 7, values: ['1', '2', '3', '4', '5', '6', '7'] }])` -> `'x: 1, 2, 3, 4, 5'`
   - `qualityNote('near_constant', 'not a list')` -> `''`
   - `qualityNote('unknown', [])` -> `''`
   If a literal result differs only by floating-point formatting, stop and report the actual
   value; do not change the function.

### Checks (implementer)

From the repo root: `make test-fast TESTS="tests/test_nodes_champion.py"`;
`.venv/bin/python -m ruff check`, `.venv/bin/python -m ruff format --check` and
`.venv/bin/python -m mypy` on `backend/statlab/domains/champion.py
tests/test_nodes_champion.py`. From `frontend/`: `npm test`, `npm run typecheck`,
`npm run build`. Receipt: changed files, commands, exits, results, deviations.
Stop and report on any failing check or missing helper; do not work around it.

Main then: review, `make check`, a browser look at a quality step on the offline stack if
practical, commit, mark chunk 63 completed.

### Addendum 63c-A (test parameter types)

mypy rejects `dict[str, list[str]]` / `list[str]` inside a `JsonValue` dict (invariance). In
`test_report_lists_data_quality_checks_and_fixes` annotate the parameters as
`mark_missing: dict[str, JsonValue]` and `merge_variants: list[JsonValue]` (both are
`JsonValue` members); the parametrize values and the summary dict stay unchanged.

### Stage 63c acceptance receipt (2026-10-09)

Implementer (after addendum A): `tests/test_nodes_champion.py` exit 0 (27 passed); Ruff
check/format and mypy on `champion.py` and the test file exit 0; `npm test` 99/99, typecheck
0, build 0. Main reviewed the full diff against the recipe (report section built only from a
completed `data_quality` boundary, capped names, escaped cells, inserted before the caveats;
card shown only for the `data_quality` step; icon). `make check` (`check63c.log`, temporary)
exit 0: coverage 100 % (TOTAL 12562 stmts, 0 missed), mypy 200 files, Ruff clean. No browser
check: the user's next run shows the step end to end.
