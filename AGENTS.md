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

The main agent owns architecture and final acceptance. It may edit plan and policy prose, author
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

## Implementation workflow

For each implementation batch:

1. Follow `00_index.md`'s execution-batch order and dependencies; do not invent a numeric sequence.
   Choose the smallest dependency-ready task and carry forward the previous stage's report.
2. The main agent writes a bounded, house-format spec that freezes decisions, with exact owned
   files, acceptance criteria, checks, and stop conditions.
3. Use a fresh `implementer` for each stage when practical. Normally there is one writer. Parallel
   work requires independent tasks, non-overlapping ownership, and explicit authorization; never
   run dependent stages concurrently. A later stage receives the prior stage's report and required
   outputs.
4. The implementer changes only its assigned implementation files, runs focused checks, and
   reports measured facts, hazards for the next stage, full-output paths, and genuine exit codes.
   It does not rewrite plans unless explicitly assigned a policy/document task.
5. The main agent independently reviews the diff and acceptance evidence, runs the review gates,
   and delegates any focused correction. Do not accept a report in place of evidence.
6. Keep live in-progress and blocker state accurate in chunk frontmatter, index/batch rows, and the
   single `Next` heading in `WHATS_NEXT.md`; record review corrections in the chunk addendum.
   Mark a chunk completed and fill `completion_date` only after acceptance. The main agent owns
   these updates. Do not put volatile counts or current batch status here.
7. Commit only when the user or the established workflow authorizes it. For authorized batches,
   commit specs only after their required gates; commit implementation only after all required
   gates pass and the main agent has read their outputs. Never push.

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

## Acceptance gates and mutation evidence

For implementation batches, run each pytest process through an explicit `.venv/bin/python`
hard-timeout wrapper with a 900-second timeout, followed by the required commands:

```sh
.venv/bin/python -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' .venv/bin/python -m pytest -o addopts='' -q -p no:cacheprovider
.venv/bin/python -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=900).returncode)' .venv/bin/python -m pytest -q --cov --cov-report=term
.venv/bin/python -m mypy
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Capture full outputs and genuine process exit statuses; do not infer success from `grep` or `tail`.
Run focused tests during implementation. Run full acceptance gates once per stable candidate and
again when changes invalidate evidence; do not rerun unchanged gates or issue speculative commands.

For required DoD mutations, the implementer uses a 300-second subprocess timeout, asserts each
replacement count, confirms the intended guard fails, restores the original in `finally`, and
verifies restoration against the original diff. A timeout or import failure is not a mutation kill.
The main agent reviews the recipe and failure/restoration receipts. Read generated `report.md` and
`pipeline.py` in full, and run required real-fixture standalone replays. Never blindly kill unrelated
processes.

Preserve dirty work. Stage only exact authorized files; never use `git add .` or destructive resets.
