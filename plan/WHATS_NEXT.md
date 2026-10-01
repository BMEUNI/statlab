# What's next

Handoff note, finalized 2026-09-11 after chunk 07. Read this first, then `00_index.md`, then
`PLAN.md`.

This file holds what is **not** written down elsewhere: live state, how the working loop actually
operates, and the lessons that cost something to learn. Architecture lives in `PLAN.md`; the chunk
map, hard rules and verified environment facts live in `00_index.md`. Do not duplicate either here.

---

## State

**9 of 41 chunks complete, plus revision batch 09r.** Phase A (foundation) done; Phase B (MCP server)
underway. Chunk 11 next.

| Verified 2026-10-01, after chunk 10 | |
|---|---|
| Tests | 1563 (1562 passed, 1 skipped — chunk 06's F9, by spec) |
| Coverage | 100 % line **and** branch (2478 stmts, 636 branches, 0 missed) |
| mypy | `--strict` clean on 74 source files, `init_typed = true` |
| ruff | `check` and `format --check` clean |
| Warnings | `filterwarnings = ["error"]`, zero ignores |
| Suppressions | zero `pragma: no cover`; zero `Any`. 23 `# type: ignore` in tests (three permitted shapes); **2 in production**, both the `__signature__` stub gap, justified inline — chunk 10 added **zero** new ones in either category |
| Git | local `master`, chunk 10 applied and **uncommitted**, otherwise tree clean |

Re-verify with `make test && make typecheck && make lint` before trusting this table.

Milestones passed: **M1** (chunk 07) — an external MCP client completes `tools/list` and a
round-trip `call_tool` over real HTTP; a tokenless request is rejected. **M2** (chunk 08) — the
five intake tools pass contract tests, mismatches produce precise field-level errors, and a real
CSV loads to a `raw` artifact over a real MCP session. **M3** (chunks 09 + 10) — every intake,
profiling and splitting tool tested on fixtures; `verify_split` catches a deliberately
overlapping split (and, via P3, a relabelled duplicate that index overlap cannot see).

---

## Information architecture

Four layers, each deriving from the one above. Nothing is restated at two levels; when a lower layer
contradicts a higher one, the higher one wins unless the contradiction is recorded as a deliberate
deviation with a reason.

| Layer | File | Holds | Written by |
|---|---|---|---|
| 1. Architecture | `plan/PLAN.md` (~1900 lines, v1.8) | 36 locked decisions **D1–D36**, the normative 13-node spine, tool catalogue (86 tools), wire protocol, schema, milestones **M1–M13**, and the 21 resolved questions **Q1–Q21** | Human + me, before execution began. Amended only when a chunk proves it wrong |
| 2. Execution map | `plan/00_index.md` | The 41-chunk table with scope, status and dependencies; 7 hard rules; **verified environment facts** (pins, pandas 3.0 behaviour, MCP SDK 2.2.0 API, toolchain config); binding conventions | Me, updated after every chunk |
| 3. Chunk spec | `plan/NN_name.md` | One chunk, unambiguous enough to implement without further questions. Anatomy below | Me, one at a time, never in advance |
| 3b. Revisions | `plan/REVISIONS_IMPLEMENTED.md` | Changes required in code already committed, found after the fact, with evidence. Applied as a batch, like a chunk | Me, after a review |
| 4. Handoff prompt | Not a file — pasted into the implementation agent | Reading order, current state, ranked hazards, non-negotiables, report questions. Anatomy below | Me, per chunk |

**Why specs are written one at a time and never batched.** Every chunk so far has changed something
a later spec would have gotten wrong. Chunk 04's report determined chunk 05's JSON-fidelity
requirement. Chunk 05's report removed a whole subsystem from chunk 06 (the artifact `locked` flag).
Chunk 07's report supplied two SDK facts chunks 08–20 need. A spec written before its predecessor
reports is a spec written against guesses.

**`00_index.md`'s "Verified environment" section is the anti-rediscovery layer.** Anything empirically
established — Python 3.14.3, the exact pins, that pandas 3.0 string columns have dtype `str` not
`object`, that `FastMCP` is now `MCPServer`, that `disallow_any_explicit` is incompatible with the
pydantic mypy plugin — goes there once so no chunk re-derives it. Four MCP assumptions taken from
memory were wrong before that block existed.

---

## The working loop

Five steps per chunk. Steps 1, 2, 4 and 5 are mine; step 3 is a separate implementation agent with
no shared context.

### Step 1 — I draft `plan/NN_name.md`

Before writing, I read the `PLAN.md` sections the chunk touches and **probe anything version- or
environment-specific against the installed packages**. Probing is not optional: it caught that
`disallow_any_explicit` breaks on every `BaseModel`, that all seven pandas dtypes survive parquet
untouched, that `os.link` + `unlink` makes refcounting unnecessary, that a `TEXT PRIMARY KEY` accepts
`NULL`, and that four MCP SDK assumptions were stale. A spec asserting unverified behaviour is a spec
that spends the agent's time discovering my mistakes.

**Exact anatomy — six parts, same order every time:**

```
---
status: untouched | in_progress | completed
dependencies: [01, 02, 05]        # chunk numbers, always < N
completion_date: null
implementation_notes: ""          # agent appends on finish
---

# Chunk NN — <title>

**Goal.** One paragraph. Names the PLAN.md sections and decision IDs it implements.

## 1. Prerequisites & Context
   - State after chunk N-1: test count, coverage, mypy file count, the commit hash
   - Table of existing symbols this chunk reuses, with file paths and why each is relevant
   - Environment facts that bite here, pointing at 00_index.md rather than restating
   - Any decision this chunk forces that PLAN.md does not answer, with alternatives + reasons
   - "What this chunk does NOT do" table: item → which chunk owns it → what it waits on

## 2. Files to Create / Modify
   Explicit table. create vs modify marked. No file appears that the spec does not describe.

## 3. Implementation Spec
   Signatures with full type annotations, docstrings carrying the *reasoning* (not restating the
   name), validation rules numbered so tests can cite them (V1..Vn, S1..Sn), error mapping,
   schema/DDL verbatim where applicable. Prose explains why, never what the code plainly says.

## 4. Test Suite Spec
   A numbered table per test file: id, what it tests, what it must assert. Includes a target count
   and the new total. Every rule from §3 gets a passing AND a failing case. Tests that must derive
   their expectations (get_args, model_fields, PRAGMA introspection) say so explicitly.

## 5. Definition of Done
   Checkbox groups: Tests / Static analysis / Boundary / Behaviour / Housekeeping. Then:
   - "Explicitly out of scope"
   - "Stop and ask if" — conditions where guessing costs more than a round trip
   - "Report back with" — 5-7 numbered questions, the last one feeding the NEXT chunk

## 6+ Addendum (only when the first pass needs correction — see below)
```

Where §3's content comes from: `PLAN.md` for the *what* (schema, tool signatures, decisions),
`00_index.md` for the *environment*, the previous chunk's report for *corrections*, and my probes for
anything neither covers.

### Step 2 — I write the handoff prompt

Not a file; pasted directly to the agent. Ranked by likelihood of going wrong, because an agent
reading a flat list of thirty requirements weights them equally and the spec has already stated them
all once.

**Exact anatomy:**

```
Implement chunk NN. Work only on chunk NN. Stop when its DoD is met — chunk NN+1 is not specced
and you must not invent it.

READ FIRST, IN THIS ORDER
  1. plan/NN_name.md              the spec
  2. plan/00_index.md             hard rules + verified environment (name the relevant block)
  3. plan/WHATS_NEXT.md           conventions + the corrections and their pattern
  4. plan/PLAN.md §x.y, Dnn       the authoritative sections. "PLAN.md wins on any conflict."

CONTEXT: chunks 01..N-1 are committed and green at <hash>
  The exact numbers. "Do not regress any of it."

THE ONE THING MOST LIKELY TO GO WRONG
  A single hazard, with the reason it is tempting and the consequence of getting it wrong.
  Examples used: "do not use ':memory:' — WAL is unsupported there so every assertion passes
  vacuously"; "your training data is mcp 1.x, do not write MCP code from memory"; "do not add
  safety machinery for the fork/prune interaction."

THREE OR FOUR MORE THAT ARE EASY TO GET SUBTLY WRONG
  Numbered, each with the mechanism and the failure mode. Not a checklist — an explanation.

REUSE, DO NOT REDEFINE
  Named existing symbols, and "stop and ask" if reuse turns out not to fit.

<domain non-negotiables>
  Security details, derivation requirements ("derive from get_args, never hand-list"), boundary
  rules. Whatever this chunk can silently get wrong.

CONVENTIONS
  Pointer to WHATS_NEXT.md, plus the two or three that specifically bite here.

TEST SUITE — section 4, roughly N new tests, total ≥ M
  Distribution across files, and any fixture the chunk needs.

DEFINITION OF DONE — section 5. Every checkbox verified by a command you ran, output pasted.

WHEN DONE — update the frontmatter, 00_index.md's row, WHATS_NEXT.md's state table.

REPORT BACK WITH
  5-7 numbered questions. Always: the four gate numbers. Usually: how a specific claim was
  proven, not just that it passed. Always: one question feeding the next chunk.

STOP AND ASK IF
  The named conditions from §5, restated.
```

**Two prompt techniques that have earned their place.** Asking *how* something was proven rather than
whether it passed — "did T3 fire at decoration time or call time, and how did you prove it" —
surfaces tests that assert the weaker property. And asking where the agent *felt* an urge to deviate
tells me whether my reasoning convinced them or merely instructed them; when the answer names a spot
I had not considered, my justification has a hole.

### Step 3 — The agent implements and reports

A separate agent, no shared context, reading only the four files. It reports against the numbered
questions. Reports have been consistently high quality and have caught four of my spec defects.

### Step 4 — I verify independently, never on the report alone

This is the step that has caught everything. Two parts:

1. **Re-run the gates myself:** `pytest -q`, `pytest --cov`, `mypy`, `ruff check`, `ruff format
   --check`. Numbers must match the report.
2. **Probe the specific claims empirically.** Not "is there a test for the constraint" but "does the
   constraint reject the duplicate". Examples that found real defects: inserting a `NULL` primary key
   (accepted — SQLite permits it without `NOT NULL`); inserting two shared-node rows (accepted — the
   `UNIQUE` was decorative over `NULL`); calling `create_artifact` and comparing `row.id` to the path
   stem (they disagreed); grepping for cross-boundary imports (found one the tests did not cover).

**Three of the last five chunks had a defect a green suite did not reveal.** Every one was found by
executing something against the claim rather than reading an assertion.

### Step 5 — I commit, then draft the next chunk

Commits only when the user asks. Never push. Commit messages carry the *reasoning* — what was
decided, what was rejected, and what was measured — because they are the only record of why a
verified fact is a fact.

### The addendum pattern

**Four of seven chunks needed one.** When verification finds a defect, the fix goes into a new
numbered section of the same spec (`## 6. Addendum`, `## 7. Addendum`, `## 8. Addendum`), the
frontmatter goes back to `in_progress`, and the next chunk is explicitly blocked until it passes. The
addendum states what was wrong, what evidence proved it, the fix, and its own DoD and report
questions.

Why in-place rather than a new chunk: the defect belongs to the chunk that introduced it, and a
reader of chunk 05 needs to see the `NULL`-hole correction without knowing to look at chunk 06.

Addenda so far: **02** (`init_typed` — `NewType` and `Literal` constructor args were unchecked),
**05** (two SQLite `NULL` holes + the exactly-once primitive), **06** (boundary violation + ref
identity + `save_*` shape), **07** (public import + type-based `RunPaths` detection).

---

## Conventions that are binding and non-obvious

`00_index.md` has the full list. These are the ones most likely to be violated by someone who has not
read it:

- **Derive, don't duplicate-and-validate.** If field A is fully determined by B, don't store both.
  Violated twice; both fixes were deletions.
- **No implicit string conventions.** No `"mutual_info_k20"`, no "any unreserved dict key is a column
  name", no decorator keyed on a parameter *named* `paths`. Dispatch on types.
- **`model_validate({...})` in negative tests**, kwargs in production and positive tests. Under
  `init_typed`, kwargs are type-checked, so a deliberately-wrong kwarg needs a suppression.
- **Typed keyword-parameter test helpers**, never `def helper(**overrides: object)` — that shape
  forces `**dict` construction and an `arg-type` suppression. The surviving `**overrides` helper in
  `tests/test_domains_base.py` is *not* the pattern to copy.
- **Derive schema and exhaustiveness assertions**, never hand-list them: `get_args`, `model_fields`,
  `PRAGMA table_info`, `PRAGMA foreign_key_list`, `sqlite_master`. A hand-typed list is a second
  place to forget to update.
- **`tuple`, not `list`, on frozen models** — `frozen=True` does not freeze list contents.
- **Boundary isolation:** `backend/` and `mcp_server/` never import each other; both may import
  `statlab_contracts`, which imports only stdlib + pydantic. `mcp_server/` must never import
  `statlab.persistence` (D26 statelessness). **Compute libraries — pandas, numpy, sklearn, scipy,
  statsmodels, matplotlib, joblib — are confined to `mcp_server/`** (chunk 08 D-b, superseding
  "pandas appears in exactly one module": that rule could not survive `tools/intake.py` needing
  pandas too, and was never the real invariant — `PLAN.md` §6's "the backend never imports pandas
  or sklearn" is). Source-scan tests enforce all of it, including `test_boundary_compute_libs.py`
  over `backend/` + `contracts/`.
- **`disallow_any_explicit` must stay OFF** — incompatible with the pydantic mypy plugin for *every*
  `BaseModel`. `tests/test_no_explicit_any.py` replaces it and is stronger.
- **Every value entering a `ToolSuccess` summary or a JSON artifact goes through
  `_frame.json_scalar` / `json_list`** (chunk 09 D-a). A pandas reduction or cell is never placed
  into either directly: `np.int64` fails `JsonValue` validation outright (it is not an `int`
  subclass, unlike `np.float64`, which is a `float` subclass and passes silently while still being
  the wrong type to assert on by value alone); `NaN`/`inf` pass validation but are not valid strict
  JSON once `save_json`'s `allow_nan=False` is reached. `json_scalar` is also the one place
  `pd.Timestamp`/`datetime.date` become `isoformat()` strings, replacing the old `isoformat`
  duck-typing in `describe_artifact`.
- **Count levels with `_frame.observed_counts`, never bare `value_counts()`** (chunk 10 review): a
  categorical's `value_counts()` lists unused categories with count 0, which a bare count mistakes
  for present levels — it made a split refuse a categorical target over a class no row had, and
  `summarize_class_target` report `minority_share` 0.0. `observed_counts` drops the zero-count rows.
  `intake.py`'s two sites are exempt (`_majority_share` takes only the top value; `_level_purity`'s
  groupby uses `observed=True`).
- **No unreachable code, therefore no `pragma: no cover`.** If a line cannot be reached, delete it. If
  you want to bypass a validator to reach a line, that line is the problem.
- **`# type: ignore` is banned in production and permitted in tests only** where a runtime check must
  deliberately violate a static type: mutating a frozen model to prove it raises, passing an invalid
  `Literal`, passing an extra kwarg to prove `extra="forbid"` fires. mypy is *correct* to object in
  all three, so the suppression is the test working, not cheating. There are 23, each justified
  inline. Do not "clean these up" — but challenge any new one outside those three shapes, especially
  `arg-type` on a `**dict` construction.
- **A fourth shape is permitted, in production only: a stdlib or third-party stub gap** — e.g.
  assigning `__signature__` on a function, which the runtime honours but mypy's function type does not
  declare writable. Legitimate under three conditions, because "the stubs are wrong" is also the
  easiest excuse available: the runtime behaviour is **verified** rather than assumed; there is **no
  correctly-typed alternative**; and it carries an **inline comment** naming the gap. Chunk 07 §9 is
  the worked example of both outcomes — two genuine gaps kept, one suppression deleted because it was
  masking a parameter widened to `object` when Starlette's own `ASGIApp` type fit.

---

## Hard-won facts not in `00_index.md`

Things discovered while implementing that no spec would think to state:

- **Run `make typecheck` project-wide before trusting a file-scoped mypy pass.** Chunk 07's third bug
  — `summary` values typed `dict[str, str]` where `dict[str, JsonValue]` was required — surfaced only
  once every new module was checked together.
- **pytest-asyncio cannot hold an `anyio` task-group context manager open across an async fixture's
  `yield`.** Teardown resumes as a different `asyncio` task, giving "Attempted to exit cancel scope in
  a different task than it was entered in". Reproduced with a bare `anyio.create_task_group()` before
  any MCP code. Fix: a *synchronous* fixture returning an `@asynccontextmanager` factory, so each test
  opens its own session inside its own task. Chunk 21 will hit this.
- **Runtime identity and mypy-strict cleanliness are different questions.** Two import paths can be
  the same object while only one passes `no_implicit_reexport`. Check both before recording an import
  path as a fact.
- **A test that confirms a guard exists is not a test that demonstrates the failure it prevents.**
  This is the single most valuable lesson of the project so far. See the corrections below.

---

## Corrections so far, and the patterns in them

Nineteen defects, plus one methodology review (17). Rows 14–17 came from the full review of
2026-10-01; their fixes are in `PLAN.md` D32–D36 and `REVISIONS_IMPLEMENTED.md`. Rows 18–19 are two
defects in my *own* 09r revision text and its first implementation, caught in review before 09r was
accepted. Of rows 1–13, seven were mine (chunks 01–07); four more were caught during
chunk 08/09's own implementation and belong to those chunks (the `\Z`/`\z` regex-engine mismatch,
the `LookupError` gap, the vacuous-empty-comparison bug, chunk 07's T23 regex false-positiving on
a legitimate `Literal[...]`) — not mine, not latent in `PLAN.md`, found and fixed in the same pass
that introduced or exposed them, which is why they are not addenda. One was latent in `PLAN.md`.
One of my *corrections*
was itself wrong and the agent reverted it — recorded because being overruled by evidence is the
loop working.

