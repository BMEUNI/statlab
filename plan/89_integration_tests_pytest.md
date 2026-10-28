---
status: planned
dependencies: [88]
completion_date: null
implementation_notes: "89a accepted 2026-10-27 (pytest parity); 89b accepted 2026-10-28 (cassettes); 89c accepted 2026-10-28 (DeepEval); 89d pending"
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

## Stage 89b (detailed spec) — record/replay cassettes

Goal: `--llm record` runs the real provider and saves every LLM answer; `--llm replay` serves
those answers with no network and no spend. Prerequisite: 89a committed (`d8e6fd0`), chunk 90
(`7e7fac2`).

Key decision (investigated 2026-10-28): prompts contain random IDs (`<prefix>_<16 hex>`,
`persistence/ids.py:36`; artifacts `a_<16 hex>`) and run-to-run values (fit timings), so a
prompt-hash key never matches on replay. Answers are matched by **slot + occurrence** instead:
- `title` = the output schema title. Every request's system message ends with
  `"\nSchema: " + <schema JSON>` (`llm/client.py:594-597`); parse after the last occurrence
  (same as `OfflineProvider`, `integration/offline_llm.py:560-565`).
- `node`, `candidate`: `DomainRuntime.decide` user messages start with the canonical
  state-summary JSON followed by `"\n\n"` (`llm/prompts.py:54-67`, compact JSON has no raw
  newline). Parse `user.split("\n\n", 1)[0]` with `json.loads`; if it is a dict, `node =
  obj.get("node")` when a `str`, `candidate = obj["candidate"]["public_id"]` when `obj.get(
  "candidate")` is a dict with a `str` `public_id`. Any parse failure or other shape → `None`
  (setup assistant, chat control, gates, rewind calls).
- `slot = f"{title}__{node or '-'}__{candidate or '-'}"`; `n` = per-scenario count of
  successful answers for that slot, starting at 0. Calls of one slot are sequential
  (one node/candidate at a time), so `n` is deterministic; parallel candidates have distinct
  slots.
- Counter increments only after a successful answer (record) or a served answer (replay), so a
  provider error retried by `recover_call` re-uses the same `n`, and replay serves the eventual
  success directly.
- Drift (advisory): `prompt_sha256` = SHA-256 of the canonical JSON of `messages` after
  replacing every `\b[a-z]{1,3}_[0-9a-f]{16}\b` with `<id>`. Replay compares it with the
  recording; a mismatch is recorded as drift, never fails the answer.

Owned files: `integration/cassette.py` (new), `integration/session.py`,
`integration/tests/conftest.py`, `tests/test_integration_cassette.py` (new), `Makefile`.
Not alone. No product code change.

### 1. `integration/cassette.py` (new)

Docstring: `"""Record/replay of LLM answers at the LlmClient acompletion seam (chunk 89b)."""`
Imports: stdlib (`hashlib`, `json`, `re`, `collections.abc` `AsyncIterator`, `Awaitable`,
`Callable`, `dataclasses`, `pathlib.Path`, `typing.Literal`), `litellm`,
`from litellm import ModelResponse`, `from litellm.types.utils import ModelResponseStream`.

- `_ID: Final = re.compile(r"\b[a-z]{1,3}_[0-9a-f]{16}\b")`.
- `def slot_of(messages: list[dict[str, str]]) -> str` — as above; raises
  `RuntimeError("cassette: request has no schema")` when the marker is missing.
