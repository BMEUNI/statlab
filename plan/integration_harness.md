---
status: in_progress
dependencies: [B9-B14 offline acceptance (plan/receipts/B9-B14-offline.md)]
completion_date: null
implementation_notes: Scenario harness for end-to-end runs; stages H1 (framework + LLM-free smoke), H2 (offline LLM stand-in), H3 (restart/propose/memory/fork scenarios), then offline dry-run and live run with fix loop.
---

# Integration scenario harness

User request (2026-10-07): a script plus predefined scenarios (dataset, contract/"prompt", run
mode, gate answers, chat, expected outcome) that drive the REAL backend end to end, run by Claude,
failures fixed one by one. Decisions: $2 budget per live suite, synthetic datasets with planted
truth, offline dry-run before live. Goal = correct behaviour, NOT model accuracy.
User-facing catalogue (authoritative for scenario content): [integration/SCENARIOS.md](../integration/SCENARIOS.md).

Non-goals: no pytest tests for the harness, no coverage requirement (outside
`[tool.coverage.run] source`), no backend/mcp_server/contracts source changes in H1–H3, no
frontend. Harness is a script; it is never collected by pytest (`testpaths = ["tests"]`).

## Architecture (all stages)

Package `integration/` at repo root (importable from repo root: `python -m integration.run_scenarios`).
- `integration/__init__.py` — empty.
- `integration/scenarios.toml` — machine definitions transcribed from SCENARIOS.md.
- `integration/model.py` — Pydantic scenario schema + loader.
- `integration/datasets.py` — deterministic synthetic CSV generators.
- `integration/services.py` — MCP subprocess + in-process API server (uvicorn on loopback).
- `integration/driver.py` — HTTP setup, run loop, SSE recorder, gate policy, actions.
- `integration/evidence.py` — read-only SQLite/file evidence collection.
- `integration/checks.py` — global (G1–G7), expectation and soft checks.
- `integration/run_scenarios.py` — CLI entry point, workspace, budget, summary.
- `integration/out/` — outputs, gitignored.

Process model: one MCP subprocess and one in-process API (uvicorn task) per suite invocation,
both bound to 127.0.0.1 on free ports, isolated data dir and DB under the output folder.
Scenarios run sequentially. The API runs in the harness process so H2 can inject the offline LLM
through `create_app(settings, llm_client=LlmClient(settings, acompletion=...))`; live mode passes
`llm_client=None` (production `LlmClient(settings)`, real litellm). Run creation copies
`./uv.lock`, so the harness must run with cwd = repo root (check at startup: `Path("uv.lock")`
is a non-empty file, else exit 2 with "run from the repository root").

## Stage H1 — framework, data, LLM-free smoke (S17) and scenario definitions

Owned files: everything under `integration/` except `SCENARIOS.md`; `.gitignore` (add one line
`integration/out/`); `pyproject.toml` (append `"integration"` to `[tool.mypy] files`).
Reuse: `statlab.main.create_app`, `statlab.config.Settings`, `statlab.llm.client.LlmClient`
(type only in H1), `statlab_contracts.contract.DataContract`,
`statlab_contracts.gates.GateAction`, `statlab_contracts.artifacts.ARTIFACT_ID_PATTERN`,
`statlab.persistence.models.RunStatus` and `RunMode` (Literal aliases, verified).

### model.py

Frozen Pydantic models, `ConfigDict(frozen=True, extra="forbid")`, tuples not lists.
- `DatasetKey = Literal["churn", "houses", "churn_dirty", "species", "sales", "visits"]`.
- `GateRule`: `node: str`; `phase: Literal["before", "after", "failure"] | None = None`;
  `action: GateAction`; `edits: dict[str, JsonValue] = {}`; `instruction: str | None = None`;
  `reason: str | None = None`; `target: str | None = None`; `once: bool = True`.
- `Trigger`: `event: str | None = None`; `node: str | None = None`; `status: RunStatus | None =
  None`. After-validator: exactly one of `event`/`status` is set; `node` only with `event`.
- `Action`: `when: Trigger`; `do: Literal["pause", "resume", "cancel", "message", "rewind"]`;
  `text: str | None = None` (required iff `message`); `target: str | None = None` and
  `request_id: str | None = None` (both required iff `rewind`). After-validator enforces these.
- `Expect`: `status: RunStatus | None = None`; `nodes_completed: tuple[str, ...] = ()`;
  `nodes_skipped: tuple[str, ...] = ()`; `nodes_failed: tuple[str, ...] = ()`;
  `nodes_not_run: tuple[str, ...] = ()`; `failure_contains: str | None = None`;
  `min_candidates_completed: int = 0`; `candidate_count: int | None = None`;
  `no_gates: bool = False`; `gates_opened_for: tuple[str, ...] = ()`;
  `gate_revision_min: dict[str, int] = {}`; `champion: bool | None = None`;
  `test_scored: bool | None = None`; `report_contains: tuple[str, ...] = ()`;
  `metrics_in_report: tuple[str, ...] = ()`; `pipeline_replay: bool = False`;
  `manifest: bool = False`; `tools_used: tuple[str, ...] = ()`;
  `events_include: tuple[str, ...] = ()`; `messages_answered: int = 0`.
- `Soft`: `inference: dict[str, Literal["reject H0", "not rejected"]] = {}`;
  `metric_min: float | None = None`; `metric_max: float | None = None`;
  `summary_mentions: tuple[str, ...] = ()`.
- `Setup`: `invalid_cases: bool = False`.
- `Scenario`: `id: str = Field(pattern=r"^S\d{2}$")`; `title: str`; `dataset: DatasetKey`;
  `mode: RunMode = "autonomous"`; `contract: dict[str, JsonValue]`; `setup: Setup = Setup()`;
  `gates: tuple[GateRule, ...] = ()`; `actions: tuple[Action, ...] = ()`; `expect: Expect =
  Expect()`; `soft: Soft = Soft()`; `timeout_s: int = Field(default=1200, gt=0)`;
  `max_cost_usd: float = Field(default=0.25, gt=0)`.
- `load_scenarios(path: Path) -> tuple[Scenario, ...]`: `tomllib.load`; top-level array key
  `scenario`; validate each with `Scenario.model_validate`; duplicate ids → `ValueError("duplicate
  scenario id <id>")`.

### datasets.py

Stdlib only (`random.Random(seed)`, `math`, `csv`, `datetime`). Public:
`generate(key: DatasetKey) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]` (header,
rows as strings) and `write_csv(key: DatasetKey, path: Path) -> Path` (csv.writer, `\n` line
terminator, UTF-8). Numbers rendered with `str()` after the stated rounding; ints as `str(int)`.
Bernoulli: `1 if rng.random() < p else 0`, `p = 1 / (1 + math.exp(-logit))`. Draw order per row
is exactly as listed (determinism).
- `churn` (seed 101, n 500): `plan = rng.choices(["basic","standard","premium"],
  weights=[40,35,25])[0]`; `region = rng.choice(["north","south","east","west"])`;
  `tenure = rng.randint(1, 72)`; `charges = round(min(150.0, max(10.0, rng.gauss(60, 15))), 2)`;
  `calls = rng.choices(range(10), weights=[25,20,15,12,10,7,5,3,2,1])[0]`;
  `logit = -0.5 + 1.4*(plan=="basic") + 0.5*(plan=="standard") - 0.04*tenure + 0.3*calls`;
  churned Bernoulli. Columns `subscriber_id` (`f"S{i:05d}"`, i from 1), `plan`, `region`,
  `tenure_months`, `monthly_charges`, `support_calls`, `churned`.
- `churn_dirty` (seed 103, n 400): per row the churn draws above (same order, this seed), then
  `notes_draw = rng.random()`, `notes_pick = rng.choice(["vip","late payer","moved"])`,
  `city = f"city_{rng.randint(1, 60):02d}"`, `na_draw = rng.random()`,
  `na_token = rng.choice(["NA","N/A",""])`. `notes = notes_pick if notes_draw >= 0.85 else ""`;
  `monthly_charges` replaced by `na_token` when `na_draw < 0.05`. `country = "HU"`;
  `customer_code = f"C{i:06d}x"` (unique). Columns: churn columns + `country`, `customer_code`,
  `notes`, `city` (appended in that order before `churned`; `churned` stays last). Then append
  copies of rows 1..20 at the end (420 rows).
- `houses` (seed 102, n 400): `area = round(rng.uniform(30, 200), 1)`; `rooms = rng.randint(1,
  6)`; `district = rng.choice(["a","b","c"])`; `age = rng.randint(0, 80)`; `garden =
  rng.choice(["yes","no"])`; `price = round(1500*area + 20000*rooms + {"a":50000,"b":0,
  "c":-30000}[district] - 300*age + rng.gauss(0, 20000))` (int); `area_missing = rng.random() <
  0.05`; `age_missing = rng.random() < 0.03`; render missing as `""`. Columns `area_m2`, `rooms`,
  `district`, `age_years`, `has_garden`, `price`.
- `species` (seed 104): 150 rows per class in order setosa_like, versicolor_like,
  virginica_like with means `x1` (5.0, 5.9, 6.6) sd 0.35 and `x3` (1.5, 4.3, 5.6) sd 0.4; per row
  draw `x1`, `x2 = gauss(3.0, 0.4)`, `x3`, `x4 = gauss(1.0, 0.5)`, round 2; then
  `rng.shuffle(rows)`. Columns `x1`..`x4`, `species`.
- `sales` (seed 105, n 365, i from 0): `date = (date(2025,1,1) + timedelta(days=i)).isoformat()`;
  `promo = 1 if rng.random() < 0.25 else 0`; `temp = round(10 + 12*math.sin(2*math.pi*(i-100)/365)
  + rng.gauss(0, 3), 1)`; `units = round(100 + 20*promo + (0,2,4,6,10,25,15)[weekday] + 0.1*i +
  0.5*temp + rng.gauss(0, 5))`. Columns `date`, `promo`, `temperature`, `units`.
- `visits` (seed 106): patients 1..80; per patient `age = rng.randint(30, 80)`, `base =
  rng.gauss(0, 1)`; 6 visits each: `dose = rng.choice([0,10,20,40])`, `biomarker = round(base +
  rng.gauss(0, 0.5), 3)`, `logit = -2 + 0.06*dose + 1.2*biomarker`, responded Bernoulli. Columns
  `patient_id` (`f"P{p:03d}"`), `age`, `dose`, `biomarker`, `responded`.

### services.py

- `free_port() -> int`: bind `socket.socket()` to `("127.0.0.1", 0)`, read port, close.
- `class McpProcess`: `__init__(self, *, data_dir: Path, token: str, log_path: Path)`.
  `start(self) -> str`: port = `free_port()`; env = `os.environ` copy plus
  `STATLAB_MCP_TOKEN=token`, `STATLAB_DATA_DIR=str(data_dir)`, `STATLAB_MCP_HOST=127.0.0.1`,
  `STATLAB_MCP_PORT=str(port)`, `STATLAB_FIT_PERMITS=1`; `subprocess.Popen([sys.executable, "-m",
  "statlab_tools"], env=env, stdout=log file, stderr=subprocess.STDOUT)` (log opened `ab`);
  poll `httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)` every 0.25 s up to 60 s until
  status 200; if the process exits or time runs out: `stop()` then `RuntimeError(f"MCP server did
  not become healthy; see {log_path}")`. Returns `f"http://127.0.0.1:{port}/mcp"`.
  `alive(self) -> bool` (`poll() is None`). `stop(self)`: terminate, wait 30 s, kill+wait if
  needed; idempotent.
- `class ApiServer`: `__init__(self, *, settings: Settings, llm_client: LlmClient | None)`.
  `async start(self) -> str`: `app = create_app(settings, llm_client=llm_client)`;
  `uvicorn.Config(app, host="127.0.0.1", port=settings.api_port, log_config=None,
  access_log=False, lifespan="on")`; `server = uvicorn.Server(config)`; `self._task =
  asyncio.create_task(server.serve())`; wait (0.1 s steps, 120 s) until `server.started` or the
  task is done; task done → re-raise its exception or `RuntimeError("API stopped during
  startup")`. Return `f"http://127.0.0.1:{settings.api_port}"`. `async stop(self)`:
  `server.should_exit = True`; `await asyncio.wait_for(self._task, 120)`; idempotent.
- `build_settings(*, token: str, mcp_url: str, data_dir: Path, db_path: Path, api_port: int) ->
  Settings`: `Settings(mcp_token=SecretStr(token), mcp_url=mcp_url, data_dir=data_dir,
  db_path=db_path, api_port=api_port)` (field names via `populate_by_name`; `max_concurrent_runs`
  stays default 0 = unlimited, scenarios are sequential anyway).
- Provider env: `create_app` lifespan runs `check_provider_environment` (local, no network) and
  then `os.environ.update(environment)` with keys from `./.env`. Start MCP BEFORE the API so the
  MCP child env never holds provider keys. Never log `os.environ`, settings dumps or request
  headers.

### driver.py

`class Driver`: `__init__(self, *, base_url: str, workspace: Workspace)` creates
`httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(120, read=300))`; `async close()`.
`Workspace` (dataclass in `run_scenarios.py` or `driver.py`): `root`, `data_dir`, `db_path`,
`api_log`, `mcp_log`; `scenario_dir(id) -> Path` creates `root / id`.
Every HTTP call goes through `async _call(method, path, *, expected: set[int], **kwargs) ->
httpx.Response`; status not in `expected` → raise `HarnessError(f"{method} {path} -> {status}:
{body[:500]}")`. Every call is appended to `self.http_log: list[dict[str, JsonValue]]` (method,
path, status, elapsed_ms) for `result.json`.

Normal setup `async setup(scenario, scenario_dir) -> SetupResult` (project_id, dataset_id,
contract_id, validation JSON):
1. `POST /api/projects` `{"slug": scenario.id.lower(), "name": scenario.title}` → 201.
2. `csv = write_csv(scenario.dataset, scenario_dir / "data.csv")`; `POST
   /api/projects/{pid}/datasets` `files={"file": ("data.csv", bytes, "text/csv")}` → 201;
   `dataset_id = json["id"]`.
3. `contract = DataContract.model_validate(scenario.contract)`; `POST
   /api/projects/{pid}/contracts/validate` `{"dataset_id", "contract":
   contract.model_dump(mode="json")}` → 200; `valid is False` → `HarnessError("setup validation
   failed: <mismatches>")`.
4. `POST /api/projects/{pid}/contracts` `files={"file": ("contract.json",
   contract.model_dump_json(), "application/json")}` → 201; `contract_id = json["id"]`.
5. `POST /api/projects/{pid}/runs` `{"mode": scenario.mode, "dataset_ids": [dataset_id],
   "contract_id": contract_id}` → 201; return `run_id`.

`async run(scenario, run_id, scenario_dir, *, budget: Budget) -> RunOutcome`:
- SSE recorder task: loop `client.stream("GET", f"/api/runs/{run_id}/events", params={"since":
  last_seq}, timeout=httpx.Timeout(30, read=None))`; parse lines: `id: N` sets pending seq,
  `data: <json>` sets pending payload, blank line emits `{**payload}` (payload already contains
  `seq`, `type`, `payload`), lines starting `:` ignored. Append each new event (seq > last_seq)
  to `self.events` and to `scenario_dir / "sse_events.jsonl"`. On `httpx.HTTPError` or stream
  end, if not stopping: sleep 1 s and reconnect with `since=last_seq`. Cancelled at the end.