| # | Defect | Resolution |
|---|---|---|
| 1 | `disallow_any_explicit` mandated without testing it against a model | Flag removed; source-scan test instead |
| 2 | `retryable` on `Failure`, fully determined by `error_class`, policed by a validator | Field deleted; derived helpers |
| 3 | Chunk 04's rule S7, redundant with S3, kept for message quality | Rule deleted; unreachable `AssertionError` deleted too |
| 4 | Two node-status vocabularies sharing one name since `PLAN.md` §10.2 was written | `NodeExecutionStatus` derived from `NodeStatus` |
| 5 | `UNIQUE` and `PRIMARY KEY` reading as present while enforcing nothing over `NULL` | `NOT NULL` on all ids; unique expression index over `IFNULL(candidate_id, '')` |
| 6 | `statlab.errors` imported inside `mcp_server` — a real D13 boundary violation | `ToolArtifactError` defined locally; both boundary directions now tested |
| 7 | `create_artifact` minted an id independent of the ref used for the file path | `new_artifact_ref()` in `statlab_contracts`; `create_artifact` takes `ref`; invariant tested |
| 8 | `@tool` found its `RunPaths` parameter by matching the name string `"paths"` | Found by resolved annotation; raises at decoration time on zero or two |
| 9 | `SHA256_PATTERN` written `r"^[0-9a-f]{64}\Z"` — valid Python `re`, but fed to `pydantic.Field(pattern=...)`, which compiles with the Rust `regex` crate and does not recognise `\Z` | Lowercase `\z`, which both engines accept with the same end-of-string meaning |
| 10 | `load_csv` caught only `ValueError` around `pd.read_csv`; an unknown codec name raises `LookupError`, a sibling hierarchy, not a `ValueError` subclass | `except (ValueError, LookupError)` |
| 11 | `detect_leaky_columns` on an entirely-null target: two empty series compare equal under `.astype(str).equals()`, vacuously flagging every other column "identical to target" | Early return (no data, no finding) before any comparison runs |
| 12 | `@tool`'s async wrapper ran sync bodies on the event loop, serialising every tool call across all parallel runs (D17). T17 passed regardless: it proved two calls *finished*, not that they *overlapped* | Body offloaded with `anyio.to_thread.run_sync`; T17b uses a barrier that deadlocks unless bodies overlap, and was confirmed to fail on the old code |
| 13 | Chunk 07's T23 (`no module holds a tool-name list`) source-scan regex matched any `["x", "y"]`-shaped string pair, including a legitimate `Literal["pearson", "spearman"]` type annotation chunk 09 introduced | Negative lookbehind for `Literal` added to the regex; confirmed it still catches a real tool-name-list shape |
| 14 | The walking skeleton (chunk 15) fit an inferential model with tools owned by chunk 16, sequenced after it — the same defect as the earlier `rank_models` fix, missed for inference | Chunks swapped: inference is 15, the gate is 16 (review 2026-10-01) |
| 15 | My chunk 08 spec told L3 to skip features with unique share > 0.5 *before* binning, so `detect_leaky_columns` never flagged any continuous leak against a class target. Green, because tests planted leaks only in categoricals | Applied as `REVISIONS_IMPLEMENTED.md` R2: numeric feature vs class target now uses rank AUC (`_rank_auc`, `LEAK_AUC = 0.99`, rule `rank_auc`); `_level_purity` is non-numeric-only. Flagged at 221/210/60/20/10 positives of 400 and with 20 % nulls |
| 16 | `validate_contract` compared target values as strings; a 0/1 target with one null loads as float, so valid contracts reported mismatches | Applied as R1: `_declared_matches_observed` + `_as_float_repr` compare as floats when the target is `is_numeric_column`; float64 0/1 with a null and `positive_class=1` → valid |
| 17 | Methodology: the test lock keyed on one artifact id (transformed test copies and forks escaped it); the champion's validation score was reported as if unbiased; tuning-by-CV and tuning-on-valid both specified; calibration and target encoding had no leak-safe fold; post-EDA p-values reported as confirmatory, uncorrected | `PLAN.md` D32–D35 |
| 18 | My R4 text said `fork_artifacts` copies `split`/`derived_from_test` onto *child rows* — contradicting chunk 06's shared-ref design (a fork hard-links under the same ref and creates no rows). The first implementation's test "proved" the copy by registering a child row whose id differed from its file stem, violating `row.id == Path(row.path).stem` | R4 amended (PLAN.md D11): forks keep the parent's refs, hard-link files under the same ref, create no rows; lineage is inherited. Test renamed `test_f2_fork_keeps_parent_refs`, asserts same stem, same inode, resolved row `split='test'`/`derived_from_test=True`, `list_artifacts(child) == ()` |
| 19 | R2's first fix (skip the unique-share rule for numeric features + purity over paired non-null rows) passed its own test only because the test used an exactly balanced 200/200 `np.tile` target: binned purity of a perfect leak reaches 1.0 only when the class boundary falls on a decile edge (0.975 at 210/400, 0.9525 at 221, 0.950 at 180/60/20), so it still missed most real leaks | R2 final rule: rank-based one-vs-rest AUC, balance-insensitive, 1.000 at every split; tests parametrised over 200/210/221/60/20 positives so an exact split alone is no longer evidence |
| — | *My* §8.1 instruction to use the "public" `create_mcp_http_client` path | Reverted by the agent: `mypy --strict`'s `no_implicit_reexport` rejects it, and the underscore module's `__all__` is the one that exports the symbol |