- `def prompt_sha256(messages: list[dict[str, str]]) -> str` — as above
  (`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
- `class _Replayed`: async-iterable stream over stored chunk dicts:
  `__init__(self, chunks: list[dict[str, object]])`, `__aiter__` yields
  `ModelResponseStream(**chunk)` in order, `async def aclose(self) -> None: return None`
  (the client requires a callable `aclose`, `client.py:481-483`).
- `@dataclass class CassetteProvider`: fields `mode: Literal["record", "replay"]`,
  `root: Path`, `scenario_id: str | None = None`, `counts: dict[str, int]` (default empty),
  `drift: list[str]` (default empty), `acompletion: Callable[..., Awaitable[object]] =
  litellm.acompletion` (test seam).
  - `def start(self, scenario_id: str) -> None`: set `scenario_id`, clear `counts` and `drift`.
  - `async def __call__(self, **kwargs: object) -> object`:
    1. `scenario_id is None` → `RuntimeError("cassette: no active scenario")`.
    2. `messages` validated exactly like `OfflineProvider.__call__` (`offline_llm.py:546-557`,
       same error text with `cassette:` prefix); `slot = slot_of(messages)`;
       `n = counts.get(slot, 0)`; `path = root / scenario_id / f"{slot}__{n}.json"`.
    3. replay: missing file → `RuntimeError(f"cassette miss: {scenario_id} {slot} #{n}; re-record")`;
       read JSON; if `stored["prompt_sha256"] != prompt_sha256(messages)` append
       `f"{slot} #{n}"` to `drift`; `counts[slot] = n + 1`; if `stored["stream"]` return
       `_Replayed(stored["chunks"])`, else `ModelResponse(**stored["response"])`.
    4. record: `raw = await self.acompletion(**kwargs)` (exceptions propagate, nothing
       stored); if `kwargs.get("stream") is True`: collect every chunk of `raw` with
       `async for` into a list, then `await raw.aclose()` if it has a callable `aclose`;
       payload `{"stream": True, "chunks": [c.model_dump() for c in chunks]}` and the return
       value is `_Replayed(<those dicts>)`; else payload `{"stream": False, "response":
       raw.model_dump()}` (raw must be a `ModelResponse`, else
       `RuntimeError("cassette: unexpected response type")`) and the return value is `raw`.
       Add to payload `"slot"`, `"n"`, `"model": kwargs["model"]`, `"prompt_sha256"`,
       `"messages"`. Never store any other kwarg (keys, base URLs, headers). Write with
       `path.parent.mkdir(parents=True, exist_ok=True)` and `path.write_text(json.dumps(payload,
       indent=1, default=str), encoding="utf-8")`; then `counts[slot] = n + 1`; return.

### 2. `integration/session.py`

- `open_services`: `mode` may now also be `record` or `replay`. Provider:
  `OfflineProvider(rich=...)` for `offline`/`offline-rich`; `CassetteProvider(mode=mode,
  root=cassettes)` for `record`/`replay` (new keyword `cassettes: Path` on `open_services`);
  `None` for `live`. `LlmClient(settings, acompletion=provider)` whenever provider is not
  `None`. Driver `cheap = mode in ("live", "record", "replay")` (replay must send the same
  cheap contract as the recording). `SuiteServices.live = mode in ("live", "record")` (G7
  real spend). `SuiteServices.provider` type becomes `OfflineProvider | CassetteProvider |
  None`.
- `run_scenarios.py` is unchanged (old runner keeps live/offline only); pass
  `cassettes=Path("integration/.cache/cassettes")` there too so its call compiles.

### 3. `integration/tests/conftest.py`

- `--llm` choices add `record`, `replay`; usage error text lists all five.
- `--cassettes` option, default `Path("integration/.cache/cassettes")` (gitignored), passed to
  `open_services`.
- `scenario_record` step 3: `OfflineProvider` → set `.scenario` (as today);
  `CassetteProvider` → `provider.start(scenario.id)`. After `run_one` and only for a
  `CassetteProvider` in replay mode: append
  `CheckResult("H-drift", "replayed prompts match the recording", "soft", not provider.drift,
  ", ".join(provider.drift))` to `record.checks` (import `CheckResult` from
  `integration.checks`).

### 4. `tests/test_integration_cassette.py` (new; main suite)

Helpers: `system(title)` returns `f"rules\nSchema: {json.dumps({'title': title})}"`;
`decide_user(node, candidate)` returns the canonical state summary
`json.dumps({"candidate": {"public_id": candidate} if candidate else None, "node": node},
sort_keys=True, separators=(",", ":"))` + `"\n\n[]\n\nquestion"`; a fake async `acompletion`
returning a fixed `ModelResponse(model="m", choices=[{"message": {"role": "assistant",
"content": "{\"a\": 1}"}, "finish_reason": "stop", "index": 0}], usage={"prompt_tokens": 3,
"completion_tokens": 2, "total_tokens": 5, "prompt_tokens_details": {"cached_tokens": 1}})`
and counting calls; a fake streaming one returning an async-iterable with `aclose` over two
`ModelResponseStream` chunks (content "ab", "c") and a usage-only chunk.
Tests (all `@pytest.mark.asyncio` where async):
1. `test_slot_of_decide_and_other_calls`: decide message → `"T__fit__c_02"`; shared node
   (candidate `None`) → `"T__intake__-"`; non-JSON user text → `"T__-__-"`; missing schema →
   `RuntimeError` match `"no schema"`.
2. `test_prompt_hash_ignores_random_ids`: two message lists differing only in
   `r_0123456789abcdef` vs `r_fedcba9876543210` → equal hashes; differing in other text →
   different.
3. `test_record_then_replay_round_trip` (`tmp_path`): record provider with the fake; `start("S01")`;
   call twice with the same decide messages, `stream=False`, `api_key="sk-secret"`; files
   `S01/T__fit__c_01__0.json`, `__1.json` exist; no file contains `"sk-secret"`; replay provider
   (fake `acompletion` that raises if called) after `start("S01")` returns two responses whose
   `choices[0].message.content == '{"a": 1}'` and `usage.prompt_tokens == 3`; `drift == []`.
4. `test_replay_stream_and_drift`: record a `stream=True` call; replay with messages whose user
   text changed → returned object iterates to the same 3 chunks (contents "ab", "c", then
   usage), has `aclose`, and `drift == ["T__fit__c_01 #0"]`.
5. `test_replay_miss_and_no_scenario`: replay on empty dir → `RuntimeError` match
   `"cassette miss: S01 T__fit__c_01 #0"`; provider without `start` → match `"no active scenario"`.
6. `test_record_error_does_not_advance`: fake raising `ConnectionError` once then answering:
   first call raises, second succeeds and is stored as `#0`; `counts == {"T__fit__c_01": 1}`.

### 5. `Makefile`

Add `itest-replay` → `$(PY) -m pytest -c integration/pytest.ini integration/tests --llm replay`.
No record/live target (paid; run by hand after a fresh user yes).

### 6. Checks (implementer)

- `make test-fast TESTS="tests/test_integration_cassette.py"`
- `.venv/bin/python -m pytest -c integration/pytest.ini integration/tests --llm offline -k S01`
- `.venv/bin/python -m pytest -c integration/pytest.ini integration/tests --llm replay -k S01`
  → expected to FAIL S01 behaviour with a `cassette miss` (no recording yet); report the
  failure text; this proves the miss path end to end. Do not run `record` or `live`.
- Ruff check, Ruff format --check, mypy on the five owned files (literal paths).

Stop and report on: `ModelResponse(**dump)` or `ModelResponseStream(**dump)` not round-tripping
(test 3/4); mypy needing `Any`/ignores for litellm types; anything requiring a product change.

Main acceptance: `make check`; offline suite unchanged; the paid record + free replay pair waits
for the user's explicit yes (planned after 89c so one recording covers judge calls too).

### Addendum 89b-A (review corrections, 2026-10-28)

1. **Stream chunks are stored in a reduced, typed form** (litellm's `ModelResponseStream.__init__`
   rejects `**dict[str, object]` under mypy). Record: for each chunk store
   `{"content": <str | None>, "usage": <None | {"prompt_tokens": int, "completion_tokens": int,
   "total_tokens": int, "cached_tokens": int}>}` where `content = chunk.choices[0].delta.content`
   when `chunk.choices` else `None`, and `usage` from `getattr(chunk, "usage", None)` when it is
   a litellm `Usage` (`cached_tokens = usage.prompt_tokens_details.cached_tokens` when details
   are present and the value is an `int`, else 0). The payload key stays `"chunks"`; add
   `"model": kwargs["model"]` (already stored). `_Replayed(chunks, model)` builds, per stored
   chunk, exactly like `OfflineProvider`'s `_Stream` (`integration/offline_llm.py:89-117`):
   content not `None` → `ModelResponseStream(choices=[StreamingChoices(index=0,
   delta=Delta(content=content))], model=model)`; usage not `None` → `ModelResponseStream(
   choices=[], model=model, usage=Usage(prompt_tokens=..., completion_tokens=...,
   total_tokens=..., prompt_tokens_details=PromptTokensDetails(cached_tokens=...)))`. Reasoning
   text is not kept (the client reads only `delta.content` and usage). Validate stored types
   with `isinstance` and raise `RuntimeError("cassette: malformed recording")` on mismatch.
   Record mode returns `_Replayed(<reduced chunks>, model)` too.
2. `integration/run_scenarios.py:53-54`: set `.scenario` only when
   `isinstance(services.provider, OfflineProvider)` (the old runner never builds a cassette).
3. A cassette miss must be visible in `api.log`: before raising, log it with
   `logging.getLogger(__name__).error(<same message>)`. (`LlmClient` logs only the exception
   class.)
4. `tests/test_integration_cassette.py`: no `typing.Any`. Annotate replay results as `object`
   and narrow with `assert isinstance(first, ModelResponse)` / `hasattr(result, "aclose")` +
   `isinstance(result, AsyncIterable)` before use. Test 4 additionally asserts the replayed
   chunks are `ModelResponseStream` with contents `["ab", "c"]` and the last chunk's
   `usage.prompt_tokens`. Add to test 5: `caplog` contains the miss message at ERROR.
Checks: §6 again (cassette tests, offline S01, replay S01 expected miss — now confirm
`grep "cassette miss: S01" integration/out/<newest>-replay/api.log` finds it), Ruff/format/mypy on
the five owned files.

### 89b acceptance receipt (2026-10-28)

Correction: Addendum 89b-A (typed reduced stream chunks, old-runner provider narrowing, miss
logged at ERROR, no `Any` in tests). Focused: 6 cassette tests pass; offline S01 under pytest
2 passed; replay S01 fails as designed with `ERROR integration.cassette cassette miss: S01
IntakeDecision__intake__- #0; re-record` in `api.log`. `make check` exit 0: 3354 passed,
1 designed skip, coverage 100.00 %, mypy "no issues found in 221 source files", Ruff clean,
"231 files already formatted". Paid record run not done (after 89c, needs user yes).

## Stage 89c (detailed spec) — DeepEval metrics

Prerequisite: 89b committed (`65736b0`). Read from deepeval 4.3.2 source (2026-10-28):
`DeepEvalBaseLLM` (`deepeval/models/base_model.py`): abstract `load_model`, `generate`,
`a_generate`, `get_model_name`, all `(*args, **kwargs)`; `__init__(model)` sets `self.name`
and calls `load_model()`. Custom (non-native) judges are called as
`a_generate(prompt, schema=<pydantic class>)` and may return a JSON string, which DeepEval parses
(`deepeval/metrics/utils/generation.py`). `ToolCorrectnessMetric` without `available_tools` is
deterministic (no LLM call) but still initialises its `model`, so always pass ours (the default
would be an OpenAI model). Metrics expose `await metric.a_measure(test_case)`, `.score`,
`.reason`, `.is_successful()`.

Design decisions:
- Judged tests call `await metric.a_measure(...)` and assert `metric.is_successful()` with
  `metric.reason` as the message. **No `assert_test` / `deepeval test run`**: those need the
  DeepEval pytest plugin and its own event loop (our tests run on the session loop); the plugin
  stays blocked (`-p no:deepeval`) and telemetry off.
- Judge calls go through the same seam as the product: the `CassetteProvider` in record/replay
  (so judged scores replay for free), `litellm.acompletion` in live, and a function raising
  `RuntimeError("judge unavailable offline")` in offline modes (LLM-judged tests skip there).
- Judge requests are keyed by content, not occurrence: they run concurrently inside a metric and
  their prompts carry no random IDs. Each scenario's judge calls use their own cassette folder
  `<scenario>-judge`.
- Judge model: `ModelRouter({}, load_routing_environment()).model_for("judge")` (default role;
  provider names stay in `backend/statlab/llm/`, D18). Judge spend is not in `llm_calls`; the
  judge counts prompt/completion tokens and the fixture prints the totals at session end. The
  budget guard ignores judge spend (estimated well under $0.30 per suite).

Owned files: `integration/judge.py` (new), `integration/cassette.py`,
`integration/tests/conftest.py`, `integration/tests/test_judged.py` (new),
`tests/test_integration_cassette.py`, `tests/test_integration_judge.py` (new). Not alone.

### 1. `integration/cassette.py`

- New constant `JUDGE_SYSTEM: Final = "Evaluation judge. Answer with one JSON object only."`
- `slot_of`: after computing `title`, if `system.startswith(JUDGE_SYSTEM)` return
  `f"{title}__judge__{prompt_sha256(messages)[:16]}"`; otherwise unchanged.

### 2. `integration/judge.py` (new)

Docstring: `"""DeepEval judge over the integration LLM seam (chunk 89c)."""` Before importing
deepeval: `os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")` (top of module, after
stdlib imports; add `# noqa: E402` only if Ruff requires it for the following imports).
```
class StatlabJudge(DeepEvalBaseLLM):
    def __init__(self, model: str, acompletion: Callable[..., Awaitable[object]]) -> None
```
- `__init__`: store `self._acompletion = acompletion`, `self.prompt_tokens = 0`,
  `self.completion_tokens = 0`, `self.calls = 0`, then `super().__init__(model)`.
- `load_model(self, *args: object, **kwargs: object) -> StatlabJudge`: `return self`.
- `get_model_name(self, *args: object, **kwargs: object) -> str`: `return str(self.name)`.
- `generate(self, *args: object, **kwargs: object) -> str`: raise
  `RuntimeError("StatlabJudge is async only")`.
- `async a_generate(self, *args: object, **kwargs: object) -> str`:
  1. `prompt = args[0]` must be `str`, else `TypeError("judge prompt must be text")`.
  2. `schema = kwargs.get("schema")`; `schema_json = json.dumps(schema.model_json_schema() if
     isinstance(schema, type) and issubclass(schema, BaseModel) else {"title": "Text"},
     sort_keys=True)`.
  3. `messages = [{"role": "system", "content": f"{JUDGE_SYSTEM}\nSchema: {schema_json}"},
     {"role": "user", "content": prompt}]`.
  4. `raw = await self._acompletion(model=self.name, messages=messages,
     response_format={"type": "json_object"}, timeout=120, num_retries=2)`.
  5. `raw` must be a litellm `ModelResponse` whose `choices[0]` is a `Choices`, else
     `RuntimeError("judge: unexpected response")`; add `usage.prompt_tokens` /
     `usage.completion_tokens` when `usage` is a `Usage`; `calls += 1`; return
     `choices[0].message.content or ""`.
- `def offline_acompletion(**kwargs: object) -> Awaitable[object]`: an `async def` raising
  `RuntimeError("judge unavailable offline")`.

### 3. `integration/tests/conftest.py`

- Session fixture `judge(suite, pytestconfig) -> StatlabJudge` (async, session loop): model as in
  the design decision; `acompletion` = `suite.services.provider` when it is a
  `CassetteProvider`, `litellm.acompletion` for `live`, `offline_acompletion` otherwise.
  Teardown prints `f"judge: {judge.calls} calls, {judge.prompt_tokens} prompt +
  {judge.completion_tokens} completion tokens"`.
- Helper `def judged_mode(config) -> bool`: `--llm` in `record`, `replay`, `live`.
- Helper `def champion_summary(record: ScenarioRecord) -> dict[str, JsonValue] | None`: read
  `record.folder / "node_executions.json"` (list of rows; `summary` is a JSON string or object),
  newest row with `node_name == "champion_selection"` and `status == "completed"`; return its
  parsed summary dict or `None` (no folder / file / row).
- Helper `def tools_called(record) -> list[str]`: `tool_name` of every row in
  `record.folder / "tool_calls.json"`, first-occurrence order, unique.

### 4. `integration/tests/test_judged.py` (new)

All `@pytest.mark.asyncio(loop_scope="session")`, take `scenario`, `scenario_record`, `judge`,
`pytestconfig`. Imports from `deepeval.metrics` (`ToolCorrectnessMetric`, `FaithfulnessMetric`,
`GEval`) and `deepeval.test_case` (`LLMTestCase`, `LLMTestCaseParams`, `ToolCall`).
1. `test_tools_match_expectation` (all modes): skip (`pytest.skip("no expected tools")`) when
   `scenario.expect.tools_used` is empty. `metric = ToolCorrectnessMetric(threshold=1.0,
   model=judge, include_reason=True, async_mode=True)`; case `LLMTestCase(input=scenario.title,
   actual_output=str(<final status from result.json or "unknown">), tools_called=[ToolCall(name=n)
   for n in tools_called(record)], expected_tools=[ToolCall(name=n) for n in
   scenario.expect.tools_used])`; `await metric.a_measure(case)`; assert `is_successful()`.
2. `test_report_faithful_to_evidence`: skip unless `judged_mode`; `summary =
   champion_summary(record)`; skip when `None` or `summary["decision"]["sections"]` is empty;
   `actual_output = "\n\n".join(f"{s['heading']}\n{s['body']}" for s in sections)`;
   `retrieval_context = [json.dumps(summary["ranking"], sort_keys=True),
   json.dumps(summary["comparison"], sort_keys=True)]`; `FaithfulnessMetric(threshold=0.7,
   model=judge, include_reason=True, async_mode=True)`; `input="Explain the champion choice"`.
   Before measuring: `provider.start(f"{scenario.id}-judge")` when the provider is a
   `CassetteProvider`.
3. `test_champion_rationale_consistent`: skip unless `judged_mode` or no champion summary;
   `GEval(name="Rationale consistency", criteria="The rationale must agree with the validation
   ranking in the input: it may only claim a candidate is better where the ranking supports it,
   and it must not mention any test-set result.", evaluation_params=[LLMTestCaseParams.INPUT,
   LLMTestCaseParams.ACTUAL_OUTPUT], threshold=0.6, model=judge, async_mode=True)`;
   `input = json.dumps(summary["ranking"], sort_keys=True)`, `actual_output =
   summary["decision"]["rationale"]`. Same cassette `start` as test 2.
Narrow every `JsonValue` with `isinstance` before use (strict mypy, no `Any`).

### 5. Tests in the main suite

- `tests/test_integration_cassette.py`: add `test_judge_slot_is_content_keyed`: system starting
  with `JUDGE_SYSTEM` + `"\nSchema: {\"title\": \"Verdicts\"}"` → slot starts with
  `"Verdicts__judge__"`, equal for equal prompts, different for different user text.
- `tests/test_integration_judge.py` (new): fake `acompletion` returning a `ModelResponse` with
  content `'{"verdict": "yes"}'` and usage (3, 2): `StatlabJudge("m", fake)`;
  `await judge.a_generate("p", schema=<small pydantic model titled Verdict>)` returns the content;
  the fake saw `model="m"`, the system message contains `JUDGE_SYSTEM` and `"Verdict"`,
  `response_format == {"type": "json_object"}`; counters `calls == 1`, tokens 3/2.
  `generate("p")` raises `RuntimeError`; non-text prompt raises `TypeError`;
  `await offline_acompletion()` raises `RuntimeError("judge unavailable offline")`;
  a non-`ModelResponse` answer raises `RuntimeError("judge: unexpected response")`.

### 6. Checks (implementer)

- `make test-fast TESTS="tests/test_integration_cassette.py tests/test_integration_judge.py"`
- `.venv/bin/python -m pytest -c integration/pytest.ini integration/tests --llm offline -k "S01 or S04"`
  → behaviour/soft pass; `test_tools_match_expectation` passes where `tools_used` is set (else
  skipped); the two LLM-judged tests skip. Report the per-test outcomes.
- `git status --short` must show no new untracked files besides the owned ones (DeepEval may
  create a local folder such as `.deepeval`; report it, do not delete or ignore it yourself).
- Ruff check, Ruff format --check, mypy on the six owned files (literal paths).
Never run `record` or `live`.

Stop and report on: mypy errors from deepeval's types needing `Any`/ignores; DeepEval import
emitting network calls or writing files; any metric constructor rejecting `model=judge`.

Main acceptance: `make check`; offline suite; then (user yes) one paid `record` run and a free
`replay` run.

### Addendum 89c-A (review corrections, 2026-10-28)

1. **DeepEval import warning (unit suite).** Python 3.14 `DeprecationWarning:
   'asyncio.iscoroutinefunction' is deprecated` raised inside `deepeval/tracing/tracing.py`
   (via `DeepEvalBaseLLM.__init_subclass__`). Third-party, not ours. `pyproject.toml`
   `[tool.pytest.ini_options]` `filterwarnings` becomes
   `["error", "ignore:'asyncio.iscoroutinefunction' is deprecated:DeprecationWarning:deepeval"]`
   (module regex scoped to deepeval; our code keeps `error`). If a *different* DeepEval warning
   appears next, stop and report it; do not widen the filter.
2. **mypy no-untyped-call** on `class StatlabJudge(DeepEvalBaseLLM)` (deepeval's untyped
   `__init_subclass__`). `pyproject.toml` `[tool.mypy]` gains
   `untyped_calls_exclude = ["deepeval"]` (mypy's scoped option; no ignore comments).
3. **No imports from conftest.** Move `Suite`, `judged_mode`, `champion_summary`,
   `tools_called` (and `final_status` from `test_judged.py`) into `integration/session.py`
   (public, unchanged bodies); `conftest.py` and `test_judged.py` import them from there.
   Tests 2 and 3 taking `suite: Suite` (to call `provider.start`) is accepted.
4. Accepted as implemented: the `args` guard in `a_generate`, `offline_acompletion -> object`,
   `-p no:deepeval` staying in `integration/pytest.ini`, RUF100 noqa removal, Ruff format.
Owned files now also include `pyproject.toml` and `integration/session.py`.
Then run all §6 checks (unit judge/cassette tests, offline `-k "S01 or S04"` with per-test
outcomes, `git status --short`, Ruff/format/mypy on the eight owned files).

### Addendum 89c-B (review correction, 2026-10-28)

New collection error: `SyntaxError: 'return' in a 'finally' block` in
`deepeval/evaluate/execute/loop.py:816`. Python 3.14 (PEP 765) emits a `SyntaxWarning` there, and
the unit suite's `filterwarnings = ["error"]` turns it into an error during import. Resolution:
**DeepEval is never imported by the unit suite.**
1. Move `tests/test_integration_judge.py` to `integration/tests/test_judge_unit.py` (content
   unchanged; `git mv` not required, the file is untracked). It runs in the integration suite,
   which keeps default warning filters and needs no services (it does not request `suite`).
2. Revert Addendum 89c-A item 1: unit-suite `filterwarnings` back to `["error"]`.
   (`untyped_calls_exclude = ["deepeval"]` stays.)
3. `tests/test_integration_cassette.py` must not import deepeval (it imports only
   `integration.cassette`, which does not); confirm with `grep`.
Checks: `make test-fast TESTS="tests/test_integration_cassette.py"`;
`.venv/bin/python -m pytest -c integration/pytest.ini integration/tests --llm offline -k "judge_unit or S01 or S04"`
with per-test outcomes; `git status --short`; Ruff/format/mypy on the owned `.py` files.

### Addendum 89c-C (review correction, 2026-10-28)

`LLMTestCaseParams` is deprecated in deepeval 4.3.2 (DeprecationWarning from our import).
`test_judged.py`: import and use `SingleTurnParams` instead (`SingleTurnParams.INPUT`,
`SingleTurnParams.ACTUAL_OUTPUT`; verified: `GEval.evaluation_params: List[SingleTurnParams]`).
The remaining DeepEval-internal warnings (tracing DeprecationWarning, loop.py SyntaxWarning) are
third-party and accepted in the integration suite. Checks: Ruff/format/mypy on
`integration/tests/test_judged.py`; offline `-k "judge_unit or S01"` passes; the log no longer
contains `LLMTestCaseParams`.

### 89c acceptance receipt (2026-10-28)

Corrections: 89c-A (mypy `untyped_calls_exclude = ["deepeval"]`; helpers moved to
`integration/session.py`), 89c-B (DeepEval never imported by the unit suite: judge unit tests in
`integration/tests/test_judge_unit.py`; unit `filterwarnings` stays `["error"]`), 89c-C
(`SingleTurnParams`). Full offline suite under pytest: exit 0, 49 passed, 56 skipped (judged
metrics skip offline; tool check skips without `tools_used`): behaviour 20/20 PASS,
`test_tools_match_expectation` PASS for S02, S03, S19, S20, judge unit tests 5/5. Remaining
warnings are DeepEval-internal (tracing DeprecationWarning, loop.py SyntaxWarning).
`make check` exit 0: 3355 passed, 1 designed skip, coverage 100.00 %, mypy "no issues found in
224 source files", Ruff clean, "234 files already formatted". LLM-judged metrics unverified
until the paid record run.