- Poll loop, every 1.0 s: `GET /api/runs/{run_id}` → 200 snapshot; `status =
  snapshot["run"]["status"]`; write latest snapshot to `scenario_dir / "snapshot.json"`.
  a) Gates: for each `gate` in `snapshot["gates"]` with `status == "pending"`: `question =
     json.loads(gate["question_json"])`; key `(gate["id"], question["revision"])` skipped if
     already answered. Rule = first `GateRule` with `node == question["node"]`, `phase in (None,
     question["phase"])`, and (`not once` or not yet used). No rule → action
     `"approve"` if `"approve"` in `question["options"]` else `question["default"]`. Submit
     `POST /api/runs/{run_id}/gates/{gate_id}` with `{"revision": question["revision"],
     "action": ..., "edits": rule.edits, "instruction", "reason", "target"}` (omit `None`
     keys; `edits` only for `approve_with_edits`) → expected {200}. Record
     `{gate_id, node, phase, revision, action, response}` in `outcome.gate_answers`.
  b) Actions: the first not-yet-fired action whose trigger matches fires (at most one per poll).
     Event trigger: some recorded SSE event has `type == event` and (`node is None` or
     `payload.get("node") == node`). Status trigger: `status == trigger.status`. `pause` /
     `resume` / `cancel` → `POST /api/runs/{run_id}/control` `{"action": do}` expected {200};
     `message` → `POST /api/runs/{run_id}/messages` `{"text": text}` expected {200}; `rewind` →
     control with `target`, `request_id` expected {200, 201}. Record `{index, do, status_code,
     body}` in `outcome.actions`; message responses also in `outcome.messages`.
  c) Exit when `status in {"completed", "failed", "cancelled", "superseded"}` and no unfired
     action has a status trigger equal to `status`.
  d) Timeout (`elapsed > scenario.timeout_s`): best-effort cancel (ignore errors),
     `outcome.timed_out = True`, exit.
  e) Budget: `budget.spent()` (read-only `SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls` on
     the suite DB) `>= budget.limit` → best-effort cancel, `outcome.budget_stopped = True`, exit.
- After exit: sleep 2 s, stop the SSE task, return `RunOutcome` (dataclass: `run_id`,
  `final_status`, `timed_out`, `budget_stopped`, `gate_answers`, `actions`, `messages`,
  `events`, `started_at`, `ended_at`).

Invalid-setup routine `async invalid_setup(scenario, scenario_dir) -> list[CheckResult]` (S17):
project + dataset as steps 1–2; (a) validate `scenario.contract` → expect `valid is False`;
upload it; create run → expect 422; (b) upload `{"target": {"column": "churned"}}` as
`incomplete.json`; create run → expect 422 and detail contains "missing required setup fields";
(c) upload `b"\x00\x01binary"` as `bad.csv` → expect status 400–499; then `GET
/api/projects/{pid}` → `runs == []`; read-only SQL `SELECT COUNT(*) FROM llm_calls` == 0. Each
lettered item and the two counts become one `CheckResult` (`S17-a`, `S17-b`, `S17-c`,
`S17-runs`, `S17-llm`), severity must.

### evidence.py

`collect(*, db_path, data_dir, slug, run_id, scenario_dir) -> Evidence` (dataclass). Read-only
connection `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`, `row_factory = sqlite3.Row`,
closed in `finally`. Rows as dicts: `runs` (the run), `node_executions`, `llm_calls`,
`tool_calls`, `gates`, `models`, `artifacts` (each `WHERE run_id = ?`, `ORDER BY rowid`); write
each list to `scenario_dir / f"{table}.json"` (indent 2, `default=str`). Run dir =
`data_dir / slug / "runs" / run_id`: parse `events.jsonl` (one JSON per line), `state.json` if
present; copy `report/report.md`, `pipeline.py`, `manifest.json`, `contract.snapshot.json`
into `scenario_dir` when present (fields hold their text or `None`).

### checks.py

`CheckResult(id: str, description: str, severity: Literal["must", "soft"], passed: bool,
detail: str)` frozen dataclass.
- `global_checks(*, scenario, outcome, evidence, api_log_slice: str, mcp_alive: bool, live:
  bool) -> list[CheckResult]`:
  G1 `" ERROR "` not in `api_log_slice` and `mcp_alive`. G2 not timed out, not budget stopped,
  `outcome.final_status == expect.status` (skip → pass when `expect.status is None`). G3 server
  events seq == 1..N; SSE events deduplicated by seq equal server events by (seq, type); if final
  status `completed`, every `node.started` `execution_id` has a later `node.completed`/
  `node.failed`/`node.skipped` with that `execution_id`. G4 terminal final status → no
  `node_executions.status` in (`running`, `pending`). G5 test scorings = `tool_calls` with
  `tool_name` starting `score_` where some string value anywhere in `json.loads(args_json)`
  (recursive walk of dicts/lists) equals the `id` of an `artifacts` row of this run with
  `split == "test"` or `derived_from_test == 1`; count ≤ 1; count == 1 iff
  `runs.champion_model_id` not null. G6 every `llm_calls` row has the run id and a
  `node_execution_id` among this run's executions. G7 live: `sum(cost_usd) <=
  scenario.max_cost_usd`; offline/none: pass with detail "not live".
- `expectation_checks(*, scenario, outcome, evidence, scenario_dir) -> list[CheckResult]`, one
  must-check per set field (`E-status` is G2, so skip it here):
  `nodes_completed`/`nodes_skipped`/`nodes_failed`: some execution row with that `node_name` and
  status; `nodes_not_run`: no execution row for that node; `failure_contains`: some failed
  execution's `error` text contains it; `min_candidates_completed`: count of
  `state.branches[*].status == "completed"` ≥ N (state from evidence `state.json`);
  `candidate_count`: `len(branches) == N`; `no_gates`: `gates == []`; `gates_opened_for`: a gate
  whose parsed question `node` equals each name; `gate_revision_min`: max `question.revision`
  over that node's gates ≥ value; `champion`: `(champion_model_id is not None) == value`;
  `test_scored`: `(test_scored_at is not None) == value`; `report_contains` /
  `metrics_in_report`: report text exists and contains each string; `manifest`: manifest text
  exists; `tools_used`: each in the set of `tool_calls.tool_name`; `events_include`: each type in
  server events; `messages_answered`: count of `outcome.messages` with non-empty `text` ≥ N;
  `pipeline_replay`: see below.
- Recorded test metric (helper `recorded_test_metric(evidence) -> float | None`): primary metric =
  `json.loads(contract_snapshot)["objective"]["primary_metric"]`; the `node_executions` row with
  `node_name == "champion_selection"` and `status == "completed"` (last by rowid);
  `json.loads(summary)["test_metrics"]["metrics"][metric]["value"]`; any missing key/row → None
  (source: `backend/statlab/domains/champion.py` summary keys `test_metrics`; score payload
  `metrics` in `mcp_server/statlab_tools/tools/evaluation.py:404`).
- Pipeline replay: recorded value as above (None → fail with detail "no recorded test metric"); run
  `subprocess.run([sys.executable, pipeline_path, data_csv], env={"PATH", "HOME"} only,
  cwd=tempfile.mkdtemp(), capture_output=True, text=True, timeout=300)`; last stdout line JSON
  `["metrics"][metric]["value"]`; pass iff return code 0 and `abs(diff) <= 0.005`; detail holds
  both values or stderr tail (last 2000 chars).
- `soft_checks(*, scenario, evidence) -> list[CheckResult]` severity soft: `inference`: find the
  report line starting `f"| {question} |"`; pass iff its last non-empty cell (split on `|`,
  stripped) equals the expected text; `metric_min`/`metric_max`: `recorded_test_metric`
  within bounds; `summary_mentions`: each string (case-insensitive) appears in some
  `node_executions.summary` text.

### run_scenarios.py

`main(argv: Sequence[str] | None = None) -> int`; `if __name__ == "__main__": raise
SystemExit(main())`. Args: `--llm` choices `("live",)` (H2 adds `offline`), required;
`--only` (nargs `*`, scenario ids); `--budget-usd` float default 2.0; `--scenarios` Path default
`integration/scenarios.toml`. Steps:
1. Repo-root check (`uv.lock`). Load scenarios; filter `--only` (unknown id → exit 2).
2. Workspace `integration/out/<UTC %Y%m%dT%H%M%SZ>-<llm>/`; `data/`, `statlab.db`, `api.log`,
   `mcp.log`. Logging: root logger level INFO, `FileHandler(api.log)` format `%(asctime)s
   %(levelname)s %(name)s %(message)s`; a console handler at WARNING.
3. `token = secrets.token_urlsafe(32)`; start MCP; `api_port = free_port()`; settings; start API
   (`llm_client=None` for live). Driver.
4. For each scenario: record `api.log` size before; `try:` invalid setup routine if
   `setup.invalid_cases`, else setup → run → evidence → global + expectation + soft checks;
   `except HarnessError | httpx.HTTPError as exc:` add must-check `H-error` failed with
   `str(exc)`; `api_log_slice` = bytes appended to `api.log` during the scenario; write
   `scenario_dir / "result.json"` (scenario id/title, run id, final status, duration, cost,
   checks, outcome lists, http log). Budget stop → mark remaining scenarios `skipped (budget)`
   and stop the loop.
5. `finally`: driver close, API stop, MCP stop.
6. `summary.md`: header (mode, `git rev-parse HEAD` output, start/end UTC, total cost and call
   count from read-only SQL); table `| Scenario | Title | Result | Must | Soft warnings | Cost
   USD | Duration s |` (Result = PASS/FAIL/SKIPPED); then per failing scenario the failed
   checks with detail and the scenario folder path. Print the summary path. Exit 0 iff every
   non-skipped scenario passed all must checks, else 1.

### scenarios.toml (H1 content)

Transcribe from SCENARIOS.md: S01–S08, S10–S13, S17 (S09 and S14–S16 come in H3). Contracts as
TOML tables mirroring `DataContract` JSON (e.g. `[scenario.contract.target]`, nested
`[[scenario.contract.objective.inference_goals]]`). Use exactly:
- Common classification contract (S01, S04–S08, S13 with its constraint): target `churned`,
  `type = "binary"`, `positive_class = 1`; `features.id_columns = ["subscriber_id"]`; objective
  `task = "classification"`, `primary_metric = "roc_auc"`, goals ("Does plan affect churn?",
  `["plan"]`) and ("Does region affect churn?", `["region"]`); split `strategy = "stratified"`,
  `seed = 42`; modelling candidates `["logistic_regression", "random_forest_classifier",
  "hist_gb_classifier"]`, `inferential_model = "logit"`.
- S01 expect: status completed; nodes_completed = the 6 shared nodes + `champion_selection`,
  `artifact_persistence`; `min_candidates_completed = 2`; `no_gates = true`; `champion = true`;
  `test_scored = true`; `report_contains = ["Modelling report", "Validation comparison",
  "Inference"]`; `pipeline_replay = true`; `manifest = true`. Soft: inference plan "reject H0",
  region "not rejected"; `metric_min = 0.6`. `max_cost_usd = 0.25`.
- S02: dataset houses; target `price` `type = "continuous"`; objective regression `rmse`, goal
  ("Does garden affect price?", `["has_garden"]`); split `random`, seed 42; preprocessing
  `missing.default = "median"`; candidates `["linear_regression", "ridge",
  "random_forest_regressor"]`, inferential `ols`. Expect as S01 plus `tools_used =
  ["fit_imputer", "apply_imputer"]`, `metrics_in_report = ["rmse"]`. Soft: garden "not
  rejected".
- S03: dataset churn_dirty; S01 contract without inference goals and without
  `inferential_model`; preprocessing `drop_duplicates = true`, `drop_constant = true`,
  `max_cardinality = 30`. Expect completed, `nodes_failed` unset, `champion = true`,
  `tools_used = ["drop_duplicate_rows"]`. Soft `summary_mentions = ["duplicate"]`.
- S04: mode guided; expect completed, `gates_opened_for` = intake, problem_definition,
  data_profiling, data_splitting, experiment_design, inferential_analysis, champion_selection,
  artifact_persistence; `events_include = ["gate.opened", "gate.answered"]`; `champion = true`.
  `max_cost_usd = 0.30`.
- S05: guided; gates: problem_definition `approve_with_edits` edits `{secondary_metrics =
  ["pr_auc"]}`; data_profiling `skip_node` reason "profiling not needed for this test";
  experiment_design phase before `instruct` "Use only logistic_regression and
  random_forest_classifier."; preprocessing `approve_all_remaining`. Expect completed,
  `nodes_skipped = ["data_profiling"]`, `gate_revision_min = {experiment_design = 1}`,
  `candidate_count = 2`, `metrics_in_report = ["pr_auc"]`. `max_cost_usd = 0.30`.
- S06: guided; gate data_splitting `abort`. Expect `status = "cancelled"`, `nodes_not_run =
  ["preprocessing"]`, `champion = false`, `test_scored = false`.
- S07: autonomous; actions: (event `node.started` node `model_training` → pause), (status
  paused → message "What is the current status of the run?"), (status paused → message "Which
  candidate is performing best so far?"), (status paused → resume). Expect completed,
  `messages_answered = 2`, `events_include = ["message.user", "message.assistant.complete"]`,
  `champion = true`.
- S08: action (event `node.started` node `preprocessing` → cancel). Expect cancelled,
  `champion = false`, `test_scored = false`.
- S10: dataset sales; target `units` continuous; regression `mae`; split `strategy =
  "temporal"`, `time_column = "date"`; candidates `["linear_regression", "hist_gb_regressor"]`.
  Expect `status = "failed"`, `nodes_failed = ["data_splitting"]`, `failure_contains =
  "no supported v1 split"`, `nodes_not_run = ["preprocessing"]`, `test_scored = false`.
  `max_cost_usd = 0.10`.
- S11: dataset visits; target `responded` binary positive 1; classification `roc_auc`; split
  `strategy = "grouped"`, `group_column = "patient_id"`; candidates `["logistic_regression",
  "random_forest_classifier"]`. Expect as S10.
- S12: dataset species; target `species` `type = "multiclass"`; classification
  `balanced_accuracy`, goal ("Does x4 matter?", `["x4"]`); stratified seed 42; candidates
  `["logistic_regression", "random_forest_classifier"]`, inferential `mnlogit`. Expect as S01
  with `metrics_in_report = ["balanced_accuracy"]`. Soft x4 "not rejected".
- S13: S01 contract plus `constraints.max_total_fit_seconds = 1`. No `expect` fields: any
  terminal status is accepted (G2 then checks only timeout/budget; G1/G3/G4/G5 still apply).
  Soft `summary_mentions = ["budget"]`.
- S17: dataset churn; contract target `does_not_exist` with S01's other fields;
  `setup.invalid_cases = true`; no expect fields.
Every scenario gets `title` from SCENARIOS.md headings.

### H1 checks and stop conditions

Static: `.venv/bin/python -m ruff check integration`, `.venv/bin/python -m ruff format --check
integration`, `.venv/bin/python -m mypy integration`. Smoke (no LLM, no cost):
`.venv/bin/python -m integration.run_scenarios --llm live --only S17` → expect exit 0, summary
shows S17 PASS. Also a parse smoke: `.venv/bin/python -c "from integration.model import
load_scenarios; from pathlib import Path; print(len(load_scenarios(Path('integration/scenarios.toml'))))"`
→ 13. Do NOT run any other scenario in live mode (would spend credits). Stop and report on:
missing run-status/mode aliases, any import that would require backend changes, S17 failing,
any static failure not fixable mechanically.
Receipt: changed files + sha256, commands + exits, S17 summary excerpt, deviations.

### H1 lint/type notes (resolved)

- Ruff selects `S`: `random.Random` draws (S311) and harness `subprocess` calls (S603/S607 if
  flagged) get a per-line `# noqa: <code> - <reason>` in the style of
  `backend/statlab/llm/routing.py:121`. Nothing else may be suppressed; any other lint that is not
  mechanically fixable → stop and report.
- No explicit `Any`; JSON values typed with `pydantic.JsonValue`; parsed JSON narrowed with
  `isinstance` before indexing. No `type: ignore`.
- Live LLM credits: H1 runs only S17 (no run is ever created successfully, so no LLM call).

## Stage H1b — real public datasets (user request 2026-10-07)

User asked for one or two real downloaded datasets besides the synthetic ones. Chosen (UCI
Machine Learning Repository, CC BY 4.0; verified downloadable 2026-10-07, hashes below):
- `adult` — UCI Adult / Census Income (id 2), `https://archive.ics.uci.edu/static/public/2/adult.zip`,
  zip sha256 `7537312dd56c2b98035880805ce99e68183a30ee468aa5329d6df0fbb3cc21bb`, member
  `adult.data` (32,561 rows, comma + space separated, no header, `?` = missing, trailing blank
  line; class balance 24,720 `<=50K` / 7,841 `>50K`).
