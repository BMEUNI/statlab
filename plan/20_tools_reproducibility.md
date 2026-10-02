---
status: completed
dependencies: [06, 07, 16, 19]
completion_date: 2026-10-02
implementation_notes: "export_pipeline_script + snapshot_run in mcp_server/.../reproducibility.py (stdlib only, phase=reproducibility, allow_in=[artifact_persistence]). LoggedCall added to contracts/.../reporting.py (frozen, extra=forbid). Driver in tests/test_skeleton.py records a LoggedCall per call. M7b gate in test_spine_reporting.py runs pipeline.py in a fresh subprocess (minimal env PATH+HOME, fresh empty cwd) for both fixtures: both reproduce the champion test metric exactly (diff 0.0, well under +/-0.005). compute_run_hash + prune_intermediate_artifacts NOT re-added (D-a). All 7 section-5 mutations KILLED. 100% line+branch; mypy/ruff clean. Not committed."
---

# Chunk 20 — Reproducibility tools (M7b)

**Goal.** Node 13's tools:
- `export_pipeline_script` writes a standalone `pipeline.py` that replays the champion's path and
  prints its test score;
- `snapshot_run` writes `manifest.json` with file hashes, package versions and the run hash.

M7b's gate: **`pipeline.py`, run in a fresh process on the CSV, reproduces the champion's test
primary metric within ±0.005 (§14.1)** for both skeleton fixtures.

Two catalogue tools are removed, and the catalogue goes from 86 to 84 (D-a).

Batch B5 runs this after chunk 19.

---

## 1. Prerequisites & Context

### State

Chunk 19 is in the working tree, uncommitted, with green gates; its report is passed to this
stage. The last commit is the B5 specs.

Read first:
- `tool.py` (the `server` object, `server.call_tool`, `server.list_tools`);
- `statlab_contracts/envelope.py` (`TOOL_RESULT_ADAPTER`);
- `statlab_contracts/artifacts.py` (`ARTIFACT_ID_PATTERN`);
- `statlab_contracts/layout.py` (`RunPaths.pipeline_py`, `manifest_json`, `ProjectPaths.datasets`);
- `backend/statlab/storage.py` (`prune_intermediates`, read only);
- `tests/test_skeleton.py` and `tests/test_spine_reporting.py`.

### Findings from probing (2026-10-02)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | A plain script that sets `STATLAB_DATA_DIR` (temp dir) and `STATLAB_MCP_TOKEN` *before* importing `statlab_tools`, imports `statlab_tools.tools`, then does `anyio.run(server.call_tool, name, {**args, "project": …, "run": …})` and validates `structured_content["result"]` with `TOOL_RESULT_ADAPTER`, replays `load_csv` → `split_stratified` → `train_estimator` → `predict_proba` → `score_classification` with wire-format arguments | The script replays through **the same argument validation and envelope as the live server**, without HTTP or auth. That matters: a direct body call skips pydantic coercion (chunk 16 D-a) |
| P2 | Two runs of that script under `env -i` and `-W error` print an identical ROC AUC | Exact reproduction; ±0.005 is the stated ceiling, not the expectation |
| P3 | The scorer took 3 s at the production `BOOTSTRAP_RESAMPLES = 1000`; the recorded test run used 200 (chunk 16 D-f) | Compare point values, never CIs; the script's runtime is a few seconds |

### Decisions this chunk makes

**D-a. Two catalogue tools go.**
- `prune_intermediate_artifacts` is already `statlab.storage.prune_intermediates` (chunk 06), with
  D24's success-only guard. Pruning is an engine step on node success, not an agent decision, and
  it sets `pruned_at` in a DB that the MCP server cannot reach. The engine calls it; this is noted
  in `00_index.md` row 22.
- `compute_run_hash` becomes the `run_hash` field of `snapshot_run`'s manifest. It has no other
  consumer, and both need the same normalisation (D-c).