**1–3 share a shape:** adding a mechanism that *reads* as rigour — an extra flag, field, or rule —
where the correct move was subtraction. 2 and 3 are the same error twice. The detector: **if a check
can only be satisfied trivially, or can never fire, it is not a check.**

**4–8 share a different shape, and it is the important one: a guarantee enforced by convention or by
name rather than by type.** A markdown table saying `node ∈ {pending, running, ...}`, a `UNIQUE` over
a nullable column, a boundary test covering one of three directions, an id the caller cannot
influence, a decorator keyed on a parameter's name — all read as correct, all were green, and all
failed silently rather than loudly. The fix each time was the same move: **make the wrong thing
unrepresentable instead of merely unlikely.** `NOT NULL`, a derived `Literal` union, a type-based
lookup, an explicit `ref` parameter.

The testing corollary, now a rule for every remaining chunk: **a test must demonstrate the failure the
guard prevents, not confirm the guard exists.** Insert the duplicate. Delete the parent. Import across
the boundary. Mint the id twice. Chunk 05's `IntegrityError` probes and chunk 06's P10/P11 are the
right shape; the boundary test that covered only `contracts/` is the wrong one.

---

## Deferred deliberately

Not oversights. Each waits on something that does not exist yet.

| Deferred | Lands in | Waiting on |
|---|---|---|
| Estimator-name validation, model-family cap (D28), `max_candidates` | 27b | The registry (13) and the contract together |
| Candidate id minting (`c_01`, sequential and human-readable) | 22 | The engine that creates waves |
| Repositories for 10 of the 15 tables | Whichever chunk first writes to each | Callers that exist |
| Provider-name env check (`DEEPSEEK_API_KEY`) | 26 | D18's rule that no provider is named outside `llm/` |
| Cancelling a *running* node | 31 | Takes effect at the next tool-call boundary (D36c); resolves live rows to `failed`, needs no new status |
| `skills/modelling.md` heuristics and pitfalls | **the human, by hand** | Nodes existing first (D12, §9.3). Chunk 26 scaffolds headings with `TODO:` only |