- `autompg` — UCI Auto MPG (id 9), `https://archive.ics.uci.edu/static/public/9/auto+mpg.zip`,
  zip sha256 `5fc1507522b73b8050d3aa45df51eefe1b666ae5f428a5f1347b6483339f1003`, member
  `auto-mpg.data` (398 rows; 8 whitespace-separated fields, then a TAB, then a double-quoted car
  name; 6 rows have `?` horsepower; 305 distinct car names).
Raw `?` tokens are kept in the CSV on purpose; the contracts declare `dataset.na_values = ["?"]`
(exercises the NA-token path end to end, incl. pipeline replay).

Owned files: `integration/datasets.py`, `integration/model.py` (DatasetKey only),
`integration/scenarios.toml`, `.gitignore` (add one line `integration/.cache/`).

### datasets.py additions
- `DatasetKey` (in model.py) gains `"adult"`, `"autompg"`.
- Module constant `REAL_SOURCES: dict[str, tuple[str, str, str]]` = key → (url, zip sha256,
  member name) with the values above. `CACHE_DIR = Path("integration/.cache")`.
- `fetch_zip(key) -> bytes`: path `CACHE_DIR / f"{key}.zip"`; if missing, `mkdir(parents=True,
  exist_ok=True)` and download with `urllib.request.urlopen(url, timeout=120)` (`# noqa: S310 -
  fixed https UCI URL`), write bytes. Read bytes; `hashlib.sha256` hex != pinned → delete the
  cached file and raise `RuntimeError(f"{key}: downloaded archive sha256 mismatch")`. Return bytes.
- `_member_text(key) -> str`: `zipfile.ZipFile(io.BytesIO(fetch_zip(key))).read(member)
  .decode("utf-8")`.
- `generate("adult")`: header `age, workclass, fnlwgt, education, education_num, marital_status,
  occupation, relationship, race, sex, capital_gain, capital_loss, hours_per_week,
  native_country, income` (snake_case names exactly). Rows: `csv.reader(text.splitlines(),
  skipinitialspace=True)`; skip rows with no fields or only blank fields; each row must have 15
  fields else `RuntimeError("adult: malformed row <line number>")`; values kept verbatim
  (stripped).
- `generate("autompg")`: header `mpg, cylinders, displacement, horsepower, weight, acceleration,
  model_year, origin, car_name`. Per non-blank line: `left, name = line.split("\t", 1)`;
  `fields = left.split()` must have 8 items else `RuntimeError("autompg: malformed row <n>")`;
  `car_name = name.strip().strip('"')`; row = `(*fields, car_name)`. Values verbatim
  (`3504.` stays `3504.`).
- `write_csv` unchanged in behaviour (works for all keys via `generate`).

