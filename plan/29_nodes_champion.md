---
status: implemented
dependencies: [19, 20, 28]
completion_date: 2026-10-05
implementation_notes: "B11 implemented/reviewed 2026-10-05; scoped and global static exit0. Runtime/coverage stopped by user. Receipt: receipts/B11.md. Fork producer-log composition belongs31."
---

# Chunk 29 — Champion, report and reproducible persistence

## Goal

Complete tabular nodes 12–13 and expose one caller-owned executor for the declared 13-node spine.
Select from validation, replay the frozen winner on test, score once, then preserve the real run.
PLAN D18/D19/D26/D27/D29/D31–D36 and §§4, 7, 10 are authority. No API/bootstrap/lifecycle work.

## Prerequisites and reuse

- B10 provides latest completed candidate training/evaluation summaries, validation-selected
  threshold, ordered `PipelineState.replay`, and immutable candidate methodology.
- Reuse `DomainRuntime`, existing executors, `CandidateSearchPlanner`, retune adapter,
  `TABULAR_SPINE`/`TABULAR_TRAVERSAL`, `shared_result`, `candidate_result`, `_Evidence` and readers.
- Reuse MCP compare/rank/select, prediction/scoring, metrics/report and export/snapshot tools.
  `compare_models` requires two score tables and validates task/truth alignment. Ranking enforces
  registered estimators and hard constraints. Scoring already provides seeded bootstrap CIs.
- B6 dispatch owns allow-list/lineage/schema checks, durable pre-dispatch test claim and sent-argument
  audit. Never clear the claim or reproduce its guarantees. `logged_calls` is the export input.
- Existing SQLite `models` table and `runs.champion_model_id` FK need repository methods, not migration.
  Skill policy/snapshot and model router already exist. No new dependencies or provider names.

## Exact owned files

New: `backend/statlab/domains/champion.py`, `backend/statlab/domains/persistence.py`,
`backend/statlab/domains/executor.py`, `tests/test_nodes_champion.py`,
`tests/test_nodes_persistence.py`, `tests/test_domain_executor.py`.
Modify: `backend/statlab/domains/decisions.py`, `backend/statlab/domains/tabular.py`,
`backend/statlab/domains/runtime.py`, `backend/statlab/persistence/models.py`,
`backend/statlab/persistence/repository.py`, `tests/test_repository.py`,
`tests/test_nodes_foundation.py` (only deferred-declaration assertions if needed).
Do not change MCP server/contracts, generic graph, LLM internals, skill policy or configuration.
Stop and report an actual ownership/API blocker before widening scope.

## Frozen behavior and APIs

### Node 12

1. Typed frozen `ChampionChoice` (candidate ID, rationale, bounded report sections) replaces
   `UnavailableDecision`. Candidate IDs map to checkpointed public IDs, never position labels.
   Require joined candidates. Eligible comparison inputs have aligned latest completed training
   and evaluation models plus a complete/skipped replay pipeline. Failed/incomplete/budget-stopped
   branches are excluded and disclosed. Require at least two eligible score tables; one survivor
   fails honestly before selection/test because the existing comparison contract requires two.
2. Use the resolved primary metric, compare latest validation tables, then rank with candidate
   estimator registry names. Persist comparison evidence in the node result. Request reasoning
   from returned validation/ranking evidence and candidate methodology/retune/wave history only.
   Guided approval precedes selection/test; no approval returns awaiting-user without test access.
   Unknown/ineligible choices fail before `select_champion`. Call select once with approved rationale.
3. Add typed `ModelRow` and small repository methods to atomically save the current-run candidate's
   trained model artifact plus parameters and set `champion_model_id`. D11 forks retain ancestor
   artifact refs: accept a registered model artifact owned by this run or its verified parent chain,
   never an unrelated run. Reject replacement, unrelated
   artifacts/candidates and a new selection after a test claim. Idempotent identical selection is
   allowed; no schema change. Identity comes from resolved registered artifacts, not arbitrary paths.
4. Derive `CallContext.champion_selected` only for champion-selection calls from a durable valid
   champion pointer, not an unconstrained caller boolean. Selection precedes all test transforms.
   Replay each saved preprocessing/feature step in order, substituting only frame; preserve all
   fitted refs/non-frame arguments, no fitting/refitting/threshold selection/test diagnostics.
   Predict with latest selected model and saved threshold; classification probabilities only when
   required by the score API. Invoke exactly one task score tool on the transformed test frame.
5. Restart boundary safety: a durable successful select audit can recover the identical choice
   before DB pointer persistence; an existing pointer must agree with the frozen latest model.
   Never dispatch another select after a durable successful select or test claim. If test is claimed
   and a successful matching score audit exists, reuse it for report continuation; otherwise fail
   closed with an explicit interruption reason. Never clear/retry an ambiguous test claim.