---

## Next: chunk 11

Chunk 10 is **applied and verified**. The next step is chunk 11 (cleaning tools).

### Chunk 11 — cleaning tools

Nine tools with the `fit_*`/`apply_*` separation (§6.1 rule 3). Two things from D34d and this
chunk's handoff carry directly into node 4:

- **`drop_duplicate_rows` must be allowed in `data_splitting`.** D34d's structural, target-independent
  pre-split drops (exact duplicates) run once in node 4, before the split tool — so this tool's
  `allow_in` must include `data_splitting`, not only a cleaning phase. The duplicate the split would
  otherwise let leak across train/test is exactly what `verify_split`'s check 2 exists to catch; D34d
  removes the leak at the source.
- **No catalogue tool yet drops rows above a `drop_rows_above` missing share, and D34d needs one in
  node 4** (`00_index.md` row 11). Decide in chunk 11 whether that is a new tool or a mode of an
  existing one. Chunk 10's report (item 7) argues for a **mode of `drop_duplicate_rows`** (or a shared
  `drop_rows` tool with a `reason`): both are target-independent, per-row structural drops that run at
  the same point in node 4, count what they removed, and return the trimmed frame — the same shape the
  null-target drop already takes inside the split tools. A separate single-purpose tool would duplicate
  that plumbing for one predicate. The frames seen here (churn 240×7, messy 60×11) are small and
  row-wise; neither drop needs streaming or chunking, so one row-mask-and-count tool covers both.

