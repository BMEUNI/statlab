@/Users/nandordukat/.codex/RTK.md

# Project Agent Workflow

## Communication

Caveman mode always active: terse, technically exact; no filler. Code, commands, errors,
commits and PR text remain normal. Clarity wins for ambiguity/security. User can disable style.

## Authority and startup

`modelling-agent` selects candidates on train/validation, then scores the champion on test once.
`plan/PLAN.md` is architecture authority; current user instructions override conflicting procedure.
This file alone defines execution/validation policy; old chunk receipts are historical evidence.

At startup inspect `git status --short` and `git log -1`. Preserve dirty work. Read only:
1. `plan/HANDOFF.md` if present and relevant, then short `plan/WHATS_NEXT.md`.
2. Relevant batch/dependency rows in `plan/00_index.md`.
3. Active chunk sections and referenced PLAN decisions. Search `plan/ENVIRONMENT.md` for touched
   dependencies; do not read every plan, completed spec, archive, or session export.

Trust accepted evidence when candidate inputs match, including docs-only descendants. No startup
baseline tests. If state is uncertain, check affected files and focused evidence first. Missing
temporary logs alone do not invalidate an accepted durable receipt. Missing/unverified source does.

## Roles: planner/reviewer and mechanical implementer

Main is the sole planner and reviewer. Main owns investigation, architecture, all design decisions,
detailed implementation plans, review, acceptance and plan/status updates. Main MUST NOT write or edit
a single line of application code, tests, implementation configuration, generated code, or temporary
mutation/probe code, including through scripts, formatters, autofix, generators or restoration commands.
Do not put executable implementation code or ready-to-apply source patches in plans or messages.
Describe exact implementation steps in prose, signatures, data definitions and non-executable
pseudocode. All code writing and source mutation/restoration belongs to the implementer, including
one-line fixes and review corrections. No complexity, urgency or failed-worker exception.

Main may edit project workflow instructions, Codex agent settings, plans and acceptance receipts.
Main may read source/diffs and run read-only inspection and validation commands. Do not delegate
a policy-only edit merely for ceremony. Skills and old specs do not grant either role extra authority.

The implementer only translates the planner's resolved plan into code and runs the specified checks.
It has no independent design authority: no choosing algorithms, APIs, defaults, error policies,
abstractions, dependencies, test cases or scope. Mechanical syntax/import/formatting work is allowed
only to express the specified design. Missing decisions, contradictory instructions, unavailable
helpers or unexpected failures return to main before any workaround or behavioral change.

### Required plan before delegation

Follow index execution-batch order, not chunk numbers. Main reads the affected source, every relevant
caller and existing helpers before freezing the plan. Resolve design and SDK/Pydantic guarantees
on main; do not ask the implementer to investigate, decide, infer intent or design a solution.
Write the next dependent stage's detailed spec only after its prerequisite terminal handoff.

Each stage spec retains frontmatter (`status`, `dependencies`, `completion_date`,
`implementation_notes`) and the house format. Before spawning, it must contain:

1. Goal, explicit non-goals, prerequisites, relevant PLAN decisions and accepted evidence to reuse.
2. Exact owned files and symbols to add/change/remove; existing callers and integration seams.
   Name the existing helpers/contracts to reuse with their locations. No unspecified file discovery.
3. Exact public/private signatures, field types, defaults, return values and compatibility behavior
   for affected interfaces. Spell out validation, error mapping and cancellation behavior.
4. An ordered, per-file edit recipe. For each non-trivial function, give the algorithm/control flow,
   branch conditions and outcomes, data transformations, operation ordering and side effects.
   Specify state transitions, persistence/transaction boundaries and concurrency ownership when
   applicable. The worker must not fill gaps with its own design.
5. Boundary and edge-case decisions with concrete inputs and expected outputs/errors. State which
   reachable invariants must remain preserved and how existing guarantees satisfy them.
6. Exact tests to add/change, fixture/setup data, actions and assertions; exact focused selectors
   and static-check commands. Include required mutation/replay recipes and restoration checks.
   Honor active user restrictions on test execution. Test counts are estimates, never quotas.