6. Render validation metrics with existing tool; include canonical node-6 coefficient table once
   when present, existing valid plot refs, D33 candidate/wave/retune selection caveats, CI overlap
   statement, and non-comparable concurrent fit timing. `prior_test_scorings` comes from repository
   project/fingerprint count excluding this run; `n_waves` from state. Assemble report using returned
   artifacts, never LLM-computed values. Save champion/model/selection/test-score/report path and
   exposure/count/replay facts in the result. Do not allow report prose to change the winner.

### Node 13

1. Typed frozen persistence decision replaces deferred declaration. Deterministic preservation is
   sufficient; do not call an LLM merely for ceremony. Read completed node-12 target test score and
   real `logged_calls` (including exact routing/seed arguments and produced references).
   Honor declared `gate_policy="always"`: guided approval of the deterministic decision precedes
   lockfile/export/snapshot/manifest/memory writes; absent approval returns awaiting-user. Gate
   rationale edits cannot change the artifact target derived from completed node 12.
   Call export with those calls and that target, then snapshot with real calls including export.
2. Extend `DomainRuntime` with optional caller-supplied `lockfile_path: Path | None = None`.
   Persistence requires a supplied readable lockfile; refuse clearly when absent. Copy exact bytes
   into run root `uv.lock`; identical bytes on retry are idempotent, differing existing bytes refuse
   rather than overwrite the run's environment. No current-directory assumption, subprocess or
   dependency inspection.
   Verify existing immutable `skill.snapshot.md` equals policy bytes/hash. Existing clients remain
   constructible without the optional field; complete run composition must supply it.
3. Enrich snapshot manifest atomically with skill SHA256 and resolved per-node model metadata,
   plus actual audited LLM models used, and lockfile digest. Recompute relevant file hashes after
   preservation. Keep snapshot run hash semantics unchanged; distinguish metadata from hash input.
   Read only caller-owned routing/skill and saved LLM audit, never environment/secrets. Preserve
   export/report/snapshot hashes in the completed result. Wrap filesystem/JSON/SQLite failures;
   preserve cancellation. No generated pipeline execution under user test stop.
4. Append one deterministic project-memory outcome fact with source run provenance, target/task,
   selected estimator and validation selection/exposure caveats from durable evidence. Exact
   identical fact is idempotent; conflicting same-run fact fails. No unbounded LLM extraction,
   secret/environment/transcript ingestion, or project-memory reread into the current prompt.

### Composition

`TabularExecutor(runtime)` dispatches exactly the thirteen declared names to existing foundation,
design/inference, candidate pipeline/model and new champion/persistence executors. Unknown nodes
raise package engine error. Caller wires existing search/retune/traversal/loop; no second engine,
global singleton, API startup or hidden event writer. Domain names remain inside domains.

## Focused evidence

Author offline tests in owned files using real checkpoint/repository fixtures and fake tool/LLM
boundaries. Cover classification/regression, latest retune alignment, ordered unchanged replay,
selection eligibility/approval, single score and interrupted claim recovery, truthful report
inputs, export LoggedCalls target slicing inputs, lockfile/skill/model manifest facts, memory
idempotence, and full dispatcher. Use existing tool/Pydantic guarantees rather than manufactured
invalid objects. Tests remain unexecuted by explicit user instruction.

Allowed focused commands: scoped `ruff check`, `ruff format --check` and strict `mypy` for owned
Python files; `git diff --check`. Redirect logs to unique `/private/tmp/statlab-b11-stage29-*` files.
No pytest/collection/coverage, live LLM, probes/mutations or pipeline execution. No worker global gate.

## DoD and stop conditions

Return terminal focused handoff once implementation and scoped static checks are green: exact
changed paths, base/candidate hashes, commands/scopes, genuine exit codes/logs, authored test
selectors (unexecuted), and chunk-30 integration hazards. No worker commits/plan edits. Preserve
others' work. Report structural blockers instead of adding architecture or weakening contracts.
Main reviews dependencies, writes chunk 30 only after this handoff, then consolidates batch review
and owns one global static acceptance. Runtime/coverage remains explicitly unproved.

## Architecture clarifications and next-stage hazards

- D11 ancestor model artifacts are valid; repository selection verifies ancestry instead of
  requiring a copied current-run artifact row.
- Node 13 honors its declared guided gate with a deterministic proposal before preservation writes.
- Chunk 31 owns fork audit composition: a current-run tool log alone lacks inherited producer
  calls required by export/snapshot. Until that integration exists, existing tools refuse an
  incomplete fork log. Do not fabricate history or relax exporter validation in this chunk.
