---
status: completed
dependencies: [87]
completion_date: 2026-10-27
implementation_notes: "run_outcome fact records dataset_sha256 and per-candidate entries; recall_memory keeps notes plus 3 relevant outcomes; DesignDecision.memory_used filtered; report caveat names earlier runs"
---

# Chunk 88 — episodic memory across runs: record what worked, retrieve the relevant runs

iLLM course integration (see `ILLM_COURSE.md` §1c; PLAN D39; agentic-integrations lecture:
memory types, lifecycle record → consolidate → retrieve → augment, score = f(similarity,
recency)).

## Today (investigated 2026-10-27)

- Record: node 13 writes one `run_outcome` fact per finished run
  (`backend/statlab/domains/persistence.py:193-207`, `remember_run_outcome`
  `backend/statlab/persistence/repository.py:1145`, idempotent per run: a retry must produce the
  same JSON). The fact names candidates only by run-local IDs (`c_01`), so a later run cannot
  tell what worked. No test metric is stored (D32-safe; keep it so).
- Retrieve: none. `api/runs.py:574` snapshots **all** project memory rows into `RunConfig.memory`
  (the only creation site); `PromptContext` (`backend/statlab/llm/prompts.py:36`) puts every row
  into every node's system prefix.
- Augment: `DesignDecision` (`backend/statlab/domains/decisions.py:182`) has no way to name the
  earlier runs it used; the report does not say.

Non-goals: vector search/embeddings (no dependency; three structured facts need none); changing
the memory table, `remember_run_outcome` or `PromptContext`; per-node memory filtering (one
cache-friendly prefix stays, chunk 43); frontend changes (the gate form renders the new field
generically); summarising/merging old facts.

Prerequisite: chunk 87 accepted (its `_number_check` must stay the last caveats line).

Owned files: `backend/statlab/memory.py`, `backend/statlab/api/runs.py`,
`backend/statlab/domains/persistence.py`, `backend/statlab/domains/decisions.py`,
`backend/statlab/domains/design_inference.py`, `backend/statlab/domains/champion.py`,
`tests/test_project_memory.py`, `tests/test_nodes_persistence.py`,
`tests/test_nodes_design_inference.py`, `tests/test_nodes_champion.py`. Not alone: preserve
others' edits. No other file, dependency or migration.

## 1. Record — `backend/statlab/domains/persistence.py`

a. New module-level function before the executor class:
   ```
   def _remembered(branch: CandidateProgress, metric: str) -> dict[str, JsonValue]:
   ```
   Docstring: `"""What one candidate was and how it did on validation, for later runs (chunk 88).
   Never a test metric."""` Builds, in this key order:
   `{"candidate": branch.public_id, "label": branch.spec.label, "estimator":
   branch.spec.estimator.model_dump(mode="json"), "features":
   branch.spec.features.model_dump(mode="json"), "status": branch.status}`. Then:
   - `evaluated` = the newest `boundary.result` in `reversed(branch.results)` with
     `boundary.node_name == "model_evaluation"` and `boundary.result.status == "completed"`, else
     `None`. If not `None`: `metrics = evaluated.summary.get("metrics")`;
     `entry["validation"] = metrics.get(metric) if isinstance(metrics, dict) else None`.
   - `failure` = the newest `boundary.result.error` in `reversed(branch.results)` that is not
     `None`, else `None`. If not `None`: `entry["failure"] = failure.message`.
   Import `CandidateProgress` from `statlab.graph.state` if not imported.

b. In `_execute`, the `fact` dict: add after `"excluded"`:
   `"dataset_sha256": runtime.dataset,` (the runtime's `dataset` is the dataset SHA-256,
   `api/runs.py:368`) and
   `"candidates": [_remembered(branch, problem.primary_metric) for branch in invocation.state.branches],`.

## 2. Retrieve — `backend/statlab/memory.py` and `backend/statlab/api/runs.py`

