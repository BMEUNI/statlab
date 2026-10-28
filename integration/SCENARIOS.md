# Integration scenarios — end-to-end backend runs

Purpose: prove the whole backend **runs as designed** end to end (real API, real MCP compute
server, real nodes, real LLM provider). Model accuracy is NOT the goal. Every expectation below
is behavioural: which steps run, which gates open, what gets stored, which invariants hold.
A few "soft" checks (planted signal found, metric in a plausible range) only produce warnings.

Run it (pytest; run from the repository root):

```sh
make itest-offline        # scripted stand-in LLM, no network, no cost
make itest-offline-rich   # same, but the stand-in also takes the optional branches
make itest-replay         # replay the recorded LLM answers from cassettes, no network, no cost
make itest-record         # PAID: real DeepSeek calls, recorded into integration/.cache/cassettes
# one scenario, or any pytest selection
.venv/bin/python -m pytest -c integration/pytest.ini integration/tests --llm replay -k S01
```

Options: `--llm {offline,offline-rich,record,replay,live}` (required), `--budget-usd 2.0`
(the suite stops starting scenarios once spend reaches it), `--log-debug`, `--cassettes DIR`,
`--record-missing` (replay only: call the real model for answers that are not recorded yet and
store them; paid, but only for the missing calls).

Test layers:

- `test_behaviour.py`: the must and soft checks below, per scenario (every mode).
- `test_judged.py`: LLM-as-judge quality checks with DeepEval, in record, replay and live modes.
  Tool correctness (expected MCP tools were called; deterministic), faithfulness (the report's
  claims are supported by the champion evidence) and rationale consistency (the champion choice
  follows the validation ranking and claims no test score). The judge is the same provider seam,
  so its answers are recorded and replayed too.
- `test_judge_unit.py`: the judge adapter itself, offline.

Replay serves each LLM call from `integration/.cache/cassettes/<scenario>/` by schema, step,
candidate and call number; a missing answer fails the run with "cassette miss". A changed prompt
still replays and shows as the soft check `H-drift`. S07, S08 and S16 trigger pause, cancel and
an API restart on mid-run events, so which LLM calls happen depends on timing: they are skipped
in replay and only run in live or record mode. `summary.md` in replay shows the recorded cost,
not spend.

Each invocation writes `integration/out/<UTC timestamp>-<mode>/` (gitignored):
`summary.md` (one table, pass/fail per scenario and check), `api.log`, `mcp.log`, and per
scenario a folder with `result.json`, the recorded SSE `events.jsonl`, `snapshot.json`, SQL
extracts (`node_executions.json`, `llm_calls.json`, `tool_calls.json`, `gates.json`), and copies
of `report.md`, `pipeline.py`, `manifest.json` when produced.

`api.log` is the backend log of the whole suite, in order. It contains:

- every LLM step (`LLM decide node=... candidate=... output=...`);
- the full prompt of every LLM request (`LLM request ...`, then `--- system ---` / `--- user ---`
  blocks);
- the raw reply (`LLM response ...`);
- every rejected reply with the validation errors sent back for repair (`LLM output rejected ...`);
- every tool call with its arguments and duration (`Tool call ...`);
- provider failures (`LLM request failed: <class> status=<code>`).

Add `--log-debug` to also log the full result of every tool call. The terminal prints one line per
run event (step started or finished, gate, status) and a line when each scenario starts and ends.
To follow a run in detail, run `tail -f integration/out/<folder>/api.log` in a second terminal.

The harness uses an isolated data directory and database inside that folder; it never touches
`projects/` or `statlab.db`. Machine-readable definitions: [scenarios.toml](./scenarios.toml).

The plain offline stand-in gives the smallest valid answer to every LLM question. The rich mode
(`offline-rich`) follows fixed rules instead:

- Profiling: picks columns and two plots.
- Training: grid-tunes the baseline.
- Retuning: retunes the baseline once.
- Evaluation: requests curves and a decision threshold for binary targets, and train CV plus a
  learning curve for the baseline.
