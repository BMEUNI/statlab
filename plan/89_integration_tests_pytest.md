---
status: planned
dependencies: [88]
completion_date: null
implementation_notes: "89a accepted 2026-10-27 (pytest suite, behaviour parity); 89b-89d pending"
---

# Chunk 89 — integration scenarios as a pytest + DeepEval suite with record/replay

User request 2026-10-27: the home-made scenario runner is not how the industry does it; move to a
standard framework. Decided with the user (2026-10-27):

| Question | Decision |
|---|---|
| What is checked | behaviour (today's checks, as pytest asserts) **plus** a few judged-quality metrics |
| Library | **pytest + DeepEval** (`deepeval==4.3.2`, supports Python 3.14) |
| Live traffic | **record/replay** at the `LlmClient` boundary (cassettes) |
| Delivery | this plan first; implement only after the user's go-ahead |

The goal stays (memory "scenario-harness-goal"): prove the backend runs as designed; model
accuracy is not the goal. Judged metrics measure LLM *output quality* and are reported
separately so they never hide a behaviour failure.

## Why these choices (research 2026-10-27)

- **pytest** is the standard runner: fixtures, markers, parametrization, `-k` selection, JUnit XML
  for CI, plugins. Today's runner re-implements all of that (`integration/run_scenarios.py`,
  425 lines: argument parsing, selection, summary table).
- **DeepEval** is the most pytest-native LLM eval library: `assert_test(test_case, metrics)`
  inside ordinary pytest tests, goldens parametrized with `@pytest.mark.parametrize`; runs fully
  locally without a Confident AI account. It has a deterministic `ToolCorrectnessMetric`
  (expected vs called tools) and LLM-judge metrics (`FaithfulnessMetric`, `GEval`); the judge is
  any `DeepEvalBaseLLM`, so it can go through our own LiteLLM route.
- Rejected: **pydantic-evals** (lighter and typed, but not pytest-native; judge goes through
  pydantic-ai), **Inspect AI** (sandboxed agent benchmarks, own runner, not pytest), **promptfoo**
  (YAML/Node CLI, prompt-level, not a Python backend e2e).
- **Record/replay** (the VCR/cassette pattern) is how teams keep LLM integration tests
  deterministic and free in CI. HTTP-level VCR is a poor fit for LLM SDKs (streaming, keys in
  cassettes), so we record at our own SDK seam: `LlmClient(settings, acompletion=...)`, the same
  seam the offline stand-in already uses (`integration/run_scenarios.py:352-353`).

Sources: deepeval.com/docs/evaluation-unit-testing-in-ci-cd, deepeval.com/docs/metrics-tool-correctness,
pydantic.dev/docs/ai/evals, braintrust.dev/articles/deepeval-alternatives-2026, pypi.org/project/deepeval.

## Target layout

```
integration/
  scenarios.toml, SCENARIOS.md          unchanged catalogue (20 scenarios)
  model.py, datasets.py, driver.py,     reused as the library layer
  services.py, evidence.py, offline_llm.py
  checks.py                             check functions kept; return CheckResult as today
  cassette.py                    (new)  record/replay acompletion provider
  judge.py                       (new)  DeepEvalBaseLLM over LiteLLM, model from ModelRouter
  pytest.ini                     (new)  own config: testpaths integration/tests, markers
  tests/conftest.py              (new)  options, session services, one cached run per scenario
  tests/test_behaviour.py        (new)  G1–G7, expectations, extra checks as asserts
  tests/test_judged.py           (new)  DeepEval metrics
  run_scenarios.py               removed in 89d after parity
```

`make test` is unaffected (`testpaths = ["tests"]`, coverage source excludes `integration/`).
mypy strict already covers `integration/`.

## Behaviour (pytest)

- Options (`conftest.py`): `--llm {offline,offline-rich,record,replay,live}` (required),
  `--budget-usd` (default 2.0), `--cassettes DIR` (default `integration/.cache/cassettes`,
  already gitignored). Scenario selection uses plain `-k S01`.
- Session fixtures: workspace + isolated DB/data dir, MCP process, API server, signed-in driver,
  budget — the bodies of today's `_suite` / `_run_one` moved into fixtures, not rewritten.
- `scenario_run(scenario)` fixture: runs a scenario **once** per session (cache by ID) and returns
  `Evidence` + scenario folder; every test reuses it. A run error is reported once, then the
  dependent tests of that scenario fail with "scenario run failed: …".