a. `memory.py`: add `from collections.abc import Sequence`, `from typing import Final`.
   Constant `RECALLED_RUNS: Final = 3`. New public function after `read_memory`:
   ```
   def recall_memory(
       rows: Sequence[MemoryRow], *, dataset_sha256: str, target: str
   ) -> tuple[MemoryRow, ...]:
   ```
   Docstring: `"""Every project note plus the few most relevant earlier run outcomes (chunk 88):
   same dataset scores 2, same target 1, otherwise 0 and left out; newest first among equals."""`
   Algorithm:
   1. Inner `score(row) -> int`: `value = row.value()`; if not a `dict` → `0`; if
      `value.get("dataset_sha256") == dataset_sha256` → `2`; if `value.get("target") == target`
      → `1`; else `0`.
   2. `ranked = sorted((row for row in rows if row.key == "run_outcome" and score(row) > 0),
      key=lambda row: (score(row), row.ts, row.id), reverse=True)[:RECALLED_RUNS]`.
   3. `chosen = {row.id for row in ranked}`.
   4. Return `tuple(row for row in rows if row.key != "run_outcome" or row.id in chosen)`
      (input order kept, so the prompt prefix stays chronological).

b. `api/runs.py` import `recall_memory` beside `read_memory`; at line 574 replace
   `memory = read_memory(self.resources.conn, project.id)` with
   `memory = recall_memory(read_memory(self.resources.conn, project.id), dataset_sha256=dataset.sha256, target=contract.target.column)`.
   (`dataset` and `contract` are bound above in `create`.)

## 3. Augment — design decision and report

a. `decisions.py` `DesignDecision`: add after `family_justifications`:
   `memory_used: tuple[str, ...] = Field(default=(), description="source_run_id of each earlier run in memory whose outcome shaped this shortlist; empty if none.")`

b. `design_inference.py`:
   - New module-level function:
     ```
     def _known_runs(decision: DesignDecision, memory: Sequence[MemoryRow]) -> DesignDecision:
     ```
     Docstring: `"""Keep only memory_used IDs that are in this run's memory snapshot (chunk 88);
     unknown IDs are dropped, not fatal (chunk 84 pattern)."""`
     `known = {row.source_run_id for row in memory if row.source_run_id is not None}`;
     `used = tuple(dict.fromkeys(run for run in decision.memory_used if run in known))`;
     return `decision` if `used == decision.memory_used`, else
     `decision.model_copy(update={"memory_used": used})`.
     Imports: `MemoryRow` from `statlab.persistence.models`, `Sequence` if missing.
   - In `_design`: right after `self.validate_design(invocation, decision, registry.summary,
     problem)` (the line after the `decide` call) add
     `decision = _known_runs(decision, self.runtime.memory)`; right after
     `self.validate_design(invocation, approved, registry.summary, problem)` add
     `approved = _known_runs(approved, self.runtime.memory)`.
   - Question: append before `+ VARIATION_RULE` the sentence
     `"List in memory_used the source_run_id of each earlier run in memory that shaped this shortlist. "`
     (inside the existing string concatenation, ending with a space).

c. `champion.py`: new module-level helper before `_number_check`:
   ```
   def _earlier_runs(state: RunState) -> str:
   ```
   Docstring: `"""Caveat line naming the earlier runs the design used (chunk 88)."""`
   `design` = `summary.get("decision")` of the newest completed `experiment_design` boundary in
   `reversed(state.shared_results)`, else `None`; `used = design.get("memory_used") if
   isinstance(design, dict) else None`; `names = [run for run in used if isinstance(run, str)]
   if isinstance(used, list) else []`; return
   `f"\n\nEarlier runs used when designing candidates: {', '.join(names) if names else 'none'}."`
   In the caveats body insert `+ _earlier_runs(state)` immediately before
   `+ _number_check(...)`.

## 4. Edge cases