- Interpretation: requests coefficients, partial dependence and error segments.
- Search: proposes one wave-2 variant of the baseline, except when a scenario pins its candidate
  count.
- Report: adds a "Stand-in methodology note" section.

Neither offline mode judges the data.

Accounts (chunk 71): the driver signs in once as `harness@example.test` (registers on a fresh
database, logs in otherwise) and sends `X-Statlab-Csrf: 1` on every request. It is the first
account, so it is admin; no budget limits are set, so budgets never stop a scenario.

## Checks applied to every scenario (unless the scenario says otherwise)

| ID | Check | Severity |
| --- | --- | --- |
| G1 | No ERROR-level record in `api.log` (log level, not message text); MCP process still alive at the end | must |
| G2 | Run reaches the scenario's expected terminal status within its timeout | must |
| G3 | `events.jsonl` sequence numbers are 1..N without gaps; every `node.started` has a matching `node.completed`/`failed`/`skipped` or the run ended paused/cancelled | must |
| G4 | No node execution left `running` or `pending` after a terminal status | must |
| G5 | At most one test-split scoring call per run (`score_*` on a test frame); none unless a champion was selected (D32) | must |
| G6 | Every `llm_calls` row belongs to the run and an execution of that run; total cost recorded | must |
| G7 | Scenario cost ≤ its `max_cost_usd` (live mode only) | must |

A failure in one model's own step ends only that model's branch; the others continue and
champion selection leaves it out (chunk 58). Any other unexpected node failure opens a failure
gate (also in autonomous mode); unless a scenario says otherwise the harness answers it with its
default `abort`, so the run ends `cancelled` and G2 fails with the failure message in the
scenario folder (`gates.json`).

## Cost controls (chunk 59)

The harness only proves the backend runs, so it buys no answer quality:

- `STATLAB_LLM_REASONING=false` for every harness run: DeepSeek thinking is off, so no hidden
  reasoning tokens.
- Live mode only: a scenario contract that does not set them gets `max_search_waves = 1` and
  `max_retune_loops = 0` (no second search wave, no retunes). Offline modes keep the defaults,
  so `offline-rich` still exercises waves and retunes.
- Per-model steps and chat questions see a digest of earlier steps without raw tool results
  (this applies to every run, not only the harness).
- No output-token cap: a cut-off JSON answer costs a repair call, which is more than it saves.

## Datasets (synthetic, deterministic, planted truth)

| Key | Rows | Task | Columns | Planted truth |
| --- | --- | --- | --- | --- |
| D1 `churn` | 500 | binary `churned` (0/1) | `subscriber_id`, `plan` (basic/standard/premium), `region` (north/south/east/west), `tenure_months`, `monthly_charges`, `support_calls` | churn rises for `basic` plan, falls with tenure, rises with support calls; `region` has NO effect |
| D2 `houses` | 400 | continuous `price` | `area_m2` (5% missing), `rooms`, `district` (a/b/c), `age_years` (3% missing), `has_garden` | price = area, rooms, district driven; `has_garden` has NO effect |
| D3 `churn_dirty` | 420 | binary `churned` | D1 + 20 exact duplicate rows, constant `country`, unique `customer_code`, 85%-empty `notes`, 60-level `city`, mixed `NA`/`N/A`/blank tokens, `?` as `plan` in every 25th row, upper-case `region` in every 20th row | same as D1; the dirt must be detected and handled, not crash the run |
| D4 `species` | 450 | multiclass `species` (3 classes) | `x1`..`x4` | class means differ on `x1`, `x3`; `x4` is pure noise |
| D5 `sales` | 365 | continuous `units` | `date` (daily 2025), `promo`, `temperature` | promo adds +20 units; weekly pattern; upward trend |
| D6 `visits` | 480 | binary `responded` | `patient_id` (80 patients × 6 visits), `age`, `dose`, `biomarker` | response driven by `dose` and `biomarker`; rows of one patient must never span splits |
| D7 `reviews` | 600 | binary `positive` (0/1) | `review_id`, `app_category` (games/tools/social), `rating_count`, `review` (2–8 words, ~10% typos) | sentiment comes only from the review words; `app_category` and `rating_count` have NO effect |
| R1 `adult` | 32,561 | binary `income` (`<=50K` / `>50K`, 24% positive) | UCI Adult / Census Income: age, workclass, fnlwgt, education, education_num, marital_status, occupation, relationship, race, sex, capital gain/loss, hours_per_week, native_country | real data, no planted truth; `?` missing tokens in workclass/occupation/native_country; 41-level native_country |
| R2 `autompg` | 398 | continuous `mpg` | UCI Auto MPG: cylinders, displacement, horsepower, weight, acceleration, model_year, origin (1/2/3), car_name | real data; 6 `?` horsepower values; 305 distinct car names (high-cardinality text) |