- Tests, each parametrized over the scenarios (`ids=scenario.id`):
  `test_global_invariants` (G1–G7), `test_expectations`, `test_extra_checks`; each asserts all its
  `CheckResult`s and shows every failing check ID with detail in one assertion message.
  Soft checks become `test_soft_checks` marked `@pytest.mark.soft`, reported with
  `record_property` and never failing (as today).
- Budget: a session-level guard skips the remaining scenarios once spend reaches `--budget-usd`
  (live/record), like the current suite stop.
- Output: `integration/out/<ts>-<mode>/` keeps today's per-scenario evidence; summary via
  `--junitxml` plus a short `summary.md` written in `pytest_sessionfinish`.

## Record / replay (`cassette.py`)

- `CassetteProvider(mode: Literal["record", "replay"], root: Path)`: callable with the
  `acompletion` signature. Key = SHA-256 of canonical JSON of `model`, `messages`,
  `response_format`/`tools` when present (never `api_key`, `api_base` or other secrets).
- record: calls `litellm.acompletion`, stores `{"request": <keyed fields>, "response":
  response.model_dump()}` at `<root>/<scenario>/<key>.json`; replay: rebuilds the litellm response
  from the file; a missing key fails with "cassette miss: <node> <key>; re-record". Same-key
  repeats (retries) are stored as an ordered list.
- Cost in replay is the recorded cost, so budget and G6 checks see real numbers; G7 skipped.
- Judge calls (below) go through the same provider, so judged metrics replay for free too.

## Judged quality (DeepEval, `test_judged.py`)

Run in `record`, `replay` and `live`; skipped in offline modes (canned text has nothing to
judge). Each metric is its own test, so a low score never hides a behaviour result.

| Test | Metric | Input | Pass |
|---|---|---|---|
| `test_tools_match_expectation` | `ToolCorrectnessMetric` (deterministic, no LLM) | called tools from `tool_calls` vs scenario `expect.tools_used` | all expected tools called (also in offline modes) |
| `test_report_faithful_to_evidence` | `FaithfulnessMetric` | `actual_output` = LLM-written report sections; `retrieval_context` = validation ranking + candidate metrics (the D38 grounding sources) | score ≥ 0.7 |
| `test_champion_rationale_consistent` | `GEval` (criteria: rationale agrees with validation ranking, claims no test result) | rationale + ranking | score ≥ 0.6 |

- `judge.py`: `StatlabJudge(DeepEvalBaseLLM)`; model name from
  `ModelRouter.model_for("champion_selection")` (no provider name outside `backend/statlab/llm/`,
  D18); calls the cassette provider in record/replay, LiteLLM in live. Judge cost is counted in
  the budget.
- DeepEval runs under plain `pytest` with the `-p deepeval` plugin. The docs prefer
  `deepeval test run`; we keep plain pytest so our options and fixtures behave normally (89c
  verifies `assert_test` works this way).
- Telemetry and cloud off: `DEEPEVAL_TELEMETRY_OPT_OUT=YES`, no `CONFIDENT_API_KEY`. Set in
  `conftest.py` before import; 89a verifies the variable name against the installed version.

## Stages (sequential; detailed spec per stage after the previous handoff)

- **89a — dependency + pytest skeleton + behaviour parity (offline).** Dependency group
  `itest = ["deepeval==4.3.2"]` (uv lock; check its pins do not move any locked production
  dependency, especially `pydantic`, `openai`, `pytest`, `pytest-asyncio`; stop if they do).
  `integration/pytest.ini`, conftest fixtures, `test_behaviour.py`. Proof: `--llm offline` and
  `--llm offline-rich` give the same pass/fail per scenario and check as the old runner.
- **89b — cassettes.** `cassette.py`, `record`/`replay` modes, unit tests for keying, miss and
  secret exclusion in `tests/` (normal coverage rules apply if the module is placed under a
  covered package; otherwise integration-only).
- **89c — DeepEval metrics.** `judge.py`, `test_judged.py`; offline mode runs only
  `test_tools_match_expectation`.
- **89d — retire and document.** Remove `run_scenarios.py`; update `SCENARIOS.md` and Makefile
  (`itest-offline`, `itest-replay`, `itest-record`); add the course link in `ILLM_COURSE.md`
  (LLM-as-judge evaluation).

## Acceptance

1. `make check` green; no product code changes.
2. Offline and offline-rich suites under pytest match the last old-runner results per scenario.
3. **Paid step, needs a fresh explicit yes:** one `--llm record` run of the full suite (≤ $2
   scenarios + judge cost, estimated ≤ $0.30), then `--llm replay` passes with zero network calls
   and the same results.