Nothing in chunk 11 touches `backend/` or `contracts/`.

## Chunk 10 — completed (summary)

`split_random`, `split_stratified`, `verify_split` (`splitting.py`), plus `is_class_target`
extracted to `_frame.py` and `save_dataframe(split=...)`. Verified by the gates below and by the
full DoD mutation sweep. Gates after the 2026-10-01 review corrections: **1562 passed, 1 skipped**
(1563 total), **100 % line and branch** coverage (2478 stmts, 636 branches, 0 missed), mypy
`--strict` clean on **74 source files**, ruff `check` + `format --check` clean. `splitting.py` adds
**no** `# type: ignore` (the two production
suppressions are still only `tool.py`'s `__signature__` stub gap) and uses **no** sklearn and **no**
`reset_index`. Nothing under `backend/`/`contracts/` changed.

**Mutation evidence** (each applied, tests run, restored; `splitting.py` has no residual diff):

| Mutation | Tests that failed |
|---|---|
| `MIN_SPLIT_ROWS = 0` | `test_guard_too_few_total_rows`, `test_guard_too_few_rows_continuous_target`, `test_min_split_rows_is_30`, `verify_split::test_29_row_valid_fails_size` (4) |
| `MIN_CLASS_ROWS_PER_SPLIT = 0` | `test_guard_minority_class_too_small_stratified`, `test_guard_tiny_class_under_random`, `verify_split::test_zero_of_a_class_fails`, `verify_split::test_four_of_a_class_fails` (4) |
| drop `sort_index()` from the fingerprint | `test_tool_fingerprint_is_row_order_invariant` (1) |
| `index=True` in the duplicate hash | `verify_split::test_relabelled_duplicate_fails_duplicates_not_overlap`, `test_shuffled_column_order_still_detects_duplicate`, `test_messy_copies_fail_duplicates` (3) |
| remove the null-target drop | `test_null_targets_dropped_and_warned`, `test_null_drop_gives_same_sizes_as_prefiltered` (2) |
| `reset_index(drop=True)` on the outputs | `test_partitions_are_disjoint_and_cover_the_input`, `test_labels_preserved_on_non_range_index`, `test_different_seed_differs`, `test_stratified_outputs_disjoint_and_cover`, `verify_split::test_clean_split_passes_all_four` + 2 clean-split checks (7) |

The `sort_index` mutation is the one that nearly slipped through: a comparison against the stored
test artifact cannot catch it, because the tool's output index is always ascending
(`iloc[np.sort(...)]`), so `sort_index` is a no-op there. Catching it needs a direct test of
`_fingerprint` on a deliberately row-shuffled frame — the "passes by construction" trap the handoff
named, found and closed before the gate rather than after.

**Measured answers to the report questions:** `split_stratified` on `churn_frame` holds class
proportions to `max_drift ≈ 0.004` (train 100/44, valid 33/15, test 33/15, pooled 0.69/0.31) — a
`max_drift_z` well below the `BALANCE_DRIFT_Z_WARN = 3.0` threshold, so it never warns.
`verify_split` on a hand-built overlapping split fails both `index_overlap` (train/test = 10) and
`duplicate_rows` (train/test = 10) with `passed = false`. **Review correction (2026-10-01):** the
original absolute `BALANCE_DRIFT_WARN = 0.05` was NOISE — `split_random` on `churn_frame` warned on
**60 of seeds 0–99**, because one standard deviation of a 31 %-minority share at n=48 is ~0.066.
It was replaced by a z-score rule (`z = |share_in_split − share_in_pooled| / sqrt(p*(1-p)/n_split)`,
warn at `z > 3`): over 200 random seeds the max z was 2.56 (0 warnings), while a split cut from the
target-sorted frame scores z = 5.34. So `split_random` on `churn_frame` over seeds 0–49 now never
warns, and the reason to prefer `split_stratified` for a class target stays measurable. Nothing
about parquet or hashing surprised beyond the documented P1–P3 (they reproduced exactly).

## Revision batch 09r — completed (summary)

Seven fixes (R1–R7) from the full review of 2026-10-01 (`PLAN.md` v1.8), applied as one batch before
chunk 10, verified by the reviewer, and recorded in `REVISIONS_IMPLEMENTED.md`. Gates after the batch:
1483 passed, 1 skipped (1484 total), 100 % line and branch coverage (2289 stmts, 588 branches), mypy
`--strict` clean on 71 source files, 2 production `# type: ignore` and 23 in tests — zero new
suppressions. `001_initial.sql` was edited **in place** (nothing is deployed), as in chunks 05/06.

**R1 — `validate_contract` compares as floats on numeric targets.** `_declared_matches_observed` +
`_as_float_repr` replace the inline `str()` comparison in C7/C8. A float64 0/1 target with one null and
`positive_class=1` is now valid; `positive_class=2`/`'abc'` is a mismatch (not a crash); numeric
multiclass with a null and `class_order=['0','1','2']` is valid.

**R2 — the leak rule for a numeric feature vs a class target.** The first fix was found **incomplete**:
binned purity of a perfect leak reaches 1.0 only when the class boundary lands on a decile edge, so it
missed the leak at most balances and passed its own test only because that test used an exactly balanced
`np.tile` target. The final rule is a rank-based one-vs-rest AUC (`_rank_auc`, folded `max(auc, 1−auc)`,
max over classes with ≥ `LEAK_MIN_CLASS_ROWS = 10` rows, flagged at `LEAK_AUC = 0.99`, rule `rank_auc`).
`_level_purity` is now non-numeric-only — its `pd.qcut` branch and `_QCUT_BINS` were deleted — and
`LEAK_UNIQUE_SHARE_SKIP` gates non-numeric features only. The perfect leak is flagged at 221, 210, 60,
20 and 10 positives of 400 and with 20 % nulls; a random feature vs a 10/400 minority class gives 0
false positives in 200 seeds; mutation check: `LEAK_AUC = 1.01` fails 9 tests. This is the lesson of
row 19: a test on an exactly balanced split is not evidence that a balance-sensitive rule works.

**R3 — L2 runs whenever both sides are numeric.** The `not is_class_target` condition was dropped and
both sides now use `is_numeric_column` (bool excluded), with a constant-column skip added because L2
now also sees class targets. A 15-level numeric target with a near-duplicate feature is flagged
`rank_correlation`. Measured bound: a perfectly monotone feature's max rank correlation is 0.866 (k=2),
0.943 (k=3), 0.980 (k=5), 0.986 (k=6), so L2 cannot fire below 6 levels — where L3 covers the leak.

**R4 — schema and wire fields for the D32 lineage lock.** `SplitRole`; `ArtifactMeta.split`;
`RunStatus + 'superseded'`; `runs.test_fingerprint`; `artifacts.split`/`derived_from_test`/`kind`
CHECKs; `repository.set_test_fingerprint` (write-once) and `count_prior_test_scorings`;
`create_artifact` gains `split`/`derived_from_test` with a `meta.split == split` guard. The
CHECK-vs-`Literal` drift test was extended to `kind` and `split`. The fork-semantics correction (row 18)
is the important one: forks keep the parent's refs, hard-link under the same ref and create **no** rows;
lineage is inherited. `test_f2` was renamed `test_f2_fork_keeps_parent_refs` and now asserts same stem,
same inode, resolved row `split='test'`/`derived_from_test=True`, and `list_artifacts(child) == ()`. One
integration fix: `tests/test_db_migrations.py` `known_valid_values` gained `'kind': 'dataframe'` because
the new kind CHECK fired before the FK in `test_m13`.

**R5–R7 — small.** `envelope.py`'s comment uses the canonical `autonomous` token (no test);
`detect_outliers`' summary key renamed `share` → `share_of_non_null`, value unchanged (one test); the
`@tool` module docstring now states that bodies run in worker threads and a running body cannot be
cancelled, so cancel/pause take effect only between calls and a time budget must be enforced in the body
(documentation only).

**One environment fact for later chunks:** under pandas 3.0 copy-on-write, `Series.to_numpy()` returns
a **read-only** array — use `to_numpy(copy=True)` before mutating. Recorded in `00_index.md`'s pandas
3.0 block.

## Chunk 09 — completed (summary)

Nine profiling tools (`profile_schema`, `summarize_numeric`, `summarize_categorical`,
`report_missingness`, `summarize_target`, `compute_correlations`, `detect_outliers`,
`plot_distribution`, `plot_target_relation`) plus the shared `_frame.py` helper module (D-a):
`json_scalar`/`json_list` (the fix for P1–P3), `is_numeric_column` (the fix for P4), and
`iso8601_parse_share`/`CLASS_TARGET_MAX_LEVELS` moved from `intake.py` unchanged — chunk 08's
intake tests pass without a single edit after the move, confirmed by a zero-diff `git diff`.

**The cross-cutting problem this chunk actually solved:** every numeric summary written the
direct way either fails `ToolSuccess` validation (`np.int64` is not an `int`) or writes invalid
JSON (`NaN`/`inf` as bare tokens). `json_scalar` is now the one conversion point every tool in
the catalogue is expected to use, and the chunk's own property test (S14) proves it holds across
all nine profiling tools and three of chunk 08's five intake tools (not `load_csv`, which needs a
real uploaded file, and `validate_contract`, covered by its own dedicated strict-JSON test) on
two frames — not merely in the few places someone remembered to add a check.

