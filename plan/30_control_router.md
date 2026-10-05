---
status: implemented
dependencies: [22, 26]
completion_date: 2026-10-05
implementation_notes: "B11 implemented/reviewed 2026-10-05; bounded status review correction; scoped and global static exit0. Runtime/coverage stopped by user. Receipt: receipts/B11.md."
---

# Chunk 30 — Interrupt classification, Q&A and status

## Goal

Implement PLAN §5's typed interrupt classifier and read-only Q&A/status handlers, with explicit
callbacks for the three mutating categories built in B12. Controls are outside the spine.
PLAN D18/D27/D36 and §§5, 7, 7.3 are authority. No API, lifecycle, retry, gate or fork implementation.

## Prerequisites and reuse

- Existing `RunState`/candidate boundaries, `NodeExecutionRow`, shared `LlmClient`, `ModelRouter`,
  `PromptContext`, immutable `SkillPolicy`, contract and project-memory snapshot.
- Existing role map has `interrupt_router` and `status_explainer` CLASSIFY; Q&A uses the current
  node's role and skill literal/role precedence. No provider literals or new model configuration.
- LLM client already validates schema, repairs once, bounds timeout/concurrency and audits usage
  against an existing same-run node execution. Do not duplicate retry/semaphore/SDK validation.
- Chunk29 handoff: `TabularExecutor(runtime)` now dispatches all13; runtime adds optional caller
  `lockfile_path` required for persistence. Stage29 scoped Ruff/format/mypy/diff exited0,18 offline
  selectors unexecuted. Test claim ambiguity fails closed; fork producer-log composition belongs31.

## Exact owned files

New `backend/statlab/graph/control.py`, `backend/statlab/graph/router.py`,
`tests/test_control_router.py`, `tests/test_control_qa_status.py` only.
No existing graph/engine/reconciliation, domains, LLM, repository, contracts, skill/config changes.
Report a concrete blocker before widening ownership. Main alone edits plans or commits.

## Frozen behavior and APIs

### Caller-owned context and outputs

Use a frozen `ControlContext` dataclass with caller-owned connection/shared LLM/router, captured
RunState, contract/skill/memory snapshot, existing audit execution ID, and explicit current node
(nullable between/after nodes). Optional bounded typed recent conversation and small artifact
summaries may be passed; they are summaries, never artifact bytes/frames/environment. Current
node must belong to captured spine when supplied. Verify audit execution belongs to captured run
before LLM use; an absent/foreign anchor fails clearly. Do not create phantom spine executions or
write checkpoint/run/candidate/gate/model status. B12/API owns obtaining an audit anchor for inbound
messages before the first execution. Capture one snapshot per request, do not reread live memory.

Use frozen extra-forbid Pydantic outputs and tuple collections. Return a typed response containing
category, text and canonical JSON facts/references as appropriate; caller owns delivery/events.
No global state, tasks, event bus construction, tools/compute/artifact-file reads or loop locking.

### Classifier and router

1. `InterruptClass` closed categories: `qa`, `status`, `decision`, `change`, `lifecycle`, `noise`.
   Lifecycle action is exactly pause/resume/cancel and is required only for lifecycle; inconsistent
   combinations fail Pydantic validation. Classification is intent only: never choose/apply gate
   params, rewind target or state changes from unverified classifier prose.
2. `ControlRouter` classifies inbound nonblank bounded text via shared typed client using
   `model_for("interrupt_router")`, CLASSIFY reasoning flag false and explicit role instructions.
   Classifier never answers. Preserve raw original message for dispatch and future handlers.
3. Dispatch qa/status to implemented handlers. Noise returns deterministic acknowledgement with
   no second LLM call. `decision`/`change`/`lifecycle` go to explicitly injected async callbacks
   receiving original message, validated class and same captured context. Missing callback raises
   package engine error instead of claiming an action happened. Callback output is typed; a handler
   may report a new run ID later, but this batch never changes state or starts/stops execution.
4. Propagate existing package LLM/domain errors and preserve cancellation. No new retry layer or
   string-matching error classifier. No frontend/transcript persistence or HTTP endpoints.

### Out-of-band Q&A

Use captured boundaries, candidate summaries, small supplied artifact summaries, contract,
current skill and initialized project memory; optional recent conversation remains bounded.
Use `PromptContext` plus explicit answering instructions, with current node model/skill precedence
and role reasoning flag. With no current node use router DEFAULT; never hardcode domain node names.
Typed answer contains nonblank bounded text and optional ArtifactRef citations. Validate citations
against refs visible in captured node results/context; syntactically valid invented refs are a
reachable LLM failure not caught by ArtifactRef's syntax validator. Reject them honestly. Do not
fetch cited artifacts or dispatch MCP. No recommendations to inspect/select/refit on test and no
claim that missing/unexecuted evidence is verified. Pipeline progress is not awaited or blocked.

### Status explainer

Build canonical status facts deterministically from captured state: status/mode, current node,
completed/skipped shared boundaries, candidate statuses and completed/current positions, wave,
remaining declared boundaries, pending gate/error facts and budget if present. Do not double-count
retune histories as distinct completed spine positions. Report actual candidate failure/budget
states. Remaining wall time is unknown unless caller supplies a finite nonnegative estimate,
which is explicitly approximate; no estimate inferred from contended fit durations.

Render these facts as a plain template first. Optional typed LLM polish through CLASSIFY may add
context but cannot supply/replace canonical status/progress/ETA fields; always return canonical
facts/template so consumers do not depend on generated factual text. Use same shared client and
audit anchor. No status DB updates, event writer or lifecycle side effects.

## Focused evidence

Author unexecuted offline selectors in the two owned test modules: all category dispatch routes,
noise/no-extra-call, missing handlers, enum/action Pydantic validation, same-run audit anchor,
current-node model/skill role precedence, grounded artifact citations, deterministic candidate
status/retune counts and unknown ETA, snapshot/memory nonmutation, concurrent read-only calls and
cancellation/domain error propagation. Use fake completion/callbacks and valid state fixtures.
Existing B6 evidence covers provider tiers, one repair, timeout/accounting and shared semaphore.

Run only scoped `ruff check`, `ruff format --check`, strict `mypy` for the four owned files and
`git diff --check`. Logs `/private/tmp/statlab-b11-stage30-*`. No pytest/collection/coverage,
probes/mutations/pipeline execution/live LLM/global gates under user stop.

## DoD and stop conditions

Stop at terminal scoped static green handoff. Give exact files/hashes/base, commands and actual
exit codes/logs, authored unexecuted selectors, public callback/context signatures and B12 hazards.
Preserve others' edits; no worker plan rewrites, commits or speculative integration. Main owns
one consolidated B11 review/correction pass and final global static-only acceptance plus receipt.
Runtime behavior and coverage remain unproved until user permits execution.

## Consolidated review correction

A valid contract may allow64 candidates with120-character labels. The original deterministic
status template could exceed `ControlResponse.text`'s8000-character Pydantic limit, causing status
to fail without an LLM call. Bound presentation honestly while preserving complete `StatusFacts`;
author a valid large-run regression but leave it unexecuted under the user's test stop. Existing
LLM audit-anchor validation is reused rather than duplicated.