## Risks

- DeepEval brings ~30 dependencies (OpenTelemetry, posthog, grpcio, pytest plugins such as
  xdist/rerunfailures) — isolated in the `itest` group, never in the product install.
- `filterwarnings = error` in the integration config may trip on DeepEval import warnings; 89a
  lists each one and main decides on targeted ignores.
- Prompt changes invalidate cassettes (by design); re-recording costs money.
- Judge scores vary between judge models; thresholds are starting values.

## Resolved before 89a (2026-10-27)

- Lock impact (scratch resolve with current pins as constraints): `deepeval==4.3.2` changes one
  locked version, `click` 8.5.0 → 8.3.3 (transitive via uvicorn/typer), and adds 26 packages.
  User accepted the click downgrade 2026-10-27. pydantic, openai, pytest, pytest-asyncio, litellm
  unchanged.
- `make install` runs `uv sync --all-groups`, so the `itest` group lands in the normal venv and its
  pytest plugins autoload. Entry-point names (wheel metadata): `deepeval`, `xdist`,
  `xdist.looponfail`, `rerunfailures`, `repeat`. The unit suite must block all five (the deepeval
  plugin also starts telemetry).
- Telemetry opt-out: `DEEPEVAL_TELEMETRY_OPT_OUT=1` (from the package source). deepeval ships
  `py.typed`.

## Stage 89a (detailed spec) — dependency, pytest suite, behaviour parity

Goal: the 20 scenarios run under pytest with the same per-scenario PASS/FAIL as the old runner;
the old runner keeps working (removed in 89d). No product code change. No DeepEval import yet.

Owned files: `pyproject.toml`, `uv.lock` (via `uv lock` only), `Makefile`,
`integration/session.py` (new), `integration/run_scenarios.py`, `integration/pytest.ini` (new),
`integration/tests/__init__.py` (new, empty), `integration/tests/conftest.py` (new),
`integration/tests/test_behaviour.py` (new). Not alone: preserve others' edits.

### 1. `pyproject.toml` and lock

a. `[dependency-groups]`: add `itest = ["deepeval==4.3.2"]` (after the existing groups).
b. `[tool.pytest.ini_options]` `addopts`: append
   ` -p no:deepeval -p no:xdist -p no:xdist.looponfail -p no:rerunfailures -p no:repeat`.
c. Run `uv lock`, then `uv sync --all-groups`. Check `git diff uv.lock`: the only changed version
   of a previously locked package must be `click` 8.5.0 → 8.3.3; stop and report anything else.

### 2. `integration/session.py` (new) — moved, not rewritten

Module docstring: `"""Suite services and one-scenario execution, shared by pytest and the old
CLI (chunk 89a)."""` Move from `run_scenarios.py` **verbatim** (bodies unchanged except the
renames): `ScenarioRecord`; `_sql_scalar` → `sql_scalar`; `_setup_logging` → `setup_logging`;
`_evaluate` → `evaluate`; `_run_one` → `run_one`; `_git_head` → `git_head`; `_write_summary` →
`write_summary` (internal calls updated to the new names). Add:

- `def make_workspace(mode: str, started: datetime) -> Workspace`: the workspace block from
  `_suite` (root `Path("integration/out") / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{mode}"`,
  `data_dir.mkdir(parents=True)`, `api_log.touch()`), returns the `Workspace`.
- `@dataclass(frozen=True) class SuiteServices`: fields `workspace: Workspace`, `mcp: McpProcess`,
  `driver: Driver`, `budget: Budget`, `provider: OfflineProvider | None`, `live: bool`.
- `@asynccontextmanager async def open_services(workspace: Workspace, *, mode: str, budget_usd:
  float) -> AsyncIterator[SuiteServices]`: the `_suite` body from `token = ...` through
  `budget = Budget(...)` (provider only when `mode != "live"`, `rich = mode == "offline-rich"`;
  driver `cheap = mode == "live"`), then `yield SuiteServices(..., live=mode == "live")`; the
  existing `finally` (driver close, api stop, mcp stop) unchanged.

### 3. `integration/run_scenarios.py`

Keep `_parse`, `main` unchanged. Replace the moved definitions with imports from
`integration.session`. Rewrite `_suite` to: `started = datetime.now(UTC)`;
`workspace = make_workspace(args.llm, started)`; `setup_logging(workspace.api_log)`; verbose as
today; `records: list[ScenarioRecord] = []`; `async with open_services(workspace,
mode=args.llm, budget_usd=args.budget_usd) as services:` the existing scenario loop using
`services.provider`, `services.driver`, `services.workspace`, `services.mcp`, `services.budget`,
`services.live`; after the block: `ended`, `write_summary(...)`, print, same return value.
Behaviour identical to today.