**A real pre-existing test bug found, not caused by sloppy new code:** chunk 07's T23 (`no module
holds a tool-name list`) source-scans `tools/` with a regex matching any `["x", "y"]`-shaped
string pair. `profiling.py`'s two legitimate `Literal["pearson", "spearman"]` /
`Literal["iqr", "zscore"]` type annotations are not tool-name lists, but the regex could not tell
the difference — it had simply never been exercised against a `Literal` before. Fixed with a
negative lookbehind for `Literal` in the regex, verified the fix still flags a real tool-name-list
shape.

**Two genuinely unreachable branches deleted, one real reachable gap found alongside them:**
`report_missingness`'s `if union == 0: continue` guard is mathematically unreachable — both
columns in every pair come from `columns_with_nulls`, so each already has at least one null,
which alone makes the union non-empty. But a sibling case right next to it, in
`compute_correlations`, is real: two *non-constant* columns with zero overlapping non-null rows
produce a genuinely `NaN` pairwise correlation (distinct from the already-handled constant-column
case), and needed its own test rather than being assumed away.

**On `describe_artifact`'s pre-existing `isoformat` duck-typing (chunk 07), now replaced by
`json_scalar`:** investigating whether it was *already* broken for `np.int64` (the question the
handoff prompt asked) found that `DataFrame.to_dict(orient="records")` already converts every
numpy scalar to its native Python equivalent, including turning a nullable `Int64` NA into plain
`None` — so the integer hazard was never real at that specific call site. The one real latent gap
was `NaN` floats, which `to_dict()` leaves untouched and which `isoformat`-duck-typing had no
reason to catch (`float("nan")` has no `.isoformat`): they would have passed `ToolSuccess`
validation and silently become a bare `NaN` token if that summary were ever round-tripped through
`save_json` — not currently exercised, since `describe_artifact` only ever returns a summary.

**The concurrency test was verified by deliberately breaking it:** `plot_distribution`'s 8-way
barrier test was confirmed to actually detect the serialisation bug T17b was written against, not
just pass by construction — reverting `tool.py`'s `anyio.to_thread.run_sync` offload to an inline
call made the barrier time out and the test fail, exactly as the regression it guards against
would behave; reverted back to the real implementation afterward with a clean `git diff`.

## Chunk 08 — completed (summary)

`load_csv`, `infer_column_roles`, `validate_contract`, `detect_id_columns`, `detect_leaky_columns`.
The first tools written against chunk 07's decorator for real, and the first to exercise
milestone M2. All six of the spec's named facts (P1–P6) held exactly as recorded against the
installed `pandas==3.0.5`; no STOP condition triggered.

**The decision it carried, resolved:** `load_csv` takes `dataset: str` (the upload's sha256), not
a filesystem path (D-a) — the schema makes a traversal shape unrepresentable rather than merely
rejecting one. Defence in depth (`resolve()` + `is_relative_to()`) still guards the one remaining
hole, a symlink planted inside `datasets/` pointing outside it; both layers were verified
independently (schema rejection over a real MCP session with the body monkeypatched to fail
loudly if ever reached, and a real symlink in a temp directory for the filesystem-level check).

**A real bug found in this chunk's own pre-staged groundwork, not in the tool code:**
`SHA256_PATTERN` (`layout.py`) was written as `r"^[0-9a-f]{64}\Z"` — syntactically valid Python,
and the exact spelling `ARTIFACT_ID_PATTERN` already used safely as a compiled `re.Pattern`. But
`SHA256_PATTERN` is also fed to `pydantic.Field(pattern=...)`, which compiles patterns with the
Rust `regex` crate (`pydantic-core`), not Python's `re` module — and that crate does not recognise
`\Z`, raising `SchemaError: unrecognized escape sequence` at import time. The fix is the lowercase
`\z`, which both engines accept with the same "absolute end of string" meaning (verified: a
literal trailing `z` character does not match under either engine, confirming `\z` is read as the
anchor, not a literal). The general lesson: a pattern string's correctness depends on which engine
reads it, and a string shared between a plain `re.compile` and a pydantic `Field(pattern=...)`
needs to satisfy both, which `\Z` does not.

**Two real production bugs found while writing tests, both fixed:**
1. `LookupError` (an unknown codec name) is not a `ValueError` subclass — `load_csv`'s original
   `except ValueError` clause never caught it, so an unrecognised `encoding` would have reached
   the decorator's default `bug` classification with a traceback, exactly the failure mode P1
   warns about for the three documented exceptions. Fixed by catching `(ValueError, LookupError)`
   together.
2. `detect_leaky_columns` on an entirely-null target: after dropping null rows, both the target
   and the comparison series become empty, and two empty series compare equal under
   `.astype(str).equals()` — every other column would have been vacuously flagged "identical to
   target". Fixed with an early return (no data, no finding) before any comparison runs.

**Dead code found and deleted, not merely left uncovered:** `_level_purity`'s `except ValueError:
return None` and `_majority_share`'s `counts.empty` guard were both genuinely unreachable once
written against the actual call sites (`qcut(q=10, duplicates="drop")` collapses every tried input
— empty, all-null, single-value, constant — to zero categories rather than raising, confirmed by
direct probe; and `_majority_share` is only ever called with the already-non-empty `target_series`
after the fix above). Removed rather than covered with a contrived test, per the project's own
"no unreachable code" rule.

**D-b, the pandas boundary rule, is now this:** confined to `mcp_server/` (pandas, numpy, sklearn,
scipy, statsmodels, matplotlib, joblib), not "exactly one module" — see the conventions section
above for the reasoning and the new `test_boundary_compute_libs.py`.

## Two named checkpoints ahead

From `00_index.md`: **chunk 16** (walking skeleton — first end-to-end run; moved after inference in the 2026-10-01 review) and **chunk 24**. Treat
both as integration gates rather than ordinary chunks. If the architecture is wrong somewhere, 15 is
where it shows.