Real datasets (R1, R2) come from the UCI Machine Learning Repository (CC BY 4.0). The harness
downloads them once into `integration/.cache/` (gitignored), checks a pinned sha256 and converts
them to CSV with a header. The raw `?` tokens are kept; the contracts declare them as missing.

## Scenarios

"Prompt" = what we ask of the modelling agent: the data contract (task, metric, candidate
families, inference questions, notes), the run mode, gate answers and chat messages.

### S01 — Classification, autonomous, full path (baseline)
- **Input:** D1; contract: binary target `churned`, positive class 1, metric `roc_auc`,
  stratified split, candidates `logistic_regression`, `random_forest_classifier`,
  `hist_gb_classifier`, inferential model `logit`, inference goals "Does plan affect churn?"
  (`plan`), "Does region affect churn?" (`region`), `subscriber_id` as id column. Mode autonomous.
- **Expect:** status `completed`; all 6 shared nodes completed in spine order; ≥2 candidates
  completed all 5 per-candidate nodes; `champion_selection` and `artifact_persistence`
  completed; a champion model row; `test_scored_at` set once; no gates opened; `report.md`
  contains "Modelling report", "Validation comparison", "Inference"; `pipeline.py` replay
  reproduces the recorded test metric within ±0.005; `manifest.json` present.
- **Soft:** plan goal "reject H0", region goal "not rejected"; test roc_auc in [0.6, 1.0].

### S02 — Regression, autonomous, missing values
- **Input:** D2; target `price` continuous, metric `rmse`, random split, candidates
  `linear_regression`, `ridge`, `random_forest_regressor`, inferential `ols`, goal "Does garden
  affect price?" (`has_garden`); median imputation. Autonomous.
- **Expect:** as S01 (regression metric `rmse` in report); imputation tools appear in
  `tool_calls` (`fit_imputer`/`apply_imputer`).
- **Soft:** garden goal "not rejected"; test r2 (if reported) > 0.5.

### S03 — Dirty data is detected and handled
- **Input:** D3; contract as S01 (no inference goals), `drop_duplicates` and `drop_constant` on,
  `max_cardinality` 30. Autonomous.