### 4. `integration/pytest.ini` (new)

```
[pytest]
minversion = 8.0
testpaths = tests
addopts = -q -rA --strict-markers --strict-config -p no:deepeval -p no:xdist -p no:xdist.looponfail -p no:rerunfailures -p no:repeat
asyncio_mode = strict
asyncio_default_fixture_loop_scope = session
asyncio_default_test_loop_scope = session
markers =
    soft: soft scenario checks; reported, never failing
```
No `filterwarnings = error`: parity with the old runner, which ran the live servers with default
warning filters (the unit suite keeps `error`). 89c removes `-p no:deepeval` here.
Run from the repository root: `.venv/bin/python -m pytest -c integration/pytest.ini --llm offline`.

### 5. `integration/tests/conftest.py` (new)

- `pytest_addoption(parser)`: `--llm` (choices `live`, `offline`, `offline-rich`, default
  `None`), `--budget-usd` (float, default 2.0), `--log-debug` (store_true; the old `--verbose`,
  renamed because pytest owns `-v/--verbose`), `--scenarios-file` (default
  `integration/scenarios.toml`).
- `pytest_configure(config)`: if `--llm` is missing raise `pytest.UsageError("--llm is required:
  live, offline or offline-rich")`; if `Path("uv.lock")` is not a non-empty file raise
  `pytest.UsageError("run from the repository root")`.
- `pytest_generate_tests(metafunc)`: when `"scenario"` is in `metafunc.fixturenames`,
  `metafunc.parametrize("scenario", load_scenarios(Path(<scenarios-file>)), ids=lambda s: s.id)`.
- `@dataclass class Suite`: `services: SuiteServices`, `started: datetime`,
  `records: dict[str, ScenarioRecord]` (default empty), `stopped: bool = False`.
- Session fixture `suite` (`@pytest_asyncio.fixture(scope="session", loop_scope="session")`,
  async generator): `started = datetime.now(UTC)`; `make_workspace(mode, started)`;
  `setup_logging`; `--log-debug` sets `logging.getLogger("statlab").setLevel(logging.DEBUG)`;
  `async with open_services(...) as services: yield Suite(services, started)`; after the block,
  `write_summary(workspace, list(suite.records.values()), mode=..., started=...,
  ended=datetime.now(UTC).isoformat())`.
- Fixture `scenario_record` (`@pytest_asyncio.fixture(loop_scope="session")`, function scope,
  params `suite: Suite, scenario: Scenario`, returns `ScenarioRecord`):
  1. cached in `suite.records` → return it;
  2. `suite.stopped` → store `ScenarioRecord(scenario)` and `pytest.skip("suite budget reached")`;
  3. set `suite.services.provider.scenario = scenario` when the provider is not `None`;
  4. `record = await run_one(scenario, driver=..., workspace=..., mcp=..., budget=...,
     live=...)`; store; `suite.stopped = record.budget_stopped`; return it.

### 6. `integration/tests/test_behaviour.py` (new)

Both tests `@pytest.mark.asyncio(loop_scope="session")`, async, taking `scenario_record`:
- `test_scenario_behaviour`: `failed` = must checks not passed; `assert not failed,
  "\n".join(f"{c.id}: {c.description} -- {c.detail}" for c in failed)` plus the scenario folder
  path as the first line of the message.
- `test_scenario_soft` (`@pytest.mark.soft`, also takes `record_property`): for each soft check
  `record_property(c.id, "pass" if c.passed else f"fail: {c.detail}")`; never asserts.

### 7. `Makefile`

Add targets (help text in the house `## ...` style):
`itest-offline` → `$(PY) -m pytest -c integration/pytest.ini --llm offline`,
`itest-offline-rich` → same with `offline-rich`. No live target in 89a (paid runs need a fresh
user yes; run the command by hand).

### 8. Checks (implementer runs exactly these)

1. `uv lock` + `uv sync --all-groups` and the §1c diff check.
2. `make test-fast TESTS="tests/test_grounding.py"` — passes, and its header/plugins line shows no
   deepeval/xdist/rerunfailures/repeat plugin.
3. `.venv/bin/python -m pytest -c integration/pytest.ini --llm offline -k S01` — both S01 tests
   pass; a `summary.md` is written in the new `integration/out/<ts>-offline/`.