**D-b. The tool log is orchestrator-supplied.** The MCP server has no DB, so chunk 29 passes
`calls: tuple[LoggedCall, ...]`, each holding `tool`, the `arguments` as sent (injected ones
included), and the `outputs` refs in envelope order. `LoggedCall` goes in
`statlab_contracts/reporting.py` (beside chunk 19's `ReportSection`), because the backend builds
it from `tool_calls`.

**D-c. The champion's path is the dependency slice of the target.** PLAN described selecting the
path by joining `node_executions.candidate_id`. A backward slice over refs is exact and simpler:
- start from the call that produced `target` (the champion's test score table);
- repeatedly add the latest earlier call that produced each ref an included call consumes.

This excludes other candidates' branches, node 6, profiling and `compare_models` automatically.
Interleaving does not matter (D29). A consumed ref that no earlier call produced is `tool_param`
("the log is incomplete"). Refs are recognised by `ARTIFACT_ID_PATTERN`, recursively through
lists and dict values. Chunk 29 may pass the whole run log.

**D-d. The script is unrolled, readable and injection-safe.**
- There is one statement per kept call: `r7 = _call("apply_scaler", scaler=r6.artifacts[0].ref,
  frame=r5.artifacts[0].ref)`.
- Tool names must be registered tools.
- Argument names must be identifiers in that tool's input schema, with `project` and `run`
  dropped.
- Values are rendered with `repr` over JSON types only, with refs replaced by `r{i}.artifacts[k].ref`.
- The contract snapshot is embedded as a `repr`'d JSON string. The dataset sha256 comes from the
  slice's single `load_csv` call (zero or several is `tool_param`).
- The source is checked with `compile()` before it is written. No data is ever `eval`'d.
- It contains no timestamps or absolute paths, so the same log always gives the same bytes.

**D-e. The run hash is content, not identity.** It is the sha256 of canonical JSON (sorted keys,
no whitespace) over:
- the dataset sha256;
- the parsed contract snapshot;
- every call with `project`/`run` dropped and each ref replaced by `"@{call}.{output}"`.

Two runs with the same data, contract and calls have the same hash, even though their refs
differ.

**D-f. The manifest never captures the environment.** It holds the Python version,
`platform.platform()`, and the versions (`importlib.metadata`) of `numpy`, `pandas`,
`scikit-learn`, `scipy`, `statsmodels`, `pyarrow`, `matplotlib`, `joblib`, `pydantic`, `mcp`
and `statlab-tools`, with `null` for a package that is not installed. It does **not** hold
environment variables: the bearer token must never reach a run folder (PLAN §11). The LLM model name is added by the backend (chunk 29). It also holds
a sha256 and size for every file under the run root except `manifest.json` itself, sorted by
relative path.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/reproducibility.py`: `export_pipeline_script`, `snapshot_run`, and the shared slice/normalise helpers |
| modify | `contracts/statlab_contracts/reporting.py`: `LoggedCall` (frozen, `extra="forbid"`) |
| modify | `tools/__init__.py`: one import line |
| modify | `tests/test_skeleton.py`: the `Driver` records a `LoggedCall` per call (tool, the injected arguments as sent, output refs). No other change |
| create | `tests/test_tools_reproducibility.py` (unit) |
| modify | `tests/test_spine_reporting.py`: the M7b gate (§4), for both fixtures |
| modify | this file's frontmatter; `00_index.md` row 20 completed, B5 and M7b ✅ (state "84-tool catalogue"); `WHATS_NEXT.md` state table (measured), "Next" → batch B6 (chunks 21 + 25), and a combined "Batch B5 — completed (summary)" absorbing chunk 19's section |

---

## 3. Implementation Spec

**Conventions:**
- `phase="reproducibility"`, `allow_in=["artifact_persistence"]`.
- Both tools are stdlib only: `hashlib`, `json`, `platform`, `importlib.metadata`, `ast`/`compile`.
- `calls` and `target` are `ORCHESTRATOR_SUPPLIED`.

### 3.1 `export_pipeline_script(calls: tuple[LoggedCall, ...] (min 1), target: ArtifactRef)`

- **Order:** validate (D-d) → slice (D-c) → render → `compile` → write `RunPaths.pipeline_py`.
- **Script header** (fixed text; it is the probe in P1):
  - a docstring with the run id, the champion target and usage `python pipeline.py DATA.csv`;
  - it needs the same `statlab_tools` version (see `manifest.json`);
  - `main(csv_path)` makes a temp data dir, sets the two env vars, then imports
    `statlab_tools.tools`;
  - it verifies `sha256(csv) == DATASET_SHA256` and exits non-zero with a clear message if not;
  - it copies the CSV to `datasets/<sha>.csv` and writes the embedded contract snapshot;
  - `_call` raises `SystemExit` naming the tool and the failure message.
- **Script body:**
  - the kept calls, each preceded by a comment naming its position in the original log;
  - the last line prints `json.dumps(r_t.summary, sort_keys=True)`, where `t` is the target's
    producer.
- **Summary:** `path` (`"pipeline.py"`), `n_calls`, `n_kept`, `tools` (kept, in order),
  `dataset_sha256`, and the script's `sha256`.

### 3.2 `snapshot_run(calls: tuple[LoggedCall, ...])`

- Writes `RunPaths.manifest_json` with `run_id`, `run_hash` (D-e), `python`, `platform`,
  `packages` and `files` (D-f).
- The summary has `run_hash`, `n_files` and `packages`.
- With no `load_csv` in `calls`, `run_hash` is `tool_param`: it has no data identity.

---

## 4. Test Suite Spec (~40)

**Slice.**
- A synthetic log of two candidates' interleaved branches keeps only the champion's branch plus
  the shared prefix, in original order.
- Node-6 and profiling calls are dropped.
- A ref consumed with no earlier producer is `tool_param` naming the call.
- A target that no call produced is `tool_param`.
- A ref inside a `candidates` dict value is followed.

**Render.**
- An unknown tool, an argument absent from the tool's schema, and a non-identifier argument name
  are each `tool_param`.
- Injection: a string argument containing `"); import os; os.system("x") #` parses back
  (`ast.literal_eval` of the rendered value) to exactly the original string, and the script's AST
  has no call outside `_call`/`main`'s fixed set.
- The same log gives byte-identical scripts twice. No absolute path from the test's `tmp_path`
  appears in the source.

**Run hash.**
- Two logs that differ only in ref values give the same hash.
- Changing one seed changes it; changing the contract changes it.

**Manifest.**
- The token's value (set via `STATLAB_MCP_TOKEN` in the test) is absent from `manifest.json`.
- Every file under the run root except the manifest is listed, with a correct sha256.
- An uninstalled package name gives `null` (monkeypatch `importlib.metadata.version`).

**M7b gate** (`tests/test_spine_reporting.py`, both fixtures, over MCP), after chunk 19's
node-12 flow:
- `export_pipeline_script(calls=driver.log, target=<champion test score table>)` and
  `snapshot_run(calls=driver.log)` succeed in `artifact_persistence`;
- the kept tools contain exactly one `train_estimator` and none of `compare_models`,
  `rank_models`, `fit_*`, `coefficient_table` or profiling;
- `subprocess.run([sys.executable, pipeline.py, csv], env=<minimal env: PATH, HOME>,
  timeout=300)` exits 0, and its last stdout line's `metrics[primary_metric].value` is within
  **±0.005** of the recorded test value (§14.1). The test docstring cites the tolerance, and the
  report gives the actual difference;
- a CSV with one byte changed makes the script exit non-zero with the sha message.

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 90 s. Coverage **100 %** line and branch. Prior tests
      pass, with edits only where §2 names them.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - the slice keeps every call;
  - the slice takes the *first* earlier producer instead of the latest;
  - refs are not normalised in the run hash;
  - argument values are rendered with `str()` instead of `repr()`;
  - the `compile()` check is removed and a broken render goes through (a test must feed one);
  - the sha256 check in the script header is removed;
  - the manifest includes `os.environ`.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

P1 or P2 do not reproduce; the gate's difference exceeds 0.005; anything needs `backend/` or a
`contracts/` change beyond `LoggedCall`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The generated classification `pipeline.py`, verbatim, plus the recorded and reproduced test
   values for both fixtures.
4. The manifest's `run_hash` and `packages` for one run.
5. For chunk 21: the exact `LoggedCall` the client must record per call.

---

## 6. Addendum — review corrections (2026-10-02)

- **My §4 wording was wrong:** "none of … `fit_*`" meant the inferential fits. The champion's
  transformer fits (`fit_imputer`, `fit_onehot_encoder`, `fit_scaler`) are on the test path and
  must be kept. The implementation keeps them and excludes `fit_logit`/`fit_ols`.
- The generated script read the CSV with a bare `open(...).read()`; it now uses a context
  manager.
- The generated classification `pipeline.py` was read in full: 15 kept calls out of 43 logged,
  one `train_estimator`, and the test branch replayed with the same fitted transformers.
  Reproduction is exact (difference 0.0) for both fixtures, under a minimal environment and an
  empty working directory.
- **Catalogue count:** 82 tools are served against the documented 84. The two missing are
  `split_temporal` and `split_grouped`, deferred by `PLAN.md` §6.4 "to M7" but never given a
  chunk (nor were the registry lines `ridge` and `mnlogit`). Recorded as an open scope gap in
  `WHATS_NEXT.md`; not closed here.