- **Expect:** `completed`; no node failed; `drop_duplicates`/`drop_constant` honoured (the
  cleaning tools appear in `tool_calls`); `data_quality` completed with `check_data_quality`
  (offline the stand-in applies every reported fix, so `fix_data_quality` runs; live the fix
  choice is the model's); the report has a `Data quality` section.
- **Soft:** intake or profiling summaries mention the duplicates or the constant column.

### S04 — Guided mode, approve everything
- **Input:** D1, S01 contract; mode **guided**; harness answers every gate `approve`.
- **Expect:** `completed`; a `before` gate opened for every shared "always" node (intake,
  problem_definition, data_profiling, data_quality, data_splitting, experiment_design, inferential_analysis,
  champion_selection, artifact_persistence); every opened gate ends `applied`; events contain
  `gate.opened` and `gate.answered` pairs.

### S05 — Guided mode with edits, an instruction and a skip
- **Input:** D1, S01 contract; guided. Gate policy: `problem_definition` →
  `approve_with_edits` adding secondary metric `pr_auc`; `data_profiling` → `skip_node`
  (reason "profiling not needed for this test"); `experiment_design` → `instruct` "Use exactly
  two candidates: one logistic_regression and one random_forest_classifier." then `approve` the revised proposal;
  first per-candidate gate (if any) → `approve_all_remaining`.
- **Expect:** `completed`; `data_profiling` execution `skipped`; `data_quality` completed on
  the raw frame; experiment_design gate has
  revision ≥ 1; candidate set has exactly 2 families; `pr_auc` appears in the validation table;
  no further gates after `approve_all_remaining`.

### S06 — Guided abort
- **Input:** D1, S01 contract; guided; `data_splitting` gate → `abort`.
- **Expect:** status `cancelled`; no candidate executions; no test scoring; no champion.

### S07 — Lifecycle controls and chat
- **Input:** D1, S01 contract; autonomous. When the first `model_training` starts: `pause`;
  when status is `paused`: send message "What is the current status of the run?", then
  "Which candidate is performing best so far?", then `resume`.
- **Expect:** status reaches `paused`, then `completed`; both messages get a non-empty answer;
  answers cite only artifacts visible to the run; events contain `message.user` and
  `message.assistant.complete`.

### S08 — Cancel mid-run
- **Input:** D1, S01 contract; autonomous; `cancel` when the first `preprocessing` starts.
- **Expect:** status `cancelled`; no champion; no test scoring; no node left running.

### S09 — Rewind creates a fork
- **Input:** D1, S01 contract; autonomous; after `completed`, control `rewind` target
  `experiment_design` with request id `s09-fork`. Then repeat the same request.
- **Expect:** first request returns 201 with a new run id; the repeat returns the same child
  id (idempotent); a completed parent stays `completed` (only an unfinished parent becomes
  `superseded`, D36b); child completes; child report discloses the prior test exposure ("The
  test set has been scored N time(s) in prior runs"); child inherits shared results before
  `experiment_design` (no execution of intake..data_splitting in the child).

### S10 — Temporal split runs end to end
- **Input:** D5; target `units` continuous, metric `mae`, split strategy `temporal`, time column
  `date`, candidates `linear_regression`, `hist_gb_regressor`. Autonomous.
- **Expect:** the temporal split (chunk 77) orders train/valid/test by `date`; the run
  completes with a champion, one test score and a replayable `pipeline.py`.

### S11 — Grouped data is refused cleanly
- **Input:** D6; binary `responded`, metric `roc_auc`, split `grouped`, group column
  `patient_id`, candidates `logistic_regression`, `random_forest_classifier`. Autonomous.
- **Expect:** `data_splitting` refuses with a data-class error containing "no supported split";
  failure gate, `abort` → `cancelled`; no candidate executions, no test scoring.

### S12 — Multiclass
- **Input:** D4; target `species` multiclass, metric `balanced_accuracy`, stratified,
  candidates `logistic_regression`, `random_forest_classifier`, inferential `mnlogit`, goal
  "Does x4 matter?" (`x4`). Autonomous.
- **Expect:** `completed`; report table has the multiclass metrics; inference runs `mnlogit`.
- **Soft:** x4 goal "not rejected".

### S13 — Fit budget exhaustion
- **Input:** D1, S01 contract with `constraints.max_total_fit_seconds` = 1. Autonomous.
- **Expect:** terminal status (`completed` or `failed`) without a crash; at least one
  `Fit budget exhausted` failure or a `budget_exhausted` branch; no ERROR log; if `failed`,
  the failure is a recorded node failure, not an execution-task crash.

### S14 — Contract proposed by the agent
- **Input:** D1; upload only `{target: {column: "churned"}}`; call `contracts/propose` with it;
  upload the proposed contract; validate it; start autonomous run.
- **Expect:** proposal is a complete contract (target type, task, metric, split strategy set)
  with a non-empty reasoning; validation `valid: true`; run `completed`.

### S15 — Project memory across runs
- **Input:** run S01's setup twice in the same project, sequentially.
- **Expect:** both `completed`; after run 1 the project has a `run_outcome` memory row; run 2's
  config carries that memory; run 2 prompts/report do not crash on it.

### S16 — API restart mid-run
- **Input:** D1, S01 contract; autonomous. When the first `model_evaluation` completes, stop
  the API (graceful shutdown), start a fresh API on the same database and data directory; if
  the run is `paused` send `resume`.
- **Expect:** run `completed` after restart; no execution duplicated as two `completed` rows for
  the same node/candidate/attempt; restart did not re-run completed shared nodes. The one
  exception is `experiment_design`, which may complete once per search wave, because each new
  wave re-confirms the cumulative shortlist.

### S17 — Invalid setup is rejected cleanly (no LLM)
- **Input:** D1. (a) contract whose target column `does_not_exist`; (b) incomplete contract
  (target only) used to create a run; (c) non-CSV upload (`b"\x00\x01binary"` as `data.csv`).
- **Expect:** (a) validate returns `valid: false` with a target mismatch and run creation is
  422; (b) run creation 422 "contract is missing required setup fields"; (c) upload rejected
  with 4xx; zero runs created; zero `llm_calls`.

### S18 — Real data: UCI Adult income (binary, messy categoricals)
- **Input:** R1; `na_values` `?`; binary target `income`, positive class `>50K`, metric
  `roc_auc`, stratified split, `fnlwgt` excluded (census sampling weight, not a feature),
  candidates `logistic_regression`, `random_forest_classifier`, `hist_gb_classifier`,
  inferential `logit`, goals "Does sex affect income?" (`sex`), "Does education_num affect
  income?" (`education_num`). Autonomous.
- **Expect:** as S01 (completed, champion, one test score, report sections, pipeline replay,
  manifest). Timeout 40 min (32k rows). `max_total_fit_seconds` 3600: the fit budget counts wall
  time of fit calls, so three parallel candidates on 32k rows can pass the default 1200 s on a
  busy machine.
- **Soft:** test roc_auc ≥ 0.8; sex goal "reject H0".

### S19 — Real data: UCI Auto MPG (regression, missing horsepower)
- **Input:** R2; `na_values` `?`; target `mpg` continuous, metric `rmse`, random split, `origin`
  declared categorical, `car_name` left in on purpose; candidates `linear_regression`, `ridge`,
  `random_forest_regressor`, inferential `ols`, goal "Does origin affect mpg?" (`origin`).
  Autonomous.
- **Expect:** as S01 with `rmse` in the report; imputation tools appear in `tool_calls`.
- **Soft:** test rmse ≤ 5.0.

### S20 — Text column: compare text methods (chunk 82)
- **Input:** D7; `review` declared text; binary target `positive` (positive class `1`), metric
  `f1`, stratified split, `review_id` excluded; candidate `logistic_regression`, inferential
  `logit`, goal "Does app_category affect sentiment?" (`app_category`). Autonomous. Offline
  hint: one logistic-regression candidate per text method (drop, lexicon, tfidf, hashed_chars).
- **Expect:** completed, at least 4 completed candidates (offline proposes exactly one per text method; a live LLM may add more), champion, one test score, text tools used
  (`fit_text_vectorizer`, `apply_text_vectorizer`, `drop_columns`), pipeline replay, manifest.
- **Soft:** test f1 ≥ 0.7; category goal "not rejected".

## Offline UI stack

`.venv/bin/python -m integration.serve_ui`

The stand-in LLM answers every agent call for free; use the printed sample CSVs (churn works with the default scenario S01).

## How failures are fixed

The harness never hides a failure. After a run, `summary.md` lists each failed check with the
evidence path. Fixes follow the project workflow (AGENTS.md): planner diagnoses, implementer
fixes, focused tests added, then the scenario is re-run. Offline first (free), then live.