### scenarios.toml additions
- S18 "Real data: UCI Adult income (binary, messy categoricals)": dataset `adult`; contract
  `dataset.na_values = ["?"]`; target `income` binary `positive_class = ">50K"`;
  `features.exclude = ["fnlwgt"]`; objective classification `roc_auc`, goals ("Does sex affect
  income?", `["sex"]`) and ("Does education_num affect income?", `["education_num"]`); split
  stratified seed 42; candidates `["logistic_regression", "random_forest_classifier",
  "hist_gb_classifier"]`, inferential `logit`. Expect: completed; nodes_completed = 6 shared +
  champion_selection + artifact_persistence; `min_candidates_completed = 2`; `no_gates = true`;
  `champion = true`; `test_scored = true`; `report_contains = ["Modelling report", "Validation
  comparison", "Inference"]`; `pipeline_replay = true`; `manifest = true`. Soft: `metric_min =
  0.8`; inference sex "reject H0". `timeout_s = 2400`; `max_cost_usd = 0.30`.
- S19 "Real data: UCI Auto MPG (regression, missing horsepower)": dataset `autompg`; contract
  `dataset.na_values = ["?"]`; target `mpg` continuous; `features.roles = {origin =
  "categorical"}`; objective regression `rmse`, goal ("Does origin affect mpg?", `["origin"]`);
  split random seed 42; candidates `["linear_regression", "ridge", "random_forest_regressor"]`,
  inferential `ols`. `car_name` stays in on purpose (high-cardinality text). Expect as S18 plus
  `tools_used = ["fit_imputer", "apply_imputer"]`, `metrics_in_report = ["rmse"]`. Soft:
  `metric_max = 5.0`. `max_cost_usd = 0.30`.
Parse smoke now prints 15.

### H1b checks
Ruff check/format and mypy on `integration` (same commands as H1); parse smoke → 15;
`.venv/bin/python -c "from integration.datasets import generate; h, r = generate('adult');
print(len(h), len(r)); h, r = generate('autompg'); print(len(h), len(r))"` → `15 32561` and
`9 398`. No scenario runs (S18/S19 need the LLM).

H1 acceptance (2026-10-07): implementer receipt + main diff review. Ruff check/format, mypy
(8 files) exit 0; parse smoke 13; `--llm live --only S17` exit 0, S17 PASS 5/5, 0 LLM calls,
output `integration/out/20261007T105101Z-live/`. Accepted deviations: `_call(json=, files=)`
explicit params; helper dataclasses in driver.py; `_QUERIES` literal SQL; noqa S311 on
`random.Random(...)` only. Real-run paths (SSE, gates, actions, evidence) unexercised until H2.

## Stage H2 — offline stand-in LLM (+ H1b in the same stage)

Do H1b (above) first, then this. Goal: `--llm offline` drives every scenario through the real
API/MCP/nodes with a deterministic, free stand-in for `litellm.acompletion`. The stand-in only
has to produce schema-valid, contract-consistent decisions; it never judges data.

Owned files: `integration/offline_llm.py` (new), `integration/model.py`,
`integration/run_scenarios.py`, `integration/scenarios.toml`, plus H1b files.

### model.py
- New `OfflineHints(_Frozen)`: `reference_levels: dict[str, str] = {}`;
  `revised_candidates: tuple[str, ...] = ()`.
- `Scenario.offline: OfflineHints = OfflineHints()`.

### offline_llm.py
Facts it relies on (verified): every role routes to `deepseek/...` → client "json" tier appends
to the FIRST (system) message a final line `Schema: <compact json schema>` whose `"title"` is the
output class name (`backend/statlab/llm/client.py:_schema_instruction`, `_with_instruction`).
Prose calls (`complete_prose`, output `QaAnswer`) pass `stream=True` and consume
`ModelResponseStream` chunks via an object with `__aiter__` and callable `aclose`
(`client.py:_consume_prose`). Usage must carry `prompt_tokens_details.cached_tokens`.

`class OfflineProvider`:
- `__init__(self) -> None`: `self.scenario: Scenario | None = None`.
- `async __call__(self, **kwargs: object) -> object`:
  1. `messages = kwargs["messages"]`, must be a list of dicts with str `role`/`content`, else
     `RuntimeError("offline provider: malformed messages")`. `model = str(kwargs["model"])`.
  2. `system = messages[0]["content"]`; `marker = "\nSchema: "`; `index = system.rfind(marker)`;
     `-1` → `RuntimeError("offline provider: request has no schema")`; `schema =
     json.loads(system[index + len(marker):])`; `title = schema["title"]` (str).
  3. `user_text = "\n".join(m["content"] for m in messages if m["role"] == "user")`.
  4. `scenario = self.scenario`; None → `RuntimeError("offline provider: no active scenario")`.
  5. `payload = self._answer(title, user_text, scenario)`; `content = json.dumps(payload)`.
  6. `kwargs.get("stream") is True` → return `_Stream(content, model)`; else return
     `ModelResponse(choices=[{"message": {"role": "assistant", "content": content},
     "finish_reason": "stop", "index": 0}], model=model, usage={"prompt_tokens": 0,
     "completion_tokens": 0, "total_tokens": 0, "prompt_tokens_details": {"cached_tokens": 0}})`
     (pattern of `tests/test_llm_client.py:_response`).
- `_answer(title, user_text, scenario) -> dict[str, JsonValue]` — contract =
  `DataContract.model_validate(scenario.contract)`; `RATIONALE = "Offline stand-in decision."`:
  - Titles `IntakeDecision`, `ProblemDecision`, `ProfileDecision`, `ProfileFindings`,
    `SplitDecision`, `PreprocessingDecision`, `FeatureDecision`, `TrainingDecision`,
    `EvaluationActions`, `ValidationConclusion`, `InterpretationDecision`, `SearchDecision`
    → `{"rationale": RATIONALE}` (all other fields default; SearchDecision with no candidates
    = no second wave).
  - `DesignDecision` → names = `scenario.offline.revised_candidates` if those are non-empty AND
    `"Revise the typed proposal" in user_text`, else `contract.modelling.candidates`; empty →
    `RuntimeError("offline provider: contract has no candidates")`. Build with the real models
    (`statlab_contracts.candidate`): `base = CandidateSpec(label=names[0], rationale=f"Offline
    stand-in candidate {names[0]}.", is_baseline=True, estimator=EstimatorSpec(name=names[0]))`;
    for each other name: `draft = CandidateSpec(label=name, rationale=f"Offline stand-in
    candidate {name}.", estimator=EstimatorSpec(name=name))` then the final spec is the same
    constructor plus `varies_from_baseline=tuple(sorted(changed_dimensions(base, draft)))`.
    Return `{"rationale": RATIONALE, "candidate_set":
    CandidateSet(candidates=(base, *others)).model_dump(mode="json")}`. (Rationale strings are
    ≥ 20 chars, required by `CandidateSpec.rationale`.)
  - `InferenceDecision` → `model = contract.modelling.inferential_model or {"continuous": "ols",
    "binary": "logit", "multiclass": "mnlogit", "count": "glm_poisson"}[contract.target.type]`
    (target type None → `RuntimeError`); `predictors` = goal predictors in goal order, de-duplicated
    keeping first occurrence; `reference_levels = {k: v for k, v in
    scenario.offline.reference_levels.items() if k in predictors}`. Return those three +
    rationale. (Mirrors `design_inference.py` checks at lines 205-255.)
  - `ChampionChoice` → `start = user_text.find('"ranking"')`; search `re.search(
    r'"candidate"\s*:\s*"(c_\d{2,})"', user_text[start:] if start >= 0 else user_text)`; match →
    group 1, else first `re.search(r"\bc_\d{2,}\b", user_text)`, else `"c_01"`. Return
    `{"candidate_id": chosen, "rationale": RATIONALE}`.
  - `InterruptClass` → `{"category": "status" if "current status" in user_text.lower() else
    "qa", "lifecycle_action": None}`.
  - `ControlText` → `{"text": "Offline stand-in reply."}`.
  - `QaAnswer` → `{"text": "Offline stand-in answer.", "references": []}`.
  - `ContractProposal` → `{"contract": scenario.contract, "reasoning": "Offline stand-in
    proposal."}`.
  - anything else → `RuntimeError(f"offline provider has no answer for {title}")` (surfaces in
    api.log via the client's bug path; main extends the table).
- `_Stream`: `__init__(content, model)`; `__aiter__` returns an async generator yielding, for
  `piece in (content[: len(content) // 2], content[len(content) // 2 :])`:
  `ModelResponseStream(choices=[StreamingChoices(index=0, delta=Delta(content=piece))],
  model=model)`, then a usage chunk `ModelResponseStream(choices=[], model=model,
  usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0,
  prompt_tokens_details=PromptTokensDetails(cached_tokens=0)))` (pattern of
  `tests/test_llm_stream.py:chunk`; imports as there). `async aclose(self) -> None`: no-op.

### run_scenarios.py
- `--llm` choices `("live", "offline")`.
- In `_suite`: `provider = OfflineProvider() if args.llm == "offline" else None`;
  `llm_client = LlmClient(settings, acompletion=provider) if provider is not None else None`;
  pass to `ApiServer`. Before each scenario: `if provider is not None: provider.scenario =
  scenario`. Nothing else changes (`live=args.llm == "live"` stays for G7).

### scenarios.toml (offline hints)
`[scenario.offline]` tables: S01, S04, S05, S06, S07, S08, S13 →
`reference_levels = {plan = "premium", region = "north"}`; S05 additionally
`revised_candidates = ["logistic_regression", "random_forest_classifier"]`; S02 →
`{has_garden = "no"}`; S18 → `{sex = "Male"}`; S19 → `{origin = "1"}`.

### H2 checks (implementer)
Ruff check, Ruff format --check, mypy on `integration`; parse smoke → 15; H1b dataset smoke;
`.venv/bin/python -m integration.run_scenarios --llm offline --only S17` → exit 0 (proves the
offline wiring starts the API). Main runs the full offline dry-run itself afterwards.

### H2 addendum A — first offline S01 findings (2026-10-07, `integration/out/20261007T105450Z-offline`)
Both are harness defects; backend behaved as designed.
1. Every `model_training` refused "training method conflicts with explicit tuning method":
   `Driver.setup` uploaded `contract.model_dump_json()`, which serializes every default and makes
   all fields "explicit" (`model_fields_set`) server-side, e.g. `modelling.tuning.method`.
   Fix in `integration/driver.py` `setup`: keep `DataContract.model_validate(scenario.contract)`
   as a local pre-check only; send `scenario.contract` (the sparse dict) as the validate body's
   `contract`, and upload `json.dumps(scenario.contract)` as `contract.json`.
2. After the first failure gate was answered `abort` (run → cancelled), `_answer_gates` kept
   answering the other pending gates of the same stale snapshot → 409. Fix: `_answer_gates`
   submits at most ONE answer per call (return right after the first submission, after recording
   it); the next poll re-reads the snapshot.
Checks: Ruff check/format, mypy on `integration`; then `--llm offline --only S17` exit 0.

### H2 addendum B — second offline S01 run (`integration/out/20261007T105632Z-offline`)
After the first failure gate's `abort` cancelled the run, the other two failure gates stay
`pending` (designed: unanswered gates are dormant; `GateService.reconcile` keeps them). The
harness then answered one of them on the cancelled run → 409. Fix in `integration/driver.py`
`Driver.run`: call `_answer_gates` only when `status in ("awaiting_user", "paused")`.
Checks: Ruff check/format, mypy on `integration`.

## Fix F1 — stored contracts lose field explicitness (backend defect found by S01 offline)

Evidence: `integration/out/20261007T105632Z-offline/S01`: sparse uploaded contract (no
`modelling.tuning`); `contract.snapshot.json` and `contracts.json` hold the FULL dump
(`"tuning": {"method": "grid", ...}`); every candidate's `model_training` refused "training
method conflicts with explicit tuning method" (`backend/statlab/domains/candidate_models.py:154`).
Cause: `create_contract` (`backend/statlab/persistence/repository.py:252`) and
`ProjectService.persist_contract` (`backend/statlab/api/projects.py:201`) serialize with
`contract.model_dump_json()`, so on reload (`ContractRow.contract()`, MCP tools'
`DataContract.model_validate_json(snapshot)`) every default is in `model_fields_set`. All seven
explicitness guards (`design_inference.py:129/138/148`, `foundation.py:135/140`,
`candidate_models.py:154`, `candidate_pipeline.py:238`) then always fire, i.e. contract defaults
silently become hard user requirements. No PLAN decision requires the full dump; the propose
endpoint already uses `exclude_unset=True` (`projects.py:274`), confirming the intent.
Verified (pydantic 2.13.5): `model_dump_json(exclude_unset=True)` round-trips with `==` equal
(equality ignores fields-set) and preserves `model_fields_set`; every reader validates through
`DataContract`, so sparse JSON is accepted everywhere (incl. the embedded pipeline.py snapshot,
`mcp_server/statlab_tools/tools/reproducibility.py:329`).

Owned files: `backend/statlab/persistence/repository.py`, `backend/statlab/api/projects.py`,
`tests/test_repository.py`, `tests/test_api_projects.py`.
Recipe:
1. `repository.py` `create_contract`: `contract_json = contract.model_dump_json(exclude_unset=True)`.
2. `projects.py` `persist_contract`: write `contract.model_dump_json(exclude_unset=True)
   .encode("utf-8")` (file bytes must stay byte-identical to the SQL text; runs.py compares them).
3. `tests/test_repository.py::test_r9_json_byte_fidelity`: expected text becomes
   `contract.model_dump_json(exclude_unset=True)`; docstring: "R9: stored text is byte-identical
   to `contract.model_dump_json(exclude_unset=True)` (explicit fields only), and
   `ContractRow.contract()` equals the original."
4. New test right after it, `test_r9b_contract_explicitness_survives_storage(migrated_db,
   project)`: `contract = DataContract(target=TargetSpec(column="y", type="binary"),
   modelling=ModellingRules(candidates=("logistic_regression",)))` (import `TargetSpec`,
   `ModellingRules` from `statlab_contracts.contract`); `create_contract(..., path=Path("/c.json"),
   contract=contract)`; fetch with `get_contract`; assert `json.loads(fetched.contract_json) ==
   {"target": {"column": "y", "type": "binary"}, "modelling": {"candidates":
   ["logistic_regression"]}}`; `restored = fetched.contract()`; assert `"tuning" not in
   restored.modelling.model_fields_set`; assert `"method" not in
   restored.modelling.tuning.model_fields_set`; assert `restored.preprocessing.model_fields_set
   == set()`. Docstring: "Defaults stay implicit after storage, so explicit-contract guards only
   see user-set fields."
5. `tests/test_api_projects.py::test_contract_upload_versions_validation_findings_and_no_runs`:
   after the `assert Path(first_row["path"]).read_bytes() == first_bytes` line add a sparse
   upload: `sparse = await client.post(path, files={"file": ("sparse.json", b'{"target":
   {"column": "a"}}', "application/json")})`; assert `sparse.status_code == 201`; assert
   `Path(sparse.json()["path"]).read_bytes() == b'{"target":{"column":"a"}}'`.
Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_repository.py
tests/test_api_projects.py tests/test_api_runs.py tests/test_domain_binding.py
tests/test_storage.py tests/test_storage_fork.py tests/test_storage_prune.py
tests/test_mcp_client.py tests/test_graph_engine.py tests/test_db_concurrency.py
tests/test_db_migrations.py tests/test_control_router.py tests/test_skeleton.py
tests/test_project_memory.py tests/test_prompt_context.py tests/test_llm_client.py'`;
`.venv/bin/python -m ruff check` + `ruff format --check` + `mypy` on the four owned files.
Known hazard (not fixed here): contracts already stored in an existing DB keep their full dump
(all-explicit); new uploads are sparse. A client that uploads a full dump still marks everything
explicit (correct: the user sent it).

F1 acceptance (focused, 2026-10-07): implementer receipt + main diff review; 16 focused modules
`make test-fast` exit 0 (log `/tmp/f1_tests.log`, quiet mode, all dots), Ruff/format/mypy on
the 4 files exit 0. Offline S01 after F1: 23/24 must checks
(`integration/out/20261007T110130Z-offline`); champion selected, test scored once. Full
`make check` deferred to the batch acceptance.

### H2 addendum C — replay uses relative paths
`checks._replay` runs with `cwd=tempfile.mkdtemp()` but passes `evidence.pipeline_path` and
`scenario_dir / "data.csv"`, which are relative (`integration/out/...`) → script not found, empty
stdout → `IndexError`. Manual replay of the same files from a temp dir: exit 0. Fix in
`integration/checks.py` `_replay`: pass `str(evidence.pipeline_path.resolve())` and
`str((scenario_dir / "data.csv").resolve())`; directly after `subprocess.run`, if
`completed.returncode != 0` return the failed must-check with detail `completed.stderr[-2000:]`
before parsing stdout; on a parse exception include `completed.stderr[-2000:]` in the detail
(`f"replay failed: {exc!r}; stderr: {completed.stderr[-2000:]}"`; the TimeoutExpired branch keeps
`f"replay failed: {exc!r}"` since `completed` is unbound there — catch it in its own `except`
around `subprocess.run`).
Checks: Ruff check/format, mypy on `integration`.

### H2 addendum D — mypy must skip run outputs
`mypy integration` (and therefore `make typecheck`) recurses into `integration/out/**`, whose
generated `pipeline.py` copies collide ("Duplicate module named pipeline"). Fix in
`pyproject.toml` `[tool.mypy]`: add `exclude = ['^integration/out/']` (single-quoted TOML
literal, regex on the discovered path). Ruff already skips it (gitignored). Checks: `.venv/bin/
python -m mypy integration` exit 0 with output dirs present; `.venv/bin/python -m mypy` (no args,
uses `files`) exit 0.

H2 addenda C/D accepted (mypy 188 files clean). First full offline suite:
`integration/out/20261007T110326Z-offline` — PASS S01 S02 S06 S07 S08 S12 S13; FAIL S03 S04 S05
S10 S11 S17 S18 S19. Note: S07 shows $0.0046 offline — NOT a network call: litellm
`stream_chunk_builder` estimates usage locally when the stand-in's usage chunk is zero
(completion_tokens 14 = stand-in JSON); live providers send real usage.
Designed behaviour confirmed (PLAN §8 "Auto: gates none (except escalation)"): an autonomous
node failure escalates to a failure gate (`abort`/`rewind`), it does not fail the run.

## Fixes F2–F4 — backend defects found by the full offline suite (one stage)

Owned files: `mcp_server/statlab_tools/tools/reproducibility.py`,
`backend/statlab/domains/candidate_pipeline.py`, `backend/statlab/domains/foundation.py`,
`tests/test_tools_reproducibility.py`, `tests/test_nodes_candidate_pipeline.py`,
`tests/test_nodes_foundation.py`.

### F2 — guided runs always fail `snapshot_run` (S04, S05)
Evidence: guided S04: `intake` attempt 1 (prepared before its gate) and attempt 2 both call
`load_csv` with the SAME dataset sha256; `logged_calls` (`backend/statlab/mcp_client.py:575`)
keeps every current-run call; `export_pipeline_script` slices from the target and succeeds, but
`snapshot_run` (`reproducibility.py:514-523`) counts every `load_csv` → "the log has 2
load_csv dataset(s)" → artifact_persistence failure gate. Same for any retried/restarted shared
node. The run hash identity is the dataset, so repeated loads of one dataset are legitimate.
Recipe (`snapshot_run`): keep the `datasets` list; compute `distinct = sorted(set(datasets))`;
the refusal condition becomes `len(distinct) != 1`, message `f"the log has {len(distinct)}
distinct load_csv dataset(s); the run hash needs exactly one"`, field error `f"{len(distinct)}
distinct load_csv dataset sha256 in the log"`; `_run_hash(calls, distinct[0], contract_snapshot)`.
Docstring sentence "A log with no `load_csv` is `tool_param`: it has no data identity to hash."
becomes "A log whose `load_csv` calls name zero or several distinct datasets is `tool_param`;
repeated loads of one dataset (guided pre-gate attempts, retries) share one data identity."
Tests (`tests/test_tools_reproducibility.py`, after `test_snapshot_no_load_csv_is_tool_param`):
- `test_snapshot_repeated_same_dataset_is_one_identity(run_paths)`: `_write_contract(run_paths)`;
  `once = (_call("load_csv", [_ref()], dataset="abc"),)`; `twice = (*once, _call("load_csv",
  [_ref()], dataset="abc"))`; `res = snapshot_run(run_paths, twice)`; assert
  `isinstance(res.summary["run_hash"], str)` and `len(res.summary["run_hash"]) == 64`.
- `test_snapshot_two_distinct_datasets_is_tool_param(run_paths)`: `_write_contract(run_paths)`;
  calls = load_csv dataset "abc" and load_csv dataset "def" (fresh `_ref()` outputs); `with
  pytest.raises(ToolParamError, match="2 distinct load_csv dataset"): snapshot_run(...)`.

### F3 — high-cardinality categoricals reach training unencoded (S03, S19)
Evidence: S03 `customer_code`/`city` (60 levels), S19 `car_name` (305): `fit_onehot_encoder`
(columns=None) lists them in `skipped_high_cardinality` with "skipped -- target-encode it
instead"; `candidate_pipeline.py` onehot branch never acts on it; `train_estimator` refuses
"feature columns [...] are not numeric or bool; encode them first". PLAN.md:388:
`max_cardinality` "above this → target/frequency encoding". Resolution: target-encode the
skipped columns right after one-hot (target encoder tool exists, cross-fits OOF, D34c).
Recipe (`candidate_pipeline.py`):
1. Rename the body of `_fit_apply` into a new method `_fit_apply_output(self, invocation,
   evidence, pipeline, fit, apply, key, parameters) -> tuple[PipelineState, ToolSuccess]`
   (same parameters/types as `_fit_apply`), returning `(updated pipeline, output)` where `output`
   is the fit tool's `ToolSuccess`. `_fit_apply` keeps its signature and returns element 0 of
   `await self._fit_apply_output(...)`.
2. In the feature stage `if feature.encoding == "onehot":` branch: call `_fit_apply_output`
   with the same arguments as today (`"fit_onehot_encoder"`, `"apply_onehot_encoder"`,
   `"encoder"`, `{"max_cardinality": feature.max_cardinality}`), assign the pipeline, keep the
   output. Then `skipped = output.summary.get("skipped_high_cardinality")`; if `skipped` is a
   non-empty dict: `pipeline = await self._fit_apply(invocation, evidence, pipeline,
   "fit_target_encoder", "apply_target_encoder", "encoder", {"columns": sorted(skipped), "seed":
   self.runtime.contract.constraints.random_seed})` (same parameter shape as the existing
   `encoding == "target"` branch). Nothing else changes.
Tests (`tests/test_nodes_candidate_pipeline.py`):
- Fake `CandidateTools`: new attribute `self.skipped: dict[str, JsonValue] = {}` in `__init__`;
  in the `tool.startswith("fit_")` branch add `if tool == "fit_onehot_encoder": summary =
  {"skipped_high_cardinality": dict(self.skipped)}` (next to the existing `fit_imputer` case).
- New test after `test_target_encoding_keeps_oof_train_and_only_applies_to_validation`:
  `test_onehot_skipped_high_cardinality_columns_are_target_encoded(migrated_db, project,
  tmp_path)`: `h, tools = await candidate_harness(migrated_db, project, tmp_path)`;
  `tools.skipped = {"zip": 400, "city": 60}`; `await prepare_candidate(h)`; `calls = [(name,
  params) for _, name, params in tools.calls]`; `names = [name for name, _ in calls]`; assert
  `names.index("fit_target_encoder") > names.index("apply_onehot_encoder")`; `fit =
  next(params for name, params in calls if name == "fit_target_encoder")`; assert
  `fit["columns"] == ["city", "zip"]` and `fit["seed"] ==
  h.runtime.contract.constraints.random_seed`; `pipeline = pipeline_from_state(h.state,
  h.state.branches[0], require="feature_engineering")`; assert `[step.tool for step in
  pipeline.replay] == ["drop_columns", "apply_imputer", "apply_onehot_encoder",
  "apply_target_encoder", "apply_scaler"]`.
  (`test_replay_fits_train_applies_both_and_locks_identity` keeps its 4-step replay: empty
  `skipped` → no target encoding.)

### F4 — dedup ignores `features.exclude` (S18)
Evidence: S18 (UCI Adult, `exclude=["fnlwgt"]`): `drop_duplicate_rows` subset includes
`fnlwgt` (near-unique) → only 24 rows dropped; `drop_columns(fnlwgt)` runs after; the frames
then hold 888/833/416 cross-split duplicate pairs → `verify_split` `duplicate_rows: fail` →
"split verification failed". `verify_split` compares all columns of the split frames, so dedup
must use the same columns minus id columns.
Recipe (`foundation.py` `_split`): move the `protected`/`excluded` computation (currently after
the `drop_high_missing_rows` block) to just before the `if contract.preprocessing.drop_duplicates:`
block (unchanged expressions); the dedup `subset` becomes the intake columns that are in neither
`contract.features.id_columns` nor `excluded` (order preserved). The `drop_columns` call stays
where it is and uses the same `excluded`.
Test (`tests/test_nodes_foundation.py::test_contract_structural_options_and_traceability_survive`):
expected dedup subset changes from `["x", "excluded", "y"]` to `["x", "y"]`.

### F2–F4 checks
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_tools_reproducibility.py
tests/test_nodes_persistence.py tests/test_spine_reporting.py tests/test_nodes_candidate_pipeline.py
tests/test_nodes_foundation.py tests/test_nodes_champion.py tests/test_skeleton.py'`; Ruff check,
Ruff format --check and mypy on the six owned files.

### H2 addendum E — harness corrections from the full offline suite
1. `integration/driver.py` `invalid_setup` S17-llm count is suite-global (229 calls from earlier
   scenarios). Use `SELECT COUNT(*) FROM llm_calls WHERE run_id IN (SELECT id FROM runs WHERE
   project_id = ?)` with the S17 project id (parameterized).
2. `integration/checks.py` `failure_contains`: also pass when some gate (from
   `evidence.gates`, question parsed with the existing `_question`) has `phase == "failure"` and
   `expect.failure_contains in json.dumps(question.get("failure"))`. (Autonomous refusals
   escalate to failure gates, PLAN §8.)
3. `integration/scenarios.toml` S10 and S11: `[scenario.expect]` becomes `status =
   "cancelled"`, remove `nodes_failed`, keep `failure_contains`, `nodes_not_run`, `test_scored`,
   add `gates_opened_for = ["data_splitting"]`; add a gate rule `[[scenario.gates]]` with `node =
   "data_splitting"`, `phase = "failure"`, `action = "abort"`.
Checks: Ruff check/format, mypy on `integration`; parse smoke → 15.

F2–F4 acceptance (focused, 2026-10-07): main diff review matches recipe; 7 focused modules
`make test-fast` exit 0 (`/tmp/f24_tests.log`), Ruff/format/mypy on 6 files exit 0. Accepted
deviation: typed local `skipped_parameters` with `[*sorted(skipped)]` (mypy/ruff C413/C416).
Second full offline suite (`integration/out/20261007T111547Z-offline`): 13/15 PASS incl. S03,
S10, S11, S17, S18 (UCI Adult, 171 s), S19 (UCI Auto MPG). Remaining: S04/S05 G3.

Open design question for the user (not changed): an approved `before` gate re-runs the node as
attempt 2; attempt 1 (the proposal attempt, `gates.node_execution_id`) gets no `node.*` end event
and its `node_executions` row stays `awaiting_user` after the run completes. Closure is only
signalled by `gate.closed`. Changing it means choosing a terminal execution status for proposal
attempts and touches accepted engine/recovery semantics.

### H2 addendum F — G3 recognises proposal attempts; soft budget text
1. `integration/checks.py` G3 `pairs_ok`: build `gate_exec = {payload["gate_id"]:
   payload["node_execution_id"]}` from server `gate.opened` events (skip events missing either
   key) and `closed = {gate_exec[gid] for gate.closed events whose gate_id is in gate_exec}`; a
   `node.started` counts as finished if a later end event exists (current rule) OR its
   `execution_id` is in `closed`.
2. `integration/checks.py` soft `summary_mentions`: search each `node_executions` row's
   `summary` AND `error` text (case-insensitive).
Checks: Ruff check/format, mypy on `integration` (report mypy's own exit, not a pipe's).

## Stage H3 — fork, propose, memory, restart scenarios (S09, S14, S15, S16)

Verified backend facts:
- Rewind: `POST /api/runs/{id}/control {"action":"rewind","target","request_id"}` → 201
  `{"new_run_id"}`; same `request_id` again returns the existing child (`rewind.py:199-211`). A
  COMPLETED parent stays `completed`; only a non-completed parent becomes `superseded`
  (`rewind.py:288`, consistent with D36b "parent halted by a rewind fork"). The child is
  launched automatically (`runs.py` `_continue`). Child report states prior test exposure:
  "The test set has been scored {n} time(s) in prior runs" (`tools/reporting.py:312`).
- Propose: `POST /api/projects/{pid}/contracts/propose` body `{"dataset_id", "contract"}` → 200
  `ContractProposal{contract, reasoning}` (LLM via `complete_setup`, no `llm_calls` row).
- Memory: `artifact_persistence` writes a `memory` row (`remember_outcome`); a new run's
  `runs.config_json` carries `"memory": [...]` (`RunConfig.memory`).
- Restart: `ApiServer.stop()` resets its server/task, so `start()` can run again (new app, boot
  recovery re-launches `pending`/`running` runs, interrupted executions get "process terminated").

Owned files: `integration/model.py`, `integration/driver.py`, `integration/run_scenarios.py`,
`integration/checks.py`, `integration/offline_llm.py`, `integration/scenarios.toml`.

### model.py
- `Action.do` literal gains `"restart_api"` (no extra fields; validator unchanged otherwise).
- `Setup` gains `propose: bool = False` and `runs: int = Field(default=1, ge=1, le=2)`.
- `OfflineHints` gains `proposed_contract: dict[str, JsonValue] = {}`.
- `Expect` gains: `fork_idempotent: bool = False`; `child_status: RunStatus | None = None`;
  `child_report_contains: tuple[str, ...] = ()`; `child_not_rerun: tuple[str, ...] = ()`;
  `proposal_complete: bool = False`; `memory_rows_min: int = 0`; `second_run_memory: bool =
  False`; `shared_completed_once: bool = False`.

### offline_llm.py
`ContractProposal` answer: `contract = scenario.offline.proposed_contract or scenario.contract`.

### driver.py
- `RunOutcome` gains `fork_responses: list[dict[str, JsonValue]]` (default empty) and
  `child_run_id: str | None = None`.
- `Driver.__init__` gains keyword `restart: Callable[[], Awaitable[None]] | None = None`
  stored as `self._restart`.
- `_fire`: `rewind` posts the same body TWICE (both expected {200, 201}); append both response
  bodies (with `status_code`) to `outcome.fork_responses`; `outcome.child_run_id =
  _str(first_body["new_run_id"])`; record the action as today with the first response.
  `restart_api`: `self._restart` None → `HarnessError("restart_api needs a restart hook")`;
  else `await self._restart()`; record `{index, do, status_code: 0, body: {}}`.
- `setup`: when `scenario.setup.propose`: after the dataset upload, `POST
  /api/projects/{pid}/contracts/propose` `{"dataset_id", "contract": scenario.contract}` →
  {200}; `proposal = _obj(body)`; write `scenario_dir / "proposal.json"`; the contract used for
  the local pre-check, validate and upload becomes `_obj(proposal["contract"])` instead of
  `scenario.contract`. `SetupResult` gains `proposal: dict[str, JsonValue] | None = None`.
- New `async def create_run(self, project_id, dataset_id, contract_id, mode) -> str` (the
  existing step-5 call, reused by `setup`).

### run_scenarios.py
- `api` restart hook: inside `_suite` define `async def restart() -> None: await api.stop();
  await api.start()` (same settings, same `llm_client`, same port) and pass
  `restart=restart` to `Driver`.
- Refactor the per-run evaluation of `_run_one` into `async def _evaluate(label: str, scenario,
  run_id, outcome, run_dir, *, expect_status, full: bool) -> list[CheckResult]`: collect
  evidence for `run_id` into `run_dir`, global checks (with `scenario.model_copy(update={"expect":
  scenario.expect.model_copy(update={"status": expect_status})})` so G2 uses `expect_status`),
  and when `full` also expectation + soft checks; prefix every check id with `f"{label}-"` when
  `label != "main"`. The api.log slice for G1 spans the whole scenario (taken once, as today).
- `_run_one` normal path: `prepared = setup`; `outcome = run(...)`; checks += `_evaluate("main",
  ..., expect_status=scenario.expect.status, full=True)`. Then:
  - if `scenario.setup.runs == 2`: `run2 = await driver.create_run(prepared.project_id,
    prepared.dataset_id, prepared.contract_id, scenario.mode)`; `outcome2 = await
    driver.run(scenario.model_copy(update={"actions": (), "gates": ()}), run2, scenario_dir /
    "run2", budget=budget)` (create the dir); checks += `_evaluate("run2", ...,
    expect_status=scenario.expect.status, full=False)`.
  - if `outcome.child_run_id`: same with label `"child"`, dir `scenario_dir / "child"`,
    `expect_status=scenario.expect.child_status`, `full=False`.
  - new scenario-level must-checks (in checks.py, function `extra_checks(*, scenario, outcome,
    db_path, project_slug, main_run_id, child_run_id, run2_id, proposal, scenario_dir)`):
    - `fork_idempotent`: two fork responses, both `new_run_id` equal and non-empty
      (`E-fork_idempotent`).
    - `child_report_contains`: child run dir report (`data_dir/slug/runs/<child>/report/report.md`,
      via the child evidence) contains each string (`E-child_report:<s>`).
    - `child_not_rerun`: no `node_executions` row of the child run for each node
      (`E-child_not_rerun:<node>`).
    - `proposal_complete`: proposal contract has non-null `target.type`, `objective.task`,
      `objective.primary_metric`, `split.strategy` and non-blank `reasoning`
      (`E-proposal_complete`).
    - `memory_rows_min`: read-only `SELECT COUNT(*) FROM memory WHERE project_id = (SELECT
      project_id FROM runs WHERE id = ?)` with the main run id ≥ N (`E-memory_rows`).
    - `second_run_memory`: run2 `runs.config_json` parsed → `memory` is a non-empty list
      (`E-second_run_memory`).
    - `shared_completed_once`: for main run, for each of the 6 shared nodes plus
      `champion_selection`, `artifact_persistence`: count of `completed` rows ≤ 1, and no
      `(node_name, candidate_id, attempt)` appears twice with status `completed`
      (`E-shared_completed_once`).
  Child/run2 evidence objects come from `_evaluate`; make `_evaluate` return `(checks,
  evidence)`.
- `result.json` gains `child_run_id`, `run2_id`, `fork_responses`.

### scenarios.toml (append; S01 contract and offline reference levels unless stated)
- S09 "Rewind creates an idempotent fork": dataset churn; autonomous; action (status
  `completed` → rewind target `experiment_design`, request_id `s09-fork`). Expect: `status =
  "completed"`, `fork_idempotent = true`, `child_status = "completed"`, `child_report_contains
  = ["time(s) in prior runs"]`, `child_not_rerun = ["intake", "problem_definition",
  "data_profiling", "data_splitting"]`, `champion = true`. `timeout_s = 1800`, `max_cost_usd =
  0.40`.
- S14 "Contract proposed by the agent": dataset churn; contract `{target = {column =
  "churned"}}` only; `setup.propose = true`; offline `proposed_contract` = the full S01
  contract. Expect: `proposal_complete = true`, `status = "completed"`, `champion = true`.
- S15 "Project memory across runs": S01 contract; `setup.runs = 2`. Expect: `status =
  "completed"`, `memory_rows_min = 1`, `second_run_memory = true`. `max_cost_usd = 0.40`.
- S16 "API restart mid-run": S01 contract; actions (event `node.completed` node
  `model_evaluation` → restart_api), (status `paused` → resume). Expect: `status =
  "completed"`, `champion = true`, `shared_completed_once = true`, `test_scored = true`.

### H3 checks
Ruff check/format, mypy on `integration` (mypy's own exit); parse smoke → 19;
`.venv/bin/python -m integration.run_scenarios --llm offline --only S09 S14 S15 S16` — report
the summary table and every failed check verbatim; failures are NOT fixed by the implementer
(main diagnoses). No live runs.

## Live run 1 (2026-10-07, `integration/out/20261007T112809Z-live`) — stopped early
S01 live: cancelled after 67 s. The design LLM put `random_state: 42` into every candidate's
`estimator.params` at `experiment_design`; nothing rejects it there, so every `train_estimator`
call failed `tool_param` "params ['random_state'] are fixed by the tool (n_jobs by D17,
random_state by D34e)" (`mcp_server/statlab_tools/tools/training.py:161`); one candidate also
refused "initial direct parameters differ from approved candidate" (the training LLM most likely
dropped that same key). All failures escalated → harness abort. Main stopped the suite to save
budget (S02 in progress). Spend: $0.1781 / 44 calls. Harness hazard noted: SIGINT is captured by
the in-process uvicorn, so stopping needed TERM and left the MCP child orphaned (killed by main).

## Fix F5 — tool-fixed estimator params rejected at the decision boundary
Resolution (D17/D34e): `n_jobs` and `random_state` are owned by the tools; an LLM decision that
sets them must fail Pydantic validation so `LlmClient` runs its one repair round with the error,
instead of failing later at the tool.
Owned files: `contracts/statlab_contracts/vocabulary.py`, `contracts/statlab_contracts/candidate.py`,
`backend/statlab/domains/decisions.py`, `backend/statlab/domains/candidate_models.py`,
`mcp_server/statlab_tools/tools/training.py`, `tests/test_candidate_models.py`,
`tests/test_nodes_candidate_models.py`.
Recipe:
1. `vocabulary.py`: add module constant `RESERVED_ESTIMATOR_PARAMS: frozenset[str] =
   frozenset({"n_jobs", "random_state"})` with a one-line comment "Owned by the tools: n_jobs
   (D17), random_state (D34e)." (place after the other module-level constants).
2. `candidate.py` `EstimatorSpec`: import the constant; add `@model_validator(mode="after")`
   `_no_tool_fixed_params(self) -> EstimatorSpec`: `reserved = sorted(set(self.params) &
   RESERVED_ESTIMATOR_PARAMS)`; non-empty → `ValueError(f"estimator params {reserved!r} are
   fixed by the tools (n_jobs by D17, random_state by D34e); omit them")`; return self.
3. `decisions.py` `TrainingDecision`: add `@model_validator(mode="after")`
   `_no_tool_fixed_params(self) -> TrainingDecision`: `keys = set(self.params) |
   set(self.param_grid or {})`; `reserved = sorted(keys & RESERVED_ESTIMATOR_PARAMS)`; non-empty →
   same message wording with "training params" instead of "estimator params"; return self.
4. `training.py`: delete the local `_RESERVED_PARAMS` definition (line 81); import
   `RESERVED_ESTIMATOR_PARAMS` from `statlab_contracts.vocabulary` and use it at line 161.
   Tool behaviour/message unchanged.
5. `candidate_models.py` training `question` (line 142-145): append the sentence " For the
   initial direct fit leave params empty; the approved candidate parameters are used." after
   "respect explicit tuning method." (keep one space between sentences).
Tests:
- `tests/test_candidate_models.py`: `test_estimator_params_reject_tool_fixed_keys` parametrized
  `key` over `("n_jobs", "random_state")`: `with pytest.raises(ValidationError, match="fixed by
  the tools"): EstimatorSpec.model_validate({"name": "ridge", "params": {key: 1}})`.
- `tests/test_nodes_candidate_models.py`: `test_training_decision_rejects_tool_fixed_params`
  parametrized `payload` over `({"rationale": "r", "params": {"random_state": 1}}, {"rationale":
  "r", "method": "grid", "param_grid": {"n_jobs": [1]}})`: `with pytest.raises(ValidationError,
  match="fixed by the tools"): TrainingDecision.model_validate(payload)`.
Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_candidate_models.py
tests/test_nodes_candidate_models.py tests/test_tools_training.py tests/test_registry.py
tests/test_candidate_hash.py tests/test_candidate_vocabulary.py
tests/test_nodes_candidate_pipeline.py tests/test_candidate_fanout_tools.py'`; Ruff check, Ruff
format --check, mypy on the seven owned files.
Hazard: stored candidate specs that already contain `random_state` (only possible from a live
run before F5) no longer validate; none exist outside `integration/out`.

### H3 receipt (partial) and addendum A (`integration/out/20261007T113129Z-offline`)
S09 PASS (fork idempotent, child completed, prior exposure disclosed, no shared re-run). S15
PASS (memory row, run2 config memory). S14 cancelled; S16 crashed the suite.
- S16: `ApiServer.stop()` sets `should_exit` only; uvicorn graceful shutdown waits forever on the
  open SSE stream. Production `statlab.main._RunServer.handle_exit` first calls
  `RunRegistry.begin_shutdown()`, which sets `streams_closing` (`api/runs.py:396-397`) and ends
  every SSE generator. The `TimeoutError` from `asyncio.wait_for` then escaped `_run_one`.
- S14: the stand-in built the design from S14's sparse contract (no candidates) → RuntimeError →
  "LLM client failed" at experiment_design. The proposal (full contract, all fields explicit —
  accepted proposals are explicit choices; no product change) also makes `tuning.method="grid"`
  explicit, which the stand-in's direct fit would violate.
Recipe (owned: `integration/services.py`, `integration/run_scenarios.py`,
`integration/offline_llm.py`, `integration/scenarios.toml`):
1. `services.py` `ApiServer`: keep the app created in `start()` as `self._app`; in `stop()`,
   before setting `should_exit`, `registry = getattr(self._app.state, "run_registry", None)` and
   if `isinstance(registry, RunRegistry)` (import from `statlab.api.runs`) call
   `registry.begin_shutdown()` (mirrors `_RunServer.handle_exit`).
2. `run_scenarios.py` `_run_one`: the caught exception tuple also includes `TimeoutError`.
3. `offline_llm.py` `_answer`: `contract = DataContract.model_validate(
   scenario.offline.proposed_contract or scenario.contract)`.
4. `scenarios.toml` S14: inside `[scenario.offline.proposed_contract]` add `modelling.tuning`
   with `method = "none"` (TOML table `[scenario.offline.proposed_contract.modelling.tuning]`).
Checks: Ruff check/format, mypy on `integration`; `LOKY_MAX_CPU_COUNT=1 .venv/bin/python -m
integration.run_scenarios --llm offline --only S14 S16` (report summary + failed checks; do not
fix scenario failures).

H3 addendum A accepted: `integration/out/20261007T113715Z-offline` S14 PASS 9/9, S16 PASS 10/10.
F5 implemented (focused tests exit 0, statics exit 0); acceptance pending `make check`.

User instruction (2026-10-07): no paid/live run until the user explicitly confirms; prepare and
verify everything first.

### H3 addendum G — safe abort of a (paid) run
Live run 1 showed Ctrl-C does not stop the suite: the in-process `uvicorn.Server.serve()`
installs its own SIGINT/SIGTERM handlers (`capture_signals`), so the harness never sees the
signal; TERM then killed the process without `finally`, orphaning the MCP child.
Recipe (owned: `integration/services.py`, `integration/run_scenarios.py`):
1. `services.py`: add `class _HarnessServer(uvicorn.Server)` overriding `capture_signals` with a
   `@contextlib.contextmanager` that only `yield`s (docstring: "Leave SIGINT/SIGTERM to the
   harness so Ctrl-C reaches its cleanup."); `ApiServer.start` builds `_HarnessServer(config)`
   instead of `uvicorn.Server(config)`.
2. `run_scenarios.py` `main`: before `asyncio.run`, `signal.signal(signal.SIGTERM,
   signal.default_int_handler)` (TERM behaves like Ctrl-C). `_suite` already stops driver, API
   (which calls `begin_shutdown` → active runs cancelled, no further LLM calls) and MCP in
   `finally`.
3. `run_scenarios.py` `main`: wrap `asyncio.run(...)` in `try/except KeyboardInterrupt:` print
   `"interrupted; services stopped"` to stderr and return 130.
Checks: Ruff check/format, mypy on `integration`; manual abort probe (offline, free): start
`.venv/bin/python -m integration.run_scenarios --llm offline --only S01` in the background, after
8 s send SIGINT to that python process, then report: exit code (expect 130), whether any
`statlab_tools` process started by it is still alive 5 s later (expect none), and the stderr line.

F5/H3-G accepted and committed (`236a47e`): make check 3014 passed/1 skip/100%; offline
`20261007T114213Z-offline` 19/19 PASS. User asked (2026-10-07) for two free pre-live steps:
F6 (validate LLM-proposed hyperparameters early) and H4 (rich offline stand-in).

## Fix F6 — curated tunable hyperparameters, validated at the LLM boundary
Problem: `experiment_design` checks estimator NAMES against the registry but never parameter
names/values (`design_inference.py:95-170`); a bad name/value only fails at `train_estimator`
(tool_param/data) → candidate failure gate → run halts. Fix: a curated table in
`statlab_contracts` (stdlib only) checked by Pydantic validators, so a bad LLM decision gets the
client's repair round with an actionable message. Combo-prone params (`penalty`, `solver`,
`l1_ratio`, `criterion`) are deliberately not tunable in v1.

Owned files: `contracts/statlab_contracts/vocabulary.py`, `contracts/statlab_contracts/candidate.py`,
`backend/statlab/domains/decisions.py`, `tests/test_candidate_vocabulary.py`,
`tests/test_candidate_models.py`, `tests/test_nodes_candidate_models.py`, `tests/test_registry.py`,
`tests/test_candidate_hash.py`, `tests/test_candidate_set.py`, `tests/test_nodes_design_inference.py`.

### vocabulary.py (after `RESERVED_ESTIMATOR_PARAMS`)
- `TunableKind = Literal["positive_number", "nonnegative_number", "positive_int",
  "positive_int_or_none", "int_at_least_2", "int_at_least_2_or_none", "bool", "max_features"]`.
- `TUNABLE_PARAM_KINDS: dict[str, TunableKind]` = `C` positive_number; `tol` positive_number;
  `max_iter` positive_int; `fit_intercept` bool; `positive` bool; `alpha` nonnegative_number;
  `n_estimators` positive_int; `max_depth` positive_int_or_none; `min_samples_split`
  int_at_least_2; `min_samples_leaf` positive_int; `max_features` max_features; `bootstrap` bool;
  `learning_rate` positive_number; `max_leaf_nodes` int_at_least_2_or_none; `l2_regularization`
  nonnegative_number.
- `TUNABLE_PARAMS: dict[str, frozenset[str]]` = `logistic_regression` {C, tol, max_iter,
  fit_intercept}; `linear_regression` {fit_intercept, positive}; `ridge` {alpha, fit_intercept,
  tol}; `random_forest_classifier` and `random_forest_regressor` {n_estimators, max_depth,
  min_samples_split, min_samples_leaf, max_features, bootstrap}; `hist_gb_classifier` and
  `hist_gb_regressor` {learning_rate, max_iter, max_leaf_nodes, max_depth, min_samples_leaf,
  l2_regularization}. Comment: "v1 tunable hyperparameters per registry estimator; combo-prone
  ones (penalty/solver/criterion) are excluded. tests/test_registry.py proves each is a real
  get_params() key."
- `def _matches_kind(value: ParamValue, kind: TunableKind) -> bool` — `number` means
  `isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)`;
  `integer` means `isinstance(value, int) and not isinstance(value, bool)`:
  positive_number → number and > 0; nonnegative_number → number and ≥ 0; positive_int → integer
  and ≥ 1; positive_int_or_none → None or (integer ≥ 1); int_at_least_2 → integer ≥ 2;
  int_at_least_2_or_none → None or (integer ≥ 2); bool → `isinstance(value, bool)`;
  max_features → None, `"sqrt"`, `"log2"`, integer ≥ 1, or (`isinstance(value, float)` and
  finite and 0 < value ≤ 1). Implement as a `match kind:` with one case per literal.
- `def tunable_param_errors(params: Mapping[str, ParamValue], allowed: frozenset[str] | None)
  -> list[str]`: for each key in sorted order: if key not in `TUNABLE_PARAM_KINDS` or (allowed
  is not None and key not in allowed) → message `f"{key!r} is not a tunable parameter here;
  allowed: {sorted(allowed if allowed is not None else TUNABLE_PARAM_KINDS)}"`; elif not
  `_matches_kind(value, TUNABLE_PARAM_KINDS[key])` → `f"{key}={value!r} is not a valid
  {TUNABLE_PARAM_KINDS[key].replace('_', ' ')}"`. Return the list.
- `def tunable_grid_errors(grid: Mapping[str, tuple[ParamValue, ...]]) -> list[str]`: for each
  key in sorted order, for each value: extend with `tunable_param_errors({key: value}, None)`;
  return de-duplicated messages in first-seen order.
(`math` and `collections.abc.Mapping` imports are stdlib.)

### candidate.py `EstimatorSpec`
Add a second `@model_validator(mode="after")` `_tunable_params(self) -> EstimatorSpec`, declared
AFTER `_no_tool_fixed_params` (so reserved keys keep their F5 message): `allowed =
TUNABLE_PARAMS.get(self.name)`; if `allowed is None` return self (unregistered names are
refused later by the design node; test fixtures use such names); `errors =
tunable_param_errors(self.params, allowed)`; non-empty → `ValueError(f"estimator {self.name!r}:
" + "; ".join(errors))`.

### decisions.py `TrainingDecision`
Add `@model_validator(mode="after")` `_tunable_params(self) -> TrainingDecision` declared after
`_no_tool_fixed_params`: `errors = [*tunable_param_errors(self.params, None),
*tunable_grid_errors(self.param_grid or {})]`; non-empty → `ValueError("; ".join(errors))`.

### Tests
- `tests/test_candidate_vocabulary.py`:
  - B5/B6/B7 (`test_param_value_*`): estimator name `"logistic_regression"` → `"fixture"`
    (they test ParamValue typing, not tunables).
  - `test_tunable_kinds_accept` parametrized `(key, value)` over: ("C", 0.5), ("C", 2),
    ("alpha", 0.0), ("max_iter", 100), ("max_depth", None), ("max_depth", 3),
    ("min_samples_split", 2), ("max_leaf_nodes", None), ("max_leaf_nodes", 15),
    ("fit_intercept", False), ("max_features", "sqrt"), ("max_features", None),
    ("max_features", 0.5), ("max_features", 3): assert `tunable_param_errors({key: value}, None)
    == []`.
  - `test_tunable_kinds_reject` over: ("C", 0), ("C", -1.0), ("C", True), ("C", float("inf")),
    ("C", "1"), ("alpha", -0.1), ("max_iter", 0), ("max_iter", 1.5), ("max_iter", True),
    ("max_depth", 0), ("min_samples_split", 1), ("max_leaf_nodes", 1), ("fit_intercept", 1),
    ("max_features", "auto"), ("max_features", 1.5), ("max_features", 0.0),
    ("max_features", (1, 2)): assert `tunable_param_errors({key: value}, None) == [f"{key}={value!r}
    is not a valid {TUNABLE_PARAM_KINDS[key].replace('_', ' ')}"]`.
  - `test_tunable_unknown_and_disallowed_keys`: `tunable_param_errors({"penalty": "l1"}, None)`
    has one message containing "'penalty' is not a tunable parameter"; `tunable_param_errors({"alpha":
    1.0}, TUNABLE_PARAMS["logistic_regression"])` one message containing "'alpha' is not a tunable
    parameter" and "allowed: ['C', 'fit_intercept', 'max_iter', 'tol']".
  - `test_tunable_grid_errors`: `tunable_grid_errors({"C": (0.5, 1.0)}) == []`;
    `tunable_grid_errors({"C": (0.5, -1.0, -1.0)}) == ["C=-1.0 is not a valid positive number"]`.
- `tests/test_candidate_models.py`: `test_estimator_params_must_be_tunable`: `with
  pytest.raises(ValidationError, match="'penalty' is not a tunable parameter"):
  EstimatorSpec.model_validate({"name": "logistic_regression", "params": {"penalty": "l1"}})`;
  `with pytest.raises(ValidationError, match="alpha=-1 is not a valid nonnegative number"):
  EstimatorSpec.model_validate({"name": "ridge", "params": {"alpha": -1}})`; and
  `EstimatorSpec.model_validate({"name": "fixture", "params": {"choice": 1}}).params == {"choice":
  1}`.
- `tests/test_nodes_candidate_models.py`:
  - new `test_training_decision_rejects_untunable_params` parametrized `payload` over
    `({"rationale": "r", "params": {"penalty": "l1"}}, {"rationale": "r", "method": "grid",
    "param_grid": {"C": (0.5, -1.0)}})`: `pytest.raises(ValidationError)` on
    `TrainingDecision.model_validate(payload)`.
  - existing lines ~460-472: `params={"choice": 1}` → `params={"C": 2.0}`; `params={"choice":
    0}` → `params={"C": 1.0}`; `param_grid={"choice": (0, 1)}` → `param_grid={"C": (0.5, 1.0)}`.
    (Expected refusal messages unchanged.)
- `tests/test_registry.py`: new `test_tunable_params_match_registry`: `assert set(TUNABLE_PARAMS)
  == set(estimators())`; for each `name, entry in estimators().items()`: `keys =
  set(entry.build(0, "none").get_params())`; `assert TUNABLE_PARAMS[name] <= keys`; `assert
  set(entry.default_grid) <= TUNABLE_PARAMS[name]`; for each grid key/value tuple assert
  `tunable_param_errors({k: v}, TUNABLE_PARAMS[name]) == []` for every value (default-grid values
  are tuples of scalars; pass each scalar).
- `tests/test_candidate_hash.py` H2: params `{"a": 1, "b": 2}` → `{"C": 1, "max_iter": 2}` and
  `{"b": 2, "a": 1}` → `{"max_iter": 2, "C": 1}`.
- `tests/test_candidate_set.py` lines ~159 and ~245: `{"distinct": 1}` → `{"C": 2.0}`.
- `tests/test_nodes_design_inference.py`:
  - add module helper `_variant(name: str, i: int) -> dict[str, ParamValue]`: `i == 0` → `{}`;
    `"logistic_regression"` → `{"C": float(i)}`; `"ridge"` → `{"alpha": float(i)}`;
    `"linear_regression"` → `({}, {"fit_intercept": False}, {"positive": True},
    {"fit_intercept": False, "positive": True})[i]`; any other name → `{"n_estimators": 100 + i}`.
    `shortlist` uses `params=_variant(name, i)`.
  - the violation test (line ~309): name becomes `"logistic_regression" if violation ==
    "explicit" else "ridge" if violation == "max" else "linear_regression"` (the max check runs
    before any registry lookup; 9 distinct ridge alphas keep hashes unique).
Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_candidate_vocabulary.py
tests/test_candidate_models.py tests/test_nodes_candidate_models.py tests/test_registry.py
tests/test_candidate_hash.py tests/test_candidate_set.py tests/test_nodes_design_inference.py
tests/test_nodes_candidate_pipeline.py tests/test_candidate_fanout.py
tests/test_candidate_fanout_tools.py tests/test_graph_state.py tests/test_control_qa_status.py
tests/test_tools_training.py tests/test_nodes_champion.py tests/test_control_rewind.py'`; Ruff
check, Ruff format --check, mypy on the owned source files and changed test files.
Stop rule: any OTHER test failing because it builds a registry-named EstimatorSpec/
TrainingDecision with non-tunable params → stop and report file:line and the params (main
extends this list); do not change it yourself.

### F6 addendum A — baseline-params refusal fixture (2026-10-07)

Receipt: only `tests/test_nodes_candidate_models.py::test_training_refusals_and_exception_wait`
failed. Cause: `_variant(name, 0)` returns `{}`, but the "approved parameters require an explicit
grid" refusal is reachable only when the approved candidate has nonempty params. Keep `_variant`.
Correction (test only): in that test pass
`estimator=EstimatorSpec(name="linear_regression", params={"fit_intercept": True})` to
`candidate_harness(...)`, and change the line-435 assertion to
`== {"fit_intercept": True}`. Expected refusals after the change: `{"C": 2.0}` direct differs from
approved; grid with `params={"C": 1.0}` hits "grid decision must put fixed parameters"; grid with
no `param_grid` hits "approved parameters require an explicit grid"; direct with `param_grid` hits
"direct fitting cannot accept a parameter grid". Checks: the 15-file focused run again, plus Ruff
check/format and mypy on `tests/test_nodes_candidate_models.py`.

F6 acceptance (2026-10-07): focused 15-file run 466 passed / 0 failed (`/tmp/f6_tests2.log`),
Ruff check/format and mypy on the 10 owned files exit 0. Global gate deferred to batch acceptance.

## Fix F7 — the LLM can see model feature names and per-estimator tunable parameters

Goal: remove two live-run failure modes found while specifying H4. (1) `model_interpretation`
asks the LLM for partial-dependence columns and error segments, but its evidence carries only the
feature *count* (`get_model_params` summary `features`), so a real LLM must guess encoded feature
names; a wrong guess fails the node and, in autonomous mode, aborts the run. (2) `TrainingDecision`
validates parameter names against the global F6 table only; a name valid for another estimator
(for example `C` for a random forest) reaches sklearn and fails as a tool error.
Non-goals: no new validation of PDP/error columns in the backend (the tool already returns a clear
`tool_param` failure); no change to `TrainingDecision` or F6 tables.

Owned files: `mcp_server/statlab_tools/tools/training.py`,
`backend/statlab/domains/candidate_models.py`, `tests/test_tools_training.py`,
`tests/test_nodes_candidate_models.py`.

Recipe:
1. `training.py` `get_model_params`: add summary key `"feature_names"` with value
   `[json_scalar(f) for f in fitted.features]`, placed right after `"features"`. Keep `"features"`
   (the count) unchanged.
2. `candidate_models.py`:
   - Import `TUNABLE_PARAMS` from `statlab_contracts.vocabulary`.
   - In `_training`, before `self.runtime.decide(...)`, set
     `tunable = TUNABLE_PARAMS.get(branch.spec.estimator.name)`. Append to the existing question
     string, when `tunable is not None`, the text
     `f" Tunable parameters for {branch.spec.estimator.name}: {', '.join(sorted(tunable))}."`
     (nothing appended otherwise).
   - Directly after the existing line `estimator = branch.spec.estimator` (after the tuning-method
     refusals, before the `if decision.params:` block), when `tunable is not None`, compute
     `unknown = sorted((set(decision.params) | set(decision.param_grid or {})) - tunable)`; if
     nonempty raise `_RefusalError(f"parameters {unknown!r} are not tunable for {estimator.name}; allowed: {sorted(tunable)!r}")`.
   - In `_interpretation`, append to the question string the sentence
     `" Partial dependence columns and error segments must be entries of model_summary.feature_names."`
3. `tests/test_tools_training.py::test_get_model_params_reports_metadata`: add
   `assert gm.summary["features"] == 2` and `assert gm.summary["feature_names"] == ["f1", "f2"]`.
4. `tests/test_nodes_candidate_models.py::test_training_refusals_and_exception_wait` (approved
   candidate is `linear_regression` with `{"fit_intercept": True}` since F6 addendum A):
   - `params={"C": 2.0}` becomes `params={"fit_intercept": False}` (still "initial direct
     parameters differ from approved candidate").
   - grid `params={"C": 1.0}` becomes `params={"fit_intercept": True}` (still "grid decision must
     put fixed parameters in param_grid").
   - direct `param_grid={"C": (0.5, 1.0)}` becomes `param_grid={"positive": (True, False)}` (still
     "direct fitting cannot accept a parameter grid").
   - Add, after that case and before `names = ...`: response
     `TrainingDecision(rationale="Grid over a foreign parameter", method="grid", param_grid={"C": (0.5, 1.0)})`;
     `_failed(await _run(h, 8), "parameters ['C'] are not tunable for linear_regression; allowed: ['fit_intercept', 'positive']")`.
   - The final "no train/tune call" assertion stays.
5. Same file, `test_bounded_grid_and_cv_keep_reserved_effective_parameters_out_of_calls`: right
   after `fit = await ...` add
   `messages = next(m for output, _, m in h.llm.calls if output is TrainingDecision)` and
   `assert "Tunable parameters for linear_regression: fit_intercept, positive." in messages[-1]["content"]`.

Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_tools_training.py tests/test_nodes_candidate_models.py tests/test_nodes_candidate_pipeline.py tests/test_nodes_champion.py'`;
Ruff check, Ruff format --check and mypy on the four owned files. Stop and report on any other
failure; do not adjust other tests.

F7 acceptance (2026-10-07): focused 4-file run 109 passed / 0 failed (`/tmp/f7_tests.log`); Ruff
check/format and mypy on the 4 owned files exit 0; read-only coverage probe of
`statlab.domains.candidate_models` over the 3 node test files: 209 statements / 84 branches, 100%.

## H4 — rich offline stand-in (`--llm offline-rich`)

Goal: exercise, offline and for free, the LLM-driven branches the minimal stand-in never takes, so
backend defects surface before any paid run: profile selections and plots, bounded grid tuning,
one retune loop, classification curves/threshold/CV/learning curve, interpretation (coefficients,
partial dependence, error segments), a second search wave, and champion report sections.
Non-goals: no data judgement (choices are fixed rules), no metric override, no preprocessing or
feature-engineering changes, no new scenario expectations, no change to `--llm offline` output.

Prerequisite: F7 (the stand-in reads `model_summary.feature_names`).

Owned files: `integration/offline_llm.py`, `integration/run_scenarios.py`.
Main owns `integration/SCENARIOS.md` (one paragraph on the mode).

### Prompt facts the stand-in relies on (verified)

- The first `user` message content is `canonical(state_summary) + "\n\n" +
  canonical(list(evidence)) + "\n\n" + question` (`backend/statlab/llm/prompts.py`
  `PromptContext.messages`). Canonical JSON never contains a raw newline, so
  `content.split("\n\n", 2)` yields `[state_json, evidence_json, question]`. Repair messages are
  appended later; always parse the first `user` message.
- `state_summary["candidate"]` is the `CandidateProgress` dump (keys `public_id`, `spec`, `wave`,
  `retune_count`, ...) or `null`; `spec` has `is_baseline` and `estimator`
  (`name`, `params`, `calibrate`, `class_weight`).
- Evidence for `ProfileDecision` is a list of `{"tool": name, "result": ToolSuccess dump}`; the
  `profile_schema` entry has `result.summary.columns` = `{column: {"dtype": str, ...}}`.
- Evidence for `TrainingDecision`, `EvaluationActions`, `InterpretationDecision`: element 0 is the
  pipeline dump (training) or the training summary (evaluation, interpretation). The training
  summary has `model_summary` = `get_model_params` summary with `task`, `classes` (list) and,
  since F7, `feature_names` (list of str).
- Evidence for `SearchDecision`: element 0 is `{"comparison": [{"candidate", "spec",
  "validation", "retune_count"}], "next_wave": int, "remaining_candidates": int, ...}`.
- Engine retunes only while `retune_count < constraints.max_retune_loops`; the planner asks for a
  wave only up to `max_search_waves` and once per wave. Baselines are always linear estimators.

### Fixed rule tables (module constants in `offline_llm.py`)

- `_GRID: dict[str, dict[str, list[JsonValue]]]`: `logistic_regression` `{"C": [0.5, 1.0]}`,
  `linear_regression` `{"fit_intercept": [True, False]}`, `ridge` `{"alpha": [0.5, 1.0]}`.
- `_RETUNE: dict[str, dict[str, JsonValue]]`: `logistic_regression` `{"C": 2.0}`,
  `linear_regression` `{"fit_intercept": True}`, `ridge` `{"alpha": 2.0}`.
- `_VARIANT: dict[str, dict[str, JsonValue]]`: `logistic_regression` `{"C": 0.5}`,
  `linear_regression` `{"fit_intercept": False}`, `ridge` `{"alpha": 0.5}`.
- `_LINEAR = frozenset({"logistic_regression", "linear_regression", "ridge"})`.
- `REPORT_HEADING = "Stand-in methodology note"`.

### offline_llm.py recipe

1. `OfflineProvider.__init__(self, *, rich: bool = False)`; store `self.rich`.
2. In `__call__`, keep everything; additionally pass the first user message content (the
   `content` of the first message whose role is `user`; `RuntimeError("offline provider: no user
   message")` if none) to `_answer` as `first_user: str`. `_answer` signature becomes
   `_answer(self, title, user_text, first_user, scenario)`.
3. In `_answer`, before the `_RATIONALE_ONLY` check: if `self.rich` and `title` is one of
   `ProfileDecision`, `TrainingDecision`, `EvaluationActions`, `ValidationConclusion`,
   `InterpretationDecision`, `SearchDecision`, `ChampionChoice`, parse with
   `_parts(first_user) -> tuple[dict[str, JsonValue], list[JsonValue]]` (split as above,
   `json.loads` both; `RuntimeError("offline provider: unexpected prompt layout")` if fewer than 3
   parts or the decoded types are not dict/list) and return the matching rich builder below.
   `ChampionChoice` returns `_champion(user_text)` plus key `"sections"` =
   `[{"heading": REPORT_HEADING, "body": "Choices in this run came from fixed offline rules."}]`.
4. Helpers: `_obj(value: JsonValue, what: str) -> dict[str, JsonValue]` and
   `_list(value: JsonValue, what: str) -> list[JsonValue]` raise
   `RuntimeError(f"offline provider: malformed {what}")` on the wrong type.
   `_candidate(state) -> dict[str, JsonValue]` = `_obj(state.get("candidate"), "candidate")`.
5. `_rich_profile(evidence, contract) -> dict`: find the first evidence entry with
   `tool == "profile_schema"` (`RuntimeError` if none); `columns` = its
   `result.summary.columns` dict, in insertion order. Excluded: `contract.target.column`,
   `contract.features.id_columns`, `contract.features.exclude`. Numeric = dtype string lowercased
   starts with `int`, `uint`, `float` or `decimal`, then overridden by `contract.features.roles`
   (`"numeric"` adds, any other role removes), exactly like
   `FoundationExecutor._validate_profile`. Lists over non-excluded columns: `num` and `cat`
   (= the rest). Return `{"rationale": RATIONALE, "numeric": num[:2], "categorical": cat[:2],
   "correlations": num[:2] if len(num) >= 2 else [], "outliers": num[:1], "plots": plots}` where
   `plots` = `[{"kind": "distribution", "column": target}]` plus, when `num + cat` is nonempty,
   `{"kind": "target_relation", "column": (num + cat)[0]}`.
6. `_rich_training(state, contract) -> dict`: `spec = _obj(candidate["spec"])`,
   `estimator = _obj(spec["estimator"])`, `name = str(estimator["name"])`,
   `retune = int(candidate["retune_count"])`, `baseline = spec["is_baseline"] is True`,
   `wave = int(candidate["wave"])`. Explicit method: `tuning = contract.modelling.tuning`,
   `explicit = "method" in tuning.model_fields_set`.
   - If explicit and `tuning.method == "grid"`: grid `_GRID[name]` if `name in _GRID` else
     direct (below).
   - Else if explicit (method `none` or `random`): direct.
   - Else if `baseline and wave == 1 and retune == 0 and name in _GRID`: grid.
   - Else: direct.
   Grid answer: `{"rationale": RATIONALE, "method": "grid", "param_grid": _GRID[name]}`.
   Direct answer: `{"rationale": RATIONALE, "method": "direct", "params": _RETUNE[name] if
   retune > 0 and name in _RETUNE else {}}`.
7. `_rich_evaluation(state, evidence) -> dict`: `trained = _obj(evidence[0])`,
   `summary = _obj(trained["model_summary"])`, `task = summary["task"]`,
   `classes = _list(summary["classes"])`; spec/estimator as above. Classification with
   `len(classes) == 2`: `curves = ["confusion", "roc", "pr", "calibration"]`,
   `threshold_objective = "f1"`. Classification otherwise: `curves = ["confusion"]`,
   `threshold_objective = None`. Regression: `curves = []`, `None`.
   `diagnostics = baseline and estimator["calibrate"] is not True`. Return rationale, `curves`,
   `threshold_objective`, `cross_validation = diagnostics`, `learning_curve = diagnostics`.
8. `_rich_conclusion(state, contract) -> dict`: retune when `baseline and wave == 1 and
   retune_count == 0 and contract.constraints.max_retune_loops >= 1 and name in _RETUNE`:
   `{"rationale": RATIONALE, "retune": True, "retune_reason": "Offline rule: one bounded retune of the baseline."}`;
   otherwise `{"rationale": RATIONALE}`.
9. `_rich_interpretation(state, evidence) -> dict`: `features` = list of str from
   `trained["model_summary"]["feature_names"]`. Return rationale,
   `coefficients = name in _LINEAR and estimator["calibrate"] is not True`,
   `partial_dependence = features[:1]`, `error_by = features[:1]`.
10. `_rich_search(evidence, scenario) -> dict`: `context = _obj(evidence[0])`. Return
    `{"rationale": RATIONALE}` (empty proposal) when `scenario.expect.candidate_count is not None`
    (the scenario pins its count), or `remaining_candidates < 1`, or no comparison item has
    `spec.is_baseline is True`, or the baseline estimator name is not in `_VARIANT`.
    Otherwise: `base = CandidateSpec.model_validate(baseline_spec)`;
    `draft = CandidateSpec.model_validate({**baseline_spec, "label": f"{name} variant",
    "rationale": "Offline stand-in variant of the baseline.", "is_baseline": False,
    "estimator": {**baseline_estimator, "params": _VARIANT[name]},
    "varies_from_baseline": []})`; `final = draft.model_copy(update={"varies_from_baseline":
    tuple(sorted(changed_dimensions(base, draft)))})` then re-validate with
    `CandidateSpec.model_validate(final.model_dump(mode="json"))`. Return
    `{"rationale": RATIONALE, "candidates": [final dump], "family_justifications":
    {"linear": "Offline stand-in variant of the linear baseline."}}`.
11. Everything else (non-rich titles, or `self.rich` false) keeps the current behaviour.

### run_scenarios.py recipe

- `--llm` choices become `("live", "offline", "offline-rich")`.
- `provider = OfflineProvider(rich=args.llm == "offline-rich") if args.llm != "live" else None`.
- No other change (`live=args.llm == "live"`; output dir and summary already use `args.llm`).

### Edge cases

- `candidate` null for a rich candidate title: `RuntimeError` via `_obj` (a backend contract
  change, not a stand-in choice).
- `feature_names` empty: PDP and `error_by` are empty lists.
- Multiclass/regression never request ROC/PR/calibration/threshold (tool constraints: binary only).
- A pinned `candidate_count` suppresses wave 2 (S05).

### Checks (implementer)

- `.venv/bin/python -m ruff check integration/offline_llm.py integration/run_scenarios.py`
- `.venv/bin/python -m ruff format --check integration/offline_llm.py integration/run_scenarios.py`
- `.venv/bin/python -m mypy integration/offline_llm.py integration/run_scenarios.py`
- Smoke: `.venv/bin/python -m integration.run_scenarios --llm offline-rich --only S01 > /tmp/h4_s01.log 2>&1`;
  report the exit and the summary table line. A FAIL is reported, not fixed.
- Plain-mode regression: `.venv/bin/python -m integration.run_scenarios --llm offline --only S01 > /tmp/h4_plain.log 2>&1`, report exit.

Main then runs the full rich suite and diagnoses failures (stand-in rule vs backend defect).

### H4 addendum A — cumulative design confirmation (2026-10-07)

Rich S01 (`integration/out/20261007T143553Z-offline-rich`) ended `cancelled`: after the wave-2
proposal the engine re-ran `experiment_design`, which by accepted design (chunk 28 lines 38/121)
must confirm the exact checkpointed cumulative set; the stand-in rebuilt the contract shortlist and
was refused ("approved shortlist must match existing candidate specifications"). Stand-in gap, not
a backend defect.

Correction (`integration/offline_llm.py`, both modes): for title `DesignDecision`, first parse
`state, _ = _parts(first_user)`. If `state.get("checkpointed_candidates")` is a nonempty list,
return `{"rationale": RATIONALE, "candidate_set": {"candidates": <that list unchanged>},
"family_justifications": {"linear": J, "random_forest": J, "hist_gradient_boosting": J}}` with
`J = "Offline stand-in confirmation of the checkpointed cumulative set."`. Otherwise keep the
existing `_design(...)` path unchanged (the `revised_candidates` branch included).
Checks: Ruff check/format and mypy on `integration/offline_llm.py`; rerun both S01 smokes
(`--llm offline-rich --only S01 > /tmp/h4_s01b.log`, `--llm offline --only S01 > /tmp/h4_plainb.log`).

### H4 addendum B — shared-once check counts design per wave (2026-10-07)

Full rich suite `integration/out/20261007T144021Z-offline-rich`: 18/19 PASS; S16 failed only
`E-shared_completed_once` with `experiment_design: 2`. The second completion is the accepted
wave-2 cumulative confirmation (chunk 28), not a restart re-run. Check fix in
`integration/checks.py` (`expect.shared_completed_once` block): query
`SELECT COALESCE(MAX(wave), 1) FROM candidates WHERE run_id = ?` for `main_run_id` (via
`_db_rows`; value `waves`, an int). Allowed completions per node: `waves` for
`experiment_design`, 1 for every other shared node. Pass iff every `per_node` count is within its
allowance and `duplicates == 0`; detail string becomes `f"per_node={per_node} waves={waves} duplicates={duplicates}"`.
Checks: Ruff check/format and mypy on `integration/checks.py`; rerun
`--llm offline-rich --only S16` and `--llm offline --only S16`.

### F6 addendum B — exhaustive match branch (2026-10-07)

`make check` (F6+F7+H4 candidate) exit 2: coverage 11385 statements / 3284 branches, one partial
branch `contracts/statlab_contracts/vocabulary.py 228->exit` (the implicit no-case-matched exit
of `match kind` in `_matches_kind`; unreachable since `TunableKind` is closed). Correction: change
the final `case "max_features":` to `case _:` (body unchanged; mypy narrows `kind` to the
remaining literal). Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_candidate_vocabulary.py tests/test_candidate_models.py'`;
Ruff check/format and mypy on `contracts/statlab_contracts/vocabulary.py`.

## Live run 2 finding (2026-10-07, `integration/out/20261007T183001Z-live`, S01 only, $0.0041)

S01 ended `cancelled` at `data_profiling`: the live LLM's `ProfileDecision` put a numeric-dtype
column in `categorical`; `_validate_profile` refused ("summarize_categorical selection has
unknown, repeated or incompatible columns"), the node failed, the failure gate defaulted to abort.
The rule is correct (the tool rejects numeric columns too), but the LLM was neither told the rule
nor given a chance to correct it: semantic refusals bypass the client's one-repair loop.

## Fix F8 — semantic decision checks join the one-repair loop

Goal: node validators that judge an LLM decision against run facts run inside
`LlmClient.complete`'s single repair round, so the LLM sees the refusal reason once and can
correct it. Applied to the three nodes that already have a standalone validator: profiling,
experiment design, inference. Non-goals: no change to post-approval revalidation (kept), no new
validators for training/evaluation/interpretation, no change to repair count, no PLAN change
(the repair budget stays "exactly once").

Owned files: `backend/statlab/llm/client.py`, `backend/statlab/llm/__init__.py` (only if it
re-exports client names; add `DecisionRejectedError` beside the existing exports),
`backend/statlab/domains/runtime.py`, `backend/statlab/domains/foundation.py`,
`backend/statlab/domains/design_inference.py`, `tests/test_llm_client.py`,
`tests/test_nodes_foundation.py`, `tests/test_nodes_design_inference.py`,
`tests/test_control_router.py`, `tests/test_nodes_candidate_pipeline.py`.

Recipe:
1. `llm/client.py`:
   - Add `class DecisionRejectedError(ValueError)` with docstring "A parsed decision that failed
     the caller's semantic check; repaired exactly like a schema validation error."
   - `complete(...)` gains keyword `check: Callable[[T], None] | None = None` (last parameter),
     passed to `_complete(..., check=check)`. `_complete` gains keyword
     `check: Callable[[T], None] | None = None` (after `sink`). `complete_prose`/`complete_setup`
     unchanged (they pass nothing).
   - In `_complete`'s `try`, replace `return output.model_validate_json(prior_content)` with:
     `result = output.model_validate_json(prior_content)`; `if check is not None: check(result)`;
     `return result`. `DecisionRejectedError` is a `ValueError`, so the existing
     `except (ValidationError, ValueError)` handles repair and the final `LlmOutputError`.
   - `_validation_errors`: before the final fallback return, add
     `if isinstance(exc, DecisionRejectedError): return {"output": str(exc)}`.
2. `domains/runtime.py` `decide(...)`: add keyword `check: Callable[[T], None] | None = None`;
   pass `check=check` to `self.llm.complete(...)`. The `decision_override` path ignores it.
3. `domains/foundation.py`:
   - Import `DecisionRejectedError` from `statlab.llm.client`.
   - Add module function
     `def _rejecting[T](validate: Callable[[T], None]) -> Callable[[T], None]` returning an inner
     `check(decision: T) -> None` that calls `validate(decision)` and converts `_RefusalError` to
     `DecisionRejectedError(str(exc))` (`raise ... from exc`).
   - `_validate_profile`: the column-selection refusal message becomes
     `f"{tool} selection has unknown, repeated or incompatible columns; allowed: {sorted(compatible)!r}"`.
     Plot and skill messages unchanged.
   - `_profile`: pass `check=_rejecting(lambda d: self._validate_profile(invocation, d, schema.summary, problem.target))`
     to the `ProfileDecision` `decide`, and append to its question:
     `" summarize_categorical takes only non-numeric columns; numeric, correlations and outliers take only numeric columns (dtype or contract role)."`
     Keep the existing post-decide and post-approval `_validate_profile` calls.
4. `domains/design_inference.py` (import `_rejecting` from foundation beside `_RefusalError`):
   - `_design`: `check=_rejecting(lambda d: self.validate_design(invocation, d, registry.summary, problem))`.
   - `_inference`: `check=_rejecting(lambda d: self._validate_inference(invocation, d, registry, problem) and None)`
     is NOT allowed (returns tuple); instead define a local
     `def check_inference(decision: InferenceDecision) -> None: self._validate_inference(invocation, decision, registry, problem)`
     and pass `check=_rejecting(check_inference)`.
   - Keep every existing validation call.
5. Test fakes: add the keyword parameter `check: Callable[[T], None] | None = None` after
   `recovery` to the `complete` overrides in `tests/test_control_router.py` (~line 47),
   `tests/test_nodes_candidate_pipeline.py` (~line 944) and `tests/test_nodes_foundation.py`
   (`OfflineLlm.complete`). Only `OfflineLlm` uses it: in `__init__` add
   `self.checks: dict[type[BaseModel], Callable[..., None]] = {}`; in `complete`, when `check` is
   not None, `self.checks[output] = check` (before returning). Fakes never call `check`.
6. New tests:
   - `tests/test_llm_client.py::test_semantic_check_rejection_gets_one_repair`: schema tier as in
     `test_schema_tier_custom_validator_gets_exactly_one_repair` (monkeypatch
     `supports_response_schema` True, model `"openai/test"`, output `Result`); fake returns
     `{"value":"bad"}` then `{"value":"good"}` (use `Result`'s actual field; read its definition
     at the top of the file and use any two distinct valid values). `check` raises
     `DecisionRejectedError("value is not allowed; allowed: ['good']")` when value is the first
     one. Assert the result is the second value, 2 calls, and the second call's last message
     content contains `value is not allowed; allowed: ['good']`.
   - `tests/test_llm_client.py::test_semantic_check_rejected_twice_is_output_error`: check always
     raises `DecisionRejectedError("never")`; assert `LlmOutputError` with
     `error.value.failure.field_errors == {"output": "never"}` (read `LlmOutputError` in
     `backend/statlab/errors.py` for the attribute holding the `Failure`; use that attribute) and
     2 calls.
   - `tests/test_nodes_foundation.py::test_profile_check_feeds_llm_repair`: `await
     foundation.advance(2)`; `await foundation.step()` (default profile decision completes);
     `check = foundation.llm.checks[ProfileDecision]`; `check(ProfileDecision(rationale="ok",
     numeric=("x",)))` returns None; `with pytest.raises(DecisionRejectedError, match="allowed")`:
     `check(ProfileDecision(rationale="bad", categorical=("x",)))`. Also assert the
     `ProfileDecision` call's last message content contains `summarize_categorical takes only
     non-numeric columns`.
   - `tests/test_nodes_design_inference.py::test_design_and_inference_checks_feed_llm_repair`:
     `h, _ = await prepared(migrated_db, project, tmp_path)`; `assert (await design(h)).status ==
     "completed"`; `design_check = h.llm.checks[DesignDecision]`; `design_check(shortlist())` is
     None; `with pytest.raises(DecisionRejectedError)`: `design_check(shortlist("logistic_regression"))`
     (classification estimator for a continuous task). Then `await
     DesignInferenceExecutor(h.runtime)(h.invocation(5))` completes; `inference_check =
     h.llm.checks[InferenceDecision]`; valid: the prepared `InferenceDecision` (ols, ("x",)) is
     None; invalid `InferenceDecision(model="logit", predictors=("x",), rationale="r")` raises
     `DecisionRejectedError`.

Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_llm_client.py tests/test_nodes_foundation.py tests/test_nodes_design_inference.py tests/test_control_router.py tests/test_nodes_candidate_pipeline.py tests/test_nodes_candidate_models.py tests/test_nodes_champion.py tests/test_nodes_candidate_search.py'`;
Ruff check, Ruff format --check and mypy on all owned files. Stop and report on any other failure.

F8 resolutions: `Result` has one field `value: str` (use `"bad"`/`"good"`); the `Failure` is
`LlmOutputError.failure`; `statlab/llm/__init__.py` re-exports `LlmClient` only — leave it
unchanged and import `DecisionRejectedError` from `statlab.llm.client` everywhere. In step 4
the lambda form for `_inference` is not used; only the local `check_inference` function.

## H5 — rich stand-in exercises the semantic repair round (after F8)

Goal: prove offline, end to end, that a semantically invalid first answer is repaired (F8) rather
than failing the node. Owned file: `integration/offline_llm.py`.

Recipe: in `_rich_profile`, add parameter `repaired: bool`. When `repaired` is False and `num` is
nonempty, return the normal answer but with `"categorical": [*cat[:1], num[0]]` (a numeric column
where only non-numeric columns are allowed). Otherwise return the normal answer. In `_answer`, the
call becomes `_rich_profile(evidence, contract, "Repair the output" in user_text)` (`user_text`
joins every user message, and the client's repair message starts with "Repair the output").

Checks: Ruff check/format and mypy on `integration/offline_llm.py`;
`.venv/bin/python -m integration.run_scenarios --llm offline-rich --only S01 S04 > /tmp/h5.log 2>&1`
(exit and both summary rows). Main verifies two `llm_calls` rows for the profiling execution.

## Live run 3 finding (2026-10-07, `integration/out/20261007T190758Z-live`, S01, $0.2191, 39 calls)

Profiling now passes (F8). All 5 wave-1 candidates completed training/evaluation/interpretation;
the LLM proposed 2 wave-2 candidates (`SearchDecision`, 54k prompt tokens, OK). The cumulative
`experiment_design` re-run's single `DesignDecision` request failed inside `acompletion` within
91 ms with a non-transient exception -> `LlmCallError(bug, "LLM client failed")` -> failure gate ->
abort. Balance was fine ($5.04 via the free balance endpoint); the design prompt is smaller than
the search prompt that succeeded, so context length is unlikely. The exception type/status is
discarded, so the cause is unknown. Cost note: S01 live is ~$0.22 (5 candidates; interpretation
prompts ~40k tokens each), not ~$0.15.

## Fix F9 — provider failures are diagnosable; HTTP 500/502 are transient

Goal: the next failure names its provider exception class and HTTP status in the API log (never
the provider text, which may echo secrets — existing tests assert that), and server-side 500/502
errors get the bounded transient retry like 503. Extends chunk 25 V6's transient list.

Owned files: `backend/statlab/llm/client.py`, `tests/test_llm_client.py`.

Recipe:
1. Import `InternalServerError` and `BadGatewayError` from `litellm.exceptions` (beside the
   existing imports) and add both to `_TRANSIENT_ERRORS`.
2. In `_request`, in the final `except Exception as exc:` handler, after computing `error_class`
   and before `raise LlmCallError(...)`, add
   `_LOGGER.warning("LLM request failed: %s status=%s class=%s", type(exc).__name__, getattr(exc, "status_code", None), error_class)`.
3. `tests/test_llm_client.py::test_provider_exception_is_safely_wrapped_and_logged`:
   - add parametrize rows `("internal", "transient")` and `("bad_gateway", "transient")`, built as
     `InternalServerError(credential, "provider", "request")` and
     `BadGatewayError(credential, "provider", "request")` (same positional pattern as
     `ServiceUnavailableError`; import both);
   - add a `caplog: pytest.LogCaptureFixture` parameter; assert
     `f"LLM request failed: {type(cause).__name__}" in caplog.text`, `"SECRET_KEY" not in caplog.text`,
     and for `rate_limit` additionally `"status=429" in caplog.text`.
Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_llm_client.py tests/test_control_recovery.py'`
Ruff check/format and mypy on the two files.

## Live run 4 finding (2026-10-07, `integration/out/20261007T191756Z-live`, S01, $0.0314)

F9 logging: no provider failure this time. S01 ended `cancelled` at the initial
`experiment_design`: the LLM put tuning grids into candidate params (`max_depth: (None, 5, 10)`,
`learning_rate: (0.05, 0.1)`); F6 rejected them ("... is not a valid positive int or none"), the
repair round repeated the same grids, `LlmOutputError` -> failure gate -> abort. The message
names a kind mismatch, not the actual mistake (a sequence where one value is required).

## Fix F10 — say "one value, grids later" at design time

Owned files: `contracts/statlab_contracts/vocabulary.py`,
`backend/statlab/domains/design_inference.py`, `tests/test_candidate_vocabulary.py`.

Recipe:
1. `vocabulary.py` `tunable_param_errors`: between the unknown-key branch and the kind branch add
   `elif isinstance(value, tuple):` appending
   `f"{key}={value!r} is a sequence; a parameter takes one value (tuning grids are chosen at model_training as param_grid)"`.
2. `design_inference.py` `_design` question: append the sentence
   `" Estimator params hold single fixed values (empty for defaults); never put tuning grids or lists there, grids are chosen later at model_training."`
3. `tests/test_candidate_vocabulary.py`: remove `("max_features", (1, 2))` from the
   `test_tunable_kinds_reject` table; add test `test_tunable_sequence_value_names_the_mistake`:
   `tunable_param_errors({"max_depth": (None, 5)}, None) == ["max_depth=(None, 5) is a sequence; a parameter takes one value (tuning grids are chosen at model_training as param_grid)"]`.
Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_candidate_vocabulary.py tests/test_candidate_models.py tests/test_nodes_design_inference.py tests/test_nodes_candidate_models.py'`;
Ruff check/format and mypy on the three owned files.

## Live run 5 finding (2026-10-07, `integration/out/20261007T192326Z-live`, S01, $0.2384)

F10 worked: design, inference and all 5 candidates completed. The wave-2 `SearchDecision` added a
third linear candidate without `family_justifications`; `CandidateSearchPlanner` ran
`validate_design` after `decide`, raised `_RefusalError("more than two candidates in a family
need justification")` from inside `engine._fanout`. The run task crashed (API log ERROR, snapshot
`execution_failed=true` by chunk 34 design, SQL status left `running`); the harness ignored
`execution_failed` and waited the full 1200 s timeout.

## Fix F11 — search proposals get the repair round; unusable proposals end search

Owned files: `backend/statlab/domains/candidate_search.py`,
`backend/statlab/domains/design_inference.py`, `tests/test_nodes_candidate_search.py`,
`integration/driver.py` (H6).

Recipe:
1. `design_inference.py` `validate_design`: family refusal message becomes
   `f"more than two candidates in family {family!r} need a family_justifications entry"`.
2. `candidate_search.py`:
   - Add `import logging`, `_LOGGER = logging.getLogger(__name__)`; import `_rejecting` from
     `statlab.domains.foundation` and `LlmOutputError` from `statlab.errors`.
   - In `__call__`, after `self.runtime.context(invocation)` and before building `comparison`,
     compute (moved up from after `decide`): `old`, `prior`, `justifications` exactly as today,
     plus `registry = _object(design.summary.get("registry"), "design registry")`,
     `problem = problem_from_state(state)` and
     `existing = tuple(branch.spec for branch in state.branches)`.
   - Define local `def cumulative(proposal: SearchDecision) -> None`: return immediately when
     `proposal.candidates` is empty; else build
     `CandidateSet(candidates=(*existing, *proposal.candidates))`, mapping `ValidationError` to
     `_RefusalError(f"invalid cumulative candidate proposal: {reasons}")` (`from exc`) where
     `reasons = "; ".join(str(error["msg"]) for error in exc.errors(include_input=False, include_context=False))`;
     then `DesignInferenceExecutor(self.runtime).validate_design(invocation,
     DesignDecision(rationale=proposal.rationale, candidate_set=<that set>,
     family_justifications={**justifications, **proposal.family_justifications}), registry,
     problem, require_saved=False)`.
   - Call `decide(..., check=_rejecting(cumulative))` inside
     `try: ... except LlmOutputError: _LOGGER.warning("Run %s wave %d search proposal stayed invalid after repair; search ends", state.run_id, wave); return ()`.
   - After decide: `if not proposal.candidates: return ()`; `cumulative(proposal)`;
     `return proposal.candidates`. Remove the old post-decide block it replaces.
3. `tests/test_nodes_candidate_search.py`:
   - The existing `match="cumulative"` assertion stays valid.
   - New `test_search_check_feeds_repair_and_unrepairable_proposal_ends_search(migrated_db,
     project, tmp_path, monkeypatch, caplog)`: `h, _ = await candidate_harness(...)`;
     `await joined(h)`; `planner = CandidateSearchPlanner(h.runtime)`;
     `original = h.state.branches[0].spec`; `new = proposal(original)`; response
     `SearchDecision(rationale="Validation comparison", candidates=(new,))`;
     `assert await planner(h.state, 2) == (new,)`; `check = h.llm.checks[SearchDecision]`;
     `check(SearchDecision(rationale="Nothing new"))` is None;
     `check(SearchDecision(rationale="ok", candidates=(new,)))` is None;
     `with pytest.raises(DecisionRejectedError, match="cumulative")`:
     `check(SearchDecision(rationale="dup", candidates=(original,)))`.
     Then define `async def unrepairable(*args: object, **kwargs: object) -> SearchDecision:
     raise LlmOutputError(Failure(error_class="tool_param", message="LLM output remained invalid after one repair"))`,
     `monkeypatch.setattr(h.llm, "complete", unrepairable)`;
     `assert await planner(h.state, 2) == ()` and `"search ends" in caplog.text`.
     (Imports: `DecisionRejectedError` from `statlab.llm.client`, `LlmOutputError` from
     `statlab.errors`; `Failure` is already imported.)
4. H6 `integration/driver.py` wait loop: right after `outcome.final_status = status`, add: if
   `snapshot.get("execution_failed") is True`: set `outcome.final_status = "execution_failed"`
   and `break` (no cancel call). G2 then reports `final=execution_failed`.

Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_nodes_candidate_search.py tests/test_nodes_design_inference.py tests/test_graph_engine.py tests/test_graph_engine_tools.py'`;
Ruff check/format and mypy on the four owned files;
`.venv/bin/python -m integration.run_scenarios --llm offline-rich --only S01 > /tmp/f11_rich.log 2>&1`.

F11 addendum A: `Failure(error_class="tool_param", ...)` requires nonempty `field_errors`. In the
new test use `Failure(error_class="tool_param", message="LLM output remained invalid after one repair", field_errors={"output": "still invalid"})`
(the shape `LlmClient` produces). Rerun the focused pytest command only.
