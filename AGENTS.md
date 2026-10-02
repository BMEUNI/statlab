# Project Agent Workflow

## Project context

`modelling-agent` is an auditable modelling app. It compares candidates on train/validation,
selects a champion without test data, then scores that champion on test exactly once. `plan/PLAN.md`
and its decision records are the architecture authority; use the current chunk for implementation
details rather than copying changing design here.

- `backend/statlab`: orchestration, database, API, and LLM; imports no compute libraries.
- `mcp_server/statlab_tools`: stateless compute tools; imports no backend package.
- `contracts/statlab_contracts`: shared wire types using stdlib and Pydantic only.
- Provider and model names belong only in `backend/statlab/llm/` (D18).
- Domain node names and spines belong in `backend/statlab/domains/` (D27).
- Test-derived artifacts remain lineage-locked under D32: no fitting on test, validation-based
  selection, and one test score for the selected champion, with prior exposure disclosed.

## Main agent role

The main agent orchestrates, architects, specifies, reviews, and updates plans. It MUST NOT write
application source code, tests, fixes, refactors, or implementation configuration. This includes
small edits and temporary source edits for probes or mutations. All such implementation and source
mutation/restoration work belongs to the `implementer` agent.

The main agent owns architecture and final acceptance. It may edit plan and workflow policy, author
specifications, inspect changes, run independent read-only review gates, and update project status.
It reviews mutation recipes and the failure/restoration receipts; the implementer performs the
source mutation and restoration. If an implementer finds a real architectural ambiguity, it stops
and reports it for the main agent to decide.

## Read order and authority

For continuation work, read only what the task needs, in this order:

1. `plan/HANDOFF.md` when present and relevant to the current task.
2. `plan/WHATS_NEXT.md` for live state, information hierarchy, and the working loop.
3. `plan/00_index.md` for hard rules, batch order, dependencies, and verified environment facts.
4. The active chunk spec and only the relevant `plan/PLAN.md` sections and addenda.

The normative `plan/PLAN.md` is the architecture authority. Explicit current user instructions
override conflicting procedure. Git state and current recorded state override stale historical
handoff claims, test counts, or HEAD claims. Do not reread all plans or re-probe environment facts
already verified in `00_index.md` without contrary evidence.

This file's validation policy is the single authority for test execution, ownership, and evidence
reuse. Chunk specs add behavior-specific acceptance criteria, not duplicate global gates. Completed
chunk reports and old handoff commands are historical evidence, not instructions to rerun them.

At startup inspect `git status --short` and `git log -1`. Trust recorded green evidence when HEAD
matches its verified commit and relevant files are unchanged; a docs-only descendant preserves that
evidence too. Do not run baseline tests merely to start a session. Establish a baseline only when
state is uncertain, HEAD differs unexpectedly, relevant unverified source changes exist, or a
specific failure needs one; start with focused checks and use a full baseline only if necessary.

## Implementation workflow

For each implementation batch:

1. Follow `00_index.md`'s execution-batch order and dependencies; do not invent a numeric sequence.
   Choose the smallest dependency-ready task and carry forward the previous stage's report.
2. The main agent writes a bounded, house-format spec that freezes decisions, with exact owned
   files, acceptance criteria, focused test commands, and stop conditions. Give the worker relevant
   spec sections and the prior stage's concise report, not the entire repository/history to read.
3. Use a fresh Luna `implementer` for each stage when practical. Normally there is one writer. Parallel
   work requires independent tasks, non-overlapping ownership, and explicit authorization; never
   run dependent stages concurrently. A later stage receives the prior stage's report and required
   outputs.
4. The implementer changes only its assigned implementation files, runs focused checks, and
   reports measured facts, hazards for the next stage, full-output paths, and genuine exit codes.
   It does not rewrite plans unless explicitly assigned a policy/document task.
5. The main agent reviews the diff, requirements, architecture/security/invariants, and recorded
   evidence; delegate corrections with focused checks. Do not repeat worker checks automatically.
   After review and all stages are stable, the main agent owns one final batch acceptance execution.
6. Keep live in-progress and blocker state accurate in chunk frontmatter, index/batch rows, and the
   single `Next` heading in `WHATS_NEXT.md`; record review corrections in the chunk addendum.
   Mark a chunk completed and fill `completion_date` only after acceptance. The main agent owns
   these updates. Do not put volatile counts or current batch status here.
7. Commit only when the user or the established workflow authorizes it. For authorized batches,
   commit specs only after their required gates; commit implementation only after all required
   gates pass and the main agent has inspected their summaries and relevant evidence. Never push.

Protect main-agent context: delegate narrow implementation exploration, builds, and debugging;
request concise summaries plus full-log paths and exit codes. The main agent reads relevant diffs
and required acceptance outputs. Report meaningful milestones and distinguish implemented,
tested, reviewed, accepted, and committed. Report actionable blockers instead of silent loops.

## Engineering rules

Keep changes minimal and follow existing helpers/patterns. The hard rules and current chunk spec
control; see `plan/00_index.md`, `plan/PLAN.md` decisions, and relevant addenda for full detail.

- Strict mypy with Pydantic `init_typed = true`; no explicit `Any` token in package Python source
  or comments, no new type ignores, no coverage suppression. Maintain 100% line and branch coverage;
  warnings are errors.