| Input | Result |
|---|---|
| 5 outcomes, same dataset, ts t1<…<t5 | recall keeps t3, t4, t5 (input order) |
| outcome same target, other dataset; outcome same dataset | both kept (≤3), dataset one ranks first |
| outcome other dataset and target | left out |
| fact written before chunk 88 (no `dataset_sha256`) | scores by target only |
| fact whose value is not a dict (e.g. `3`) under key `run_outcome` | left out |
| non-`run_outcome` rows | always kept |
| `memory_used=("r_old","r_missing","r_old")`, memory has `r_old` | `("r_old",)` |
| design decision saved before chunk 88 | report line says `none` |
| failed candidate | memory entry has `status: "failed"`, `failure` message, no `validation` unless an earlier evaluation completed |

## 5. Tests

### `tests/test_project_memory.py`

New `test_recall_keeps_notes_and_three_most_relevant_outcomes`: build `MemoryRow`s directly
(typed kwargs, `project_id="p"`, `value_json` via `json.dumps`): `note` key `"fact"` value `1`
ts `"t0"`; outcomes key `"run_outcome"`: `old` `{"target": "y"}` (no dataset) ts `"t1"`;
`d1`..`d4` `{"dataset_sha256": "s", "target": "y"}` ts `"t2"`..`"t5"`; `other`
`{"dataset_sha256": "x", "target": "z"}` ts `"t6"`; `scalar` value `3` ts `"t7"`; rows in that
order. Assert `recall_memory(rows, dataset_sha256="s", target="y")` `== (note, d2, d3, d4)`.
Second assert with `dataset_sha256="none"`: result `== (note, d2, d3, d4)` (all score 1 by target
→ newest three: d4, d3, d2 — `old` is oldest and drops). Third assert with `target="z",
dataset_sha256="none"`: `== (note, other)`. Import `json`, `recall_memory`.

### `tests/test_nodes_persistence.py`

In the existing test ending with `assert "selection_bias" in json.loads(memory[0].value_json)`
(~line 206) add: `fact = json.loads(memory[0].value_json)`; assert
`fact["dataset_sha256"] == h.runtime.dataset`; assert `[entry["candidate"] for entry in
fact["candidates"]] == [branch.public_id for branch in h.state.branches]`; assert every entry has
keys `label`, `estimator`, `features`, `status`; and the D32 guard: collect every dict key in
`fact` recursively (small local recursive function `keys(value) -> set[str]` in the test) and
assert `{key for key in keys(fact) if "test" in key} <= {"prior_test_scorings"}`.

### `tests/test_nodes_design_inference.py`

New `test_design_keeps_only_remembered_runs` (`@pytest.mark.asyncio`):
`h, _ = await prepared(migrated_db, project, tmp_path)`; set
`h.runtime = replace(h.runtime, memory=(MemoryRow(id="m1", project_id=project.id, key="run_outcome", value_json="{}", source_run_id="r_old", ts="t"),))`;
`base = shortlist()`; `h.llm.responses[DesignDecision] = DesignDecision(rationale=base.rationale, candidate_set=base.candidate_set, memory_used=("r_old", "r_missing", "r_old"))`;
`output = await design(h)`; assert `output.status == "completed"`; assert
`output.summary["decision"]["memory_used"] == ["r_old"]` (narrow with `isinstance` for mypy).

### `tests/test_nodes_champion.py`

In `test_champion_selects_before_exact_replay_and_one_task_score` add
`assert "Earlier runs used when designing candidates: none." in str(report["sections"])`.
New `test_report_names_earlier_runs_the_design_used`: `h, tools = await champion_harness(...)`;
replace the newest `experiment_design` boundary in `h.state.shared_results` by a copy whose
`result.summary["decision"]` has `"memory_used": ["r_old", 3]` (build a new `BoundaryResult` /
`NodeResult` with typed kwargs, other fields copied; if the harness has no `experiment_design`
boundary, append one with `status="completed"` and summary `{"decision": {"memory_used":
["r_old", 3]}}`); run the executor; assert the last section body contains
`"Earlier runs used when designing candidates: r_old."` and that this text comes before
`"Number check"` when present.