4. `.venv/bin/python -m integration.run_scenarios --llm offline --only S01` — still PASS.
5. Ruff check, Ruff format --check and mypy on: `integration/session.py`,
   `integration/run_scenarios.py`, `integration/tests/conftest.py`,
   `integration/tests/test_behaviour.py`, `integration/tests/__init__.py`.

Stop and report on: any extra lock change; a plugin still loading in the unit suite;
pytest-asyncio refusing session-scoped loop options; an async fixture/test loop error; S01
failing under pytest while passing under the old runner. Never weaken a check.

Main acceptance (not the implementer): full `--llm offline` and `--llm offline-rich` under both
runners with matching per-scenario results, and `make check`.

Receipt: changed files, commands, exits, results, deviations.

### Addendum 89a-A (review corrections, 2026-10-27)

1. Lock: `huggingface-hub` 1.30.0 → 1.16.1 is accepted. Cause: the accepted `click<8.4` cap
   (newer huggingface-hub needs newer click); path litellm → tokenizers → huggingface-hub, used
   only for tokenizer files. The lock as regenerated stands; no re-lock. §1c now allows exactly
   these two version changes.
2. `scenario_record` §5 step 1: a cached record whose `result == "SKIPPED"` (stored by step 2)
   must `pytest.skip("suite budget reached")` again instead of returning it, so the second test of
   a budget-skipped scenario cannot pass on an empty record.

### Addendum 89a-B (review correction, 2026-10-27)

§8.3 exit 4 "unrecognized arguments: --llm": with `-c integration/pytest.ini` the rootdir is
`integration/`, and pytest uses `testpaths` only when invoked from the rootdir, so
`integration/tests/conftest.py` was not an initial conftest. Resolution: always pass the test
directory explicitly. Every integration command becomes
`.venv/bin/python -m pytest -c integration/pytest.ini integration/tests --llm <mode> [...]`:
the §4 run note, both §7 Makefile targets and §8.3
(`... integration/tests --llm offline -k S01`). `pytest.ini` keeps `testpaths = tests`.
Then run §8.3, §8.4, §8.5.

### Addendum 89a-C (review correction, 2026-10-27)

Ruff on `integration/tests/test_behaviour.py`: S101 (`assert`) and E501 (docstring, 102 chars).
Resolution: pytest asserts are the house test idiom, so `pyproject.toml`
`[tool.ruff.lint.per-file-ignores]` gains `"integration/tests/*" = ["S101"]` (beside
`"tests/*"`); shorten the module docstring to ≤ 100 chars without changing its meaning. Then
rerun §8.5 (Ruff check, format --check, mypy on the five files) plus `.venv/bin/python -m ruff
check pyproject.toml` is not needed; Ruff reads it. No other change.

### Addendum 89a-D (review correction, 2026-10-27)

First `make check` exit 2: tests 3347 passed, 1 skip, coverage 100 %, but mypy:
`integration/serve_ui.py:19: Module "integration.run_scenarios" has no attribute "_setup_logging"`.
Missed caller (the only one; grep of integration/, tests/, scripts/, Makefile, README). Fix:
`integration/serve_ui.py` imports `setup_logging` from `integration.session` (line 19) and calls
`setup_logging(root / "api.log")` (line 38). Checks: Ruff check/format and mypy on
`integration/serve_ui.py`; `.venv/bin/python -m mypy` (whole configured scope).

### 89a acceptance receipt (2026-10-27)

Corrections: Addenda 89a-A (huggingface-hub 1.16.1 accepted; skipped-record rule), 89a-B
(explicit `integration/tests` path), 89a-C (Ruff S101 ignore for `integration/tests/*`), 89a-D
(`serve_ui.py` caller). Lock vs 7da364a: click 8.5.0→8.3.3, huggingface-hub 1.30.0→1.16.1,
25 packages added. Parity (offline, same commit tree): plain offline old runner 20/20 PASS, pytest
19/20 (S16 G1: `BoundaryStopError` ERROR during API restart — a product race also reachable under
the old runner, 2/5 pytest vs 0/5 old; fixed by chunk 90). offline-rich: identical per-scenario
results under both runners (14 FAIL, 6 PASS: pre-existing offline-rich regression, see
WHATS_NEXT). After chunk 90: S16 under pytest 5/5 PASS. Final `make check` (with chunk 90)
exit 0: 3348 passed, 1 designed skip, coverage 100.00 % (14106 statements, 3998 branches), mypy
"no issues found in 219 source files", Ruff "All checks passed!", "229 files already formatted".
Paid modes not run.
