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

## Roles and implementation loop

Main owns architecture, bounded specs, review, acceptance and plan/status updates. Main MUST NOT edit
application source, tests, implementation configuration, or temporary mutation/probe source. Delegate
all implementation and source mutation/restoration to an implementer. Main may edit workflow policy
and plans and run read-only gates. Do not delegate a policy-only edit merely for ceremony.

1. Follow index execution-batch order, not chunk numbers. Freeze exact owned files, behavior/API,
   relevant decisions, focused selectors and stop conditions in the chunk's house-format spec.
   Write the next dependent stage's detailed spec after its prerequisite handoff. Use existing
   contracts; no speculative architecture. Test counts are estimates, never quotas.
2. Spawn one writer with `fork_turns="none"` and a self-contained bounded assignment: task, owned
   files, relevant spec/decision locations, prior stage's concise report, checks and stop conditions.
   Do not copy full parent history. Tell worker it is not alone and must preserve others' edits.
3. Use Luna `implementer` for narrow routine work. Use Sol `implementer_strong` for complex boundary,
   async/concurrency or cross-module work, or after two unsuccessful focused correction rounds.
   Both are implementation roles; main does not take over. Routine reasoning: medium; raise effort
   for a named unresolved decision. Fresh worker per stage; reuse it for related corrections.
   No dependent stages in parallel. Parallel agents require independent work, non-overlapping
   ownership and explicit authorization.
4. Worker implements -> focused tests -> fixes -> focused tests. Reports concise results, changed
   files, candidate identity, commands/selectors, genuine exit codes, log paths and next-stage hazards.
   Stop at focused green handoff. No worker commits, plan rewrites or architecture changes.
5. Check terminal stage handoff for dependency/API blockers, then advance dependency-ready work.
   Consolidate full batch review into one pass; send one concrete correction list where practical.
   Before demanding a new defensive guard/test, identify a reachable failure and check existing
   SDK/Pydantic guarantees. Do not bypass validators to manufacture unreachable coverage paths.
   Re-review changed behavior/invariants; do not restart whole review after each fix.
6. After stable review, main owns one final acceptance execution. Update chunk frontmatter,
   index rows and the single Next heading only after acceptance. Commit only with user/workflow
   authorization, stage exact files, never `git add .`, destructive reset, or push.

Specs retain frontmatter (`status`, `dependencies`, `completion_date`, `implementation_notes`), goal,
prerequisites/reuse, owned files, frozen behavior, tests, DoD/stop conditions. Record actual review
corrections in a short addendum; do not copy global gates into every spec.

## Waiting and runtime recovery

Do not perform delegated implementation while worker runs or treat unfinished output as final.
Wait for every required worker's terminal result. A wait timeout is not failure or completion.
Use event-driven completion; blocking waits use the longest interval the active runtime permits
while meeting its communication requirements. Avoid reflex polling, repeated log counts, or
"still working" messages to workers. Inspect status after timeout before another wait.

If blocked without progress, inspect worker's pending command/approval/runtime state. Do not
blindly kill processes or replace healthy workers. For FAILED/ERRORED/CANCELLED, report failure,
preserve outputs, and retry only unfinished scope through an implementer. COMPLETED with unavailable
result is an orchestration error: recover receipt, do not silently redo work. Runtime/configuration
failure is distinct from a product defect; more reasoning will not fix it.

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

Only affected node IDs/modules or smallest relevant regression group, without coverage. Broaden
when changed shared boundaries justify it. Targeted Ruff check/format; scoped mypy when useful.
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

One actor owns expensive final execution. If explicitly delegated, inspect receipt rather than
repeat it. Read actual exits, final pytest summary, coverage TOTAL, static summaries and relevant
failures. Redirect large outputs to unique logs; do not infer success from grep/tail.

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
not a kill. Main reviews recipe and failure/restoration receipts. Repeat only changed properties.
For required artifacts, main reads `report.md` and `pipeline.py` fully and inspects standalone replay
receipt. Persist decisive facts so another session can reuse acceptance.