7. Acceptance/stop conditions, receipt fields and known dependency hazards. No unresolved design
   questions, TBDs, alternatives for the worker to choose, or instructions such as "handle edge
   cases", "choose a suitable design" or "fix as needed" without a resolved recipe.

If a plan cannot provide these details, main continues read-only investigation and planning before
delegating. A behavior summary or file list alone is not an implementation-ready plan.

### Implementation and review loop

1. Spawn one writer with `agent_type="implementer"` and `fork_turns="none"`. Only `gpt-6-luna`
   with low reasoning is authorized for subagents. Never spawn Sol, `implementer_strong`, generic
   workers, explorers, reviewers or other roles; never escalate model/effort after failures.
   Check the available role configuration before source work: if it pins another model/effort,
   report the runtime mismatch and require a fresh session rather than silently substituting.
   No dependent stages in parallel. Additional independent implementers require explicit user
   authorization and non-overlapping ownership. Implementers must never spawn subagents.
2. Give a self-contained bounded assignment containing the detailed spec path, exact owned files,
   relevant decision locations, prerequisite receipt, ordered recipe, checks and stop conditions.
   Tell the implementer it is not alone and must preserve others' edits. Do not copy full history.
   Use a fresh implementer per stage; reuse it for that stage's explicit correction plans.
3. Implementer transcribes the plan and runs only specified, currently permitted focused checks.
   On an unresolved decision or unexpected failure, it stops affected work and reports the exact
   gap/failure, commands, exits and partial diff. It must not diagnose/design a fix independently,
   weaken tests/validators, expand ownership or claim a failed check passed.
4. Main diagnoses reported gaps read-only, updates the spec with a resolved correction recipe and
   exact affected checks, then sends it to the same implementer. Main never applies the correction.
   Repeated failures require a clearer/smaller plan, not Sol escalation or main implementation.
5. Wait for the terminal stage receipt; inspect the complete diff against the plan and invariants,
   focused evidence and dependency/API blockers. Advance only dependency-ready work. Consolidate
   batch review into one pass and send one concrete correction plan where practical. Identify a
   reachable failure and existing SDK/Pydantic guarantees before requiring a guard/test. Never
   bypass validators to manufacture unreachable coverage paths. Re-review changed behavior only.
6. After stable review, main owns one final acceptance execution under the validation policy and
   active user test restrictions. Update chunk frontmatter, index rows and the single Next heading
   only after acceptance. Commit only with user/workflow authorization; stage exact files, never
   `git add .`, destructive reset, or push. No worker commits, plan rewrites or architecture changes.

Record resolved review corrections in a short spec addendum before delegating them. Do not copy
global gates into every spec. Worker receipts include changed files, candidate identity,
commands/selectors, genuine exits, results, log paths, plan deviations and next-stage hazards.

## Waiting and runtime recovery

Do not perform delegated implementation while worker runs or treat unfinished output as final.
Wait for every required worker's terminal result. A wait timeout is not failure or completion.
Use event-driven completion; blocking waits use the longest interval the active runtime permits
while meeting its communication requirements. Avoid reflex polling, repeated log counts, or
"still working" messages to workers. Inspect status after timeout before another wait.

If blocked without progress, inspect worker's pending command/approval/runtime state. Do not
blindly kill processes or replace healthy workers. For FAILED/ERRORED/CANCELLED, report failure,
preserve outputs, and retry only unfinished scope through an implementer with a resolved recovery plan.
COMPLETED with unavailable result is an orchestration error: recover receipt, do not silently redo
work. Runtime/configuration failure is distinct from a product defect; more reasoning will not fix it.

## Engineering invariants

- `backend/statlab` imports no compute libraries; `mcp_server/statlab_tools` is stateless and imports
  no backend; `contracts/statlab_contracts` uses only stdlib/Pydantic. Provider/model names only in
  `backend/statlab/llm/` (D18); domain nodes/spines only in `backend/statlab/domains/` (D27).
- D32 lineage: no fitting on test, validation-only selection, one champion test score, prior exposure
  disclosed; test-derived artifacts remain locked. Enforce D12 allow-lists at dispatch. D13 MCP
  defaults to loopback with bearer authentication.