- Map foreign boundary failures to package domain errors without swallowing cancellation. Never
  evaluate formulas or expose/log secrets. Tests must not make paid live LLM requests.
- Reuse established helpers such as `json_scalar`, `observed_counts`, `as_str`, and the MCP tool
  pattern. Preserve labels; never reset indexes; never transform the target; keep fitted state
  frozen; import compute libraries lazily; use `n_jobs=1`; do not use `warnings.catch_warnings`.
- Keep plots pyplot-free and pass `parse_math=False` on every text call. See D12/D13/D18/D27/D32
  and the current spec; do not duplicate their changing design here.
- Enforce D12 tool allow-lists at dispatch, not only in prompts; keep the D13 MCP service
  loopback-bound by default and bearer-authenticated.

## Validation policy

### Tier 1 — implementation and corrections

Implement → focused tests → fix → focused tests. The implementer runs only affected node IDs,
modules, or the smallest relevant regression group, without coverage. Broaden the group when a
shared boundary or cross-cutting change warrants it. No full suite, global coverage, or startup
full gates in this loop. Use targeted ruff check/format frequently; scoped mypy on affected modules
and callers when useful. A scoped pass is development evidence, not full acceptance.

Every normal pytest process uses the explicit 900-second subprocess timeout wrapper. For example:

```sh
make test-fast TESTS=tests/test_mcp_client.py
make test-fast TESTS=tests/test_mcp_client.py::test_both_tool_allow_lists_and_exact_wildcard_are_enforced
.venv/bin/python -m ruff check backend/statlab/mcp_client.py tests/test_mcp_client.py
.venv/bin/python -m ruff format --check backend/statlab/mcp_client.py tests/test_mcp_client.py
.venv/bin/python -m mypy backend/statlab/mcp_client.py tests/test_mcp_client.py
```

Select tests for the assigned behavior. Report changed files, commands/selectors, genuine exit
codes, concise results, log paths, and unresolved risks.
The main agent inspects evidence rather than rerunning the same commands to establish ownership.

### Tier 2 — final acceptance of a stable implementation batch

After focused green state and diff review, run once for the complete stable batch:

```sh
.venv/bin/python -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' .venv/bin/python -m pytest -q --cov --cov-report=term
.venv/bin/python -m mypy
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

`make check` runs this sequence; `make test` is the full coverage gate, never the fast loop.
Coverage already executes every test: do not precede it with full plain pytest unless diagnosing
a specific coverage-related failure. Keep 100% line and branch coverage, strict mypy, warnings as
errors, and all boundary/security/lineage requirements. One actor owns expensive final execution:
normally the main agent; if explicitly delegated, review that receipt and do not run it again.
Intermediate stages hand off focused green evidence; global gates wait until batch review is done.

### Evidence reuse and proportionality

Record evidence in the stage report/current chunk: candidate HEAD plus relevant diff or file hashes,
command/scope, exit code, summary, log path, and which requirements it proves. Before a rerun, name
the change that invalidates that evidence. Reuse successful unchanged checks across actors/stages.

- Semantic product source, test, fixture, dependency, or runtime/test configuration changes
  invalidate full test/coverage evidence. Rerun affected focused checks while fixing, then one full
  coverage gate when stable again; keep unrelated static/probe evidence if its inputs are unchanged.
- Planning, docs, status, implementation summaries, AGENTS, agent instructions, and unrelated
  metadata changes do not invalidate source-code gates. Validate their syntax/links/diff only.
  Test-command/config changes require command/syntax inspection; run product tests only if execution
  semantics change (selection, dependencies, warning handling, coverage settings, etc.).
- Comments or demonstrably formatting-only Python changes need ruff check/format, not automatic
  ML/integration reruns. Use judgment for executable directives, doctests, or uncertain semantics.
- A one-line pure-function fix gets the relevant unit/module tests and one final gate. A cross-cutting
  architecture change gets broader focused regressions and the same final gate. Docs-only work gets
  no pytest. Do not run product gates to commit a spec before implementation.

Redirect large command output to unique temporary logs and capture the process exit status before
reading summaries. Inspect the final test summary, coverage TOTAL, static summary, and relevant
failures/tracebacks; do not paste progress streams or entire coverage tables into the main context.
Do not infer success from `grep`/`tail`; read full logs only when a specific unresolved issue needs it.

### Requirement-specific mutation and probe evidence

Only run mutations, empirical probes, real-fixture replays, and generated-artifact review explicitly
required by the current chunk/affected requirement, normally once during final review before the
global gate. Never inherit all historical probes. Repeat only evidence for a changed property;
unrelated fixes and docs do not invalidate it. Full-suite tests already exercising a required probe
count as its execution; standalone replay is additional only when the spec requires it.

For assigned DoD mutations, the implementer uses a 300-second subprocess timeout, asserts each
replacement count, confirms the intended guard fails, restores the original in `finally`, and
verifies restoration against the original diff. A timeout or import failure is not a mutation kill.
The main agent reviews the recipe and failure/restoration receipts. Exact restoration preserves
prior evidence; a persistent source/test correction invalidates affected evidence. When the spec
requires generated artifacts, read `report.md` and `pipeline.py` in full and inspect the required
standalone replay receipt. Never blindly kill unrelated processes.

Preserve dirty work. Stage only exact authorized files; never use `git add .` or destructive resets.