## 6. Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_project_memory.py tests/test_nodes_persistence.py tests/test_nodes_design_inference.py tests/test_nodes_champion.py tests/test_nodes_candidate_search.py tests/test_api_runs.py tests/test_prompt_context.py"`
- `.venv/bin/python -m ruff check`, `.venv/bin/python -m ruff format --check` and
  `.venv/bin/python -m mypy` on the ten owned files.

Stop and report on: a pre-existing test asserting the exact `run_outcome` JSON or the exact
caveats body; a `RunConfig.memory` consumer that needs all rows; any mypy error needing `Any` or
an ignore.

Receipt: changed files, commands, exits, results, deviations.

## Addendum A (review correction, 2026-10-27)

mypy: `problem.primary_metric` is `Metric | None` (`decisions.py:96`). Resolution:
`_remembered(branch: CandidateProgress, metric: str | None)`, and the validation line becomes
`entry["validation"] = metrics.get(metric) if isinstance(metrics, dict) and metric is not None else None`
(one expression, no new branch; a finished run always has a primary metric, so `None` only keeps
the type honest). No test change. Checks: mypy on the ten owned files, Ruff check/format on
`persistence.py`, `make test-fast TESTS="tests/test_nodes_persistence.py"`.

## Addendum B (review correction, 2026-10-27)

First `make check` exit 2: 3346 passed, 1 designed skip, no failures; coverage 99.98 % —
`persistence.py` 70->75 (no completed evaluation) and 84 (`failure` recorded) unreached: the
fixture has no failed candidate. Correction (test only), `tests/test_nodes_persistence.py`:
import `_remembered` beside `PersistenceExecutor`; import `CandidateProgress` from
`statlab.graph.state`, `CandidateSpec` and `EstimatorSpec` from `statlab_contracts.candidate`,
`Failure` from `statlab_contracts.envelope`. New sync test
`test_memory_entry_records_a_failed_candidate_without_validation`:
`branch = CandidateProgress(public_id="c_03", spec=CandidateSpec(label="Failed fit", rationale="Fixture candidate that fails to train.", estimator=EstimatorSpec(name="ridge")), status="failed", results=(BoundaryResult(node_name="model_training", execution_id="e1", result=NodeResult(status="failed", error=Failure(error_class="data", message="fit diverged"))),))`;
`entry = _remembered(branch, "rmse")`; assert `entry["status"] == "failed"`,
`entry["failure"] == "fit diverged"`, `"validation" not in entry`,
`entry["estimator"] == {"name": "ridge", "params": {}, "class_weight": "none", "calibrate": False}`.
If `NodeResult`/`Failure` construction raises (a validator needs more fields), stop and report.
Checks: `make test-fast TESTS="tests/test_nodes_persistence.py"`, Ruff check/format and mypy on
`tests/test_nodes_persistence.py`. Main reruns `make check`.

## Acceptance receipt (2026-10-27)

Candidate: HEAD `7da364a` plus working-tree changes (chunk 87 + 88). SHA-256 prefixes:
`memory.py` 87a15469, `api/runs.py` c6601515, `persistence.py` 981a0bee, `decisions.py` 18d31ee1,
`design_inference.py` 28510f58, `champion.py` f27629c2. Corrections: Addendum A (mypy:
`metric: str | None`), Addendum B (failed-candidate memory test). First gate exit 2 (coverage
99.98 %, persistence 70->75, 84; 3346 passed). Final `make check` exit 0: 3347 passed, 1 designed
skip, coverage TOTAL 100.00 % (14102 statements, 3998 branches), mypy "no issues found in 215
source files", Ruff "All checks passed!", "225 files already formatted". D32 key guard test
passes. No mutation required. Offline/live scenario not run.