- Strict mypy, Pydantic `init_typed=true`, 100% line/branch coverage, warnings as errors. No explicit
  `Any` token in package Python source/comments, new type ignores, or coverage suppression.
- Foreign boundary failures become package domain errors; preserve cancellation. Never evaluate
  formulas or expose/log secrets. No paid live LLM tests. Preserve labels/indexes/target and frozen
  fitted state. Lazy compute imports, `n_jobs=1`, no `warnings.catch_warnings`.
- Reuse existing helpers (`json_scalar`, `observed_counts`, `as_str`) and MCP patterns. Plots stay
  pyplot-free; every text call uses `parse_math=False`. See relevant D12/D13/D18/D27/D32 addenda.
- Derive redundant fields/assertions; use tuples in frozen models. Negative Pydantic tests use
  `model_validate`; production/positive tests use typed kwargs. No untyped override-dict helpers.

## Validation policy

### Tier 1 — implementation and corrections

Main specifies affected node IDs/modules or the smallest relevant regression group, without coverage.
Main broadens selectors only when changed shared boundaries justify it. Specify targeted Ruff
check/format and scoped mypy when useful; implementer runs the exact planned commands.
No full suite/global coverage/startup gates in the correction loop. Every normal pytest process
uses the explicit 900-second subprocess timeout, including standalone replays through pytest:

```sh
make test-fast TESTS=tests/test_mcp_client.py
.venv/bin/python -m ruff check backend/statlab/mcp_client.py tests/test_mcp_client.py
.venv/bin/python -m ruff format --check backend/statlab/mcp_client.py tests/test_mcp_client.py
.venv/bin/python -m mypy backend/statlab/mcp_client.py tests/test_mcp_client.py
```

Worker owns focused evidence; main inspects it, not automatically reruns it.

### Tier 2 — one final stable batch acceptance

Main runs `make check` once: equivalent to these four gates (no preceding full plain pytest):

```sh
.venv/bin/python -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' .venv/bin/python -m pytest -q --cov --cov-report=term
.venv/bin/python -m mypy
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Main alone owns expensive final execution; do not delegate global gates to the implementer or
repeat them without changed inputs. Read actual exits, final pytest summary, coverage TOTAL, static
summaries and relevant failures. Redirect large outputs to unique logs; do not infer success from grep/tail.

### Evidence reuse and retention

Before rerunning, name changed input/property invalidating evidence. Source/test/fixture/dependency/
runtime-test-config semantic edits require affected focused checks, then one full gate when stable.
Reuse unrelated static/probe evidence. Exact mutation restoration preserves evidence. Planning/docs/
status/AGENTS/agent instructions/Codex settings do not invalidate product gates: check syntax, links
and diff only. Test-command/config edits need command inspection; run product tests only if execution
semantics change. Comments/formatting-only Python: Ruff and judgment, not automatic integration runs.

Keep concise durable acceptance receipt in current chunk or `plan/receipts/<batch>.md`: candidate
commit plus relevant diff/file hashes, commands/scopes, genuine exits, results, requirements proved,
required mutation restoration/replay results and full-log locations. Large logs may be temporary;
accepted facts must survive expiry. Do not reconstruct accepted evidence merely because `/tmp` or
pytest retained directories were cleaned. Unaccepted/contradictory evidence still needs verification.
Stage handoffs use the same concise fields.

### Requirement-specific mutations and probes

Run only checks explicitly required by current chunk/affected requirement, normally once at final
review before global acceptance. Do not inherit completed chunks' mutation/probe lists. For new
specs, choose mutations for named high-risk guards not already adequately proved; no blanket quota.
Preserve explicitly required checks. Normal suite execution counts for probes it exercises;
standalone replay is extra only when current spec requires it.

Implementer owns mutations: 300-second subprocess timeout, asserted replacement counts, intended
guard failure, restoration in `finally`, original-diff/hash comparison. Timeout/import failure is
not a kill. Main specifies the exact recipe and reviews failure/restoration receipts. Repeat only
changed properties.
For required artifacts, main reads `report.md` and `pipeline.py` fully and inspects standalone replay
receipt. Persist decisive facts so another session can reuse acceptance.
