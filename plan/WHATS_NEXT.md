# What's next

Handoff note, finalized 2026-09-11 after chunk 07. Read this first, then `00_index.md`, then
`PLAN.md`.

This file holds what is **not** written down elsewhere: live state, how the working loop actually
operates, and the lessons that cost something to learn. Architecture lives in `PLAN.md`; the chunk
map, hard rules and verified environment facts live in `00_index.md`. Do not duplicate either here.

---

## State

**13 of 41 chunks complete, plus revision batch 09r.** Phase A (foundation) done; Phase B (MCP server)
underway. Batch B2 (chunks 14 + 15) next; remaining work runs in batches (`00_index.md`).

| Verified 2026-10-01, after batch B1 (chunks 12 + 13, incl. review) | |
|---|---|
| Tests | 1729 (1728 passed, 1 skipped — chunk 06's F9, by spec); suite ≈ 27 s |
| Coverage | 100 % line **and** branch (3405 stmts, 886 branches, 0 missed) |
| mypy | `--strict` clean on 83 source files, `init_typed = true` |
| ruff | `check` and `format --check` clean |
| Warnings | `filterwarnings = ["error"]`, zero ignores |
| Suppressions | zero `pragma: no cover`; zero `Any`. 23 `# type: ignore` in tests (three permitted shapes); **2 in production**, both the `__signature__` stub gap, justified inline — B1 added **zero** |
| Git | local `master`, batch B1 committed, tree clean |

Re-verify with `make test && make typecheck && make lint` before trusting this table.

Milestones passed: **M1** (chunk 07) — an external MCP client completes `tools/list` and a
round-trip `call_tool` over real HTTP; a tokenless request is rejected. **M2** (chunk 08) — the
five intake tools pass contract tests, mismatches produce precise field-level errors, and a real
CSV loads to a `raw` artifact over a real MCP session. **M3** (chunks 09 + 10) — every intake,
profiling and splitting tool tested on fixtures; `verify_split` catches a deliberately
overlapping split (and, via P3, a relabelled duplicate that index overlap cannot see). **M4**
(chunks 11 + 12) — every `fit_*` learns on train and every `apply_*` replays it unchanged on a
frame with different statistics; the target encoder is out-of-fold and refuses train rows.

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

Twenty-one defects, plus one methodology review (17). Rows 14–17 came from the full review of
2026-10-01; their fixes are in `PLAN.md` D32–D36 and `REVISIONS_IMPLEMENTED.md`. Rows 18–19 are two
defects in my *own* 09r revision text and its first implementation, caught in review before 09r was
accepted. Rows 20–21 came from batch B1's review. Defects caught in chunk 10/11 reviews
are recorded in those chunks' addenda (§6), not here. Of rows 1–13, seven were mine (chunks 01–07); four more were caught during
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
| 20 | Chunk 13's tuning CV used `n_jobs=-1`: it broke D17 and deadlocked the test run (loky workers started from a tool-body thread), stalling the implementation stage for an hour | Pin resource rules with a spy test, not a comment. Nothing in a tool body may spawn workers (`00_index.md`, sklearn block) |
| 21 | Chunk 13's "predict uses frame column order" mutation survived: every test frame had its columns in fitted order | A mutation that survives means the tests share an accidental property. Vary it (here, reorder the columns) |
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

## Next: batch B2 (chunks 14 + 15)

Two specs, one cycle (`00_index.md`, "Execution batches"): chunk 14 (evaluation, plus
`rank_models` and `compare_models`) and chunk 15 (inference). They are independent apart from
shared fixtures, so they can run as two sequential stages with one review.

- **Chunk 14** scores `predictions` artifacts against truth. Chunk 13 indexes predictions by the
  frame's labels, with columns `prediction` and `proba__{class}` in `FittedModel.classes` order.
  Align by label, never by position, and refuse a label mismatch. Every `score_*` reports a
  bootstrap 95 % CI (D33b): 1000 resamples, seeded. Precision, recall and F1 need
  `zero_division=0`, because the zero-division warning is an error under `-W error` (chunk 13 P4).
- **Chunk 15** builds the inferential matrix (dummy coding with an explicit reference level, no
  scaling, D31) and the statsmodels fits. It applies D35: confirmatory/exploratory labels and Holm
  correction. statsmodels is untyped here, like sklearn: keep it behind `object` and narrow.
- Both chunks: import sklearn and statsmodels **lazily inside tool bodies** (chunk 12's scipy
  fact). Use `n_jobs=1` in everything (chunk 13's deadlock).

## Batch B1 — completed (summary)

**Chunk 13 (training).** `registry.ESTIMATORS` holds the six D25 estimators. The six training
tools are `list_estimators`, `train_estimator`, `tune_estimator`, `predict`, `predict_proba` and
`get_model_params`.
- The contract snapshot owns `cv_folds` and the tuning budget.
- CV always shuffles with the injected seed.
- Calibration is `CalibratedClassifierCV` on train, after a class-count pre-check that turns
  sklearn's `ValueError` into a `data` failure.
- `tune_estimator` stops at `max_fit_seconds` and returns params.
- Predictions keep the frame's labels.

The implementation stage **stalled and was cancelled**. `n_jobs=-1` in the tuning CV started loky
workers from a tool-body thread, the test run deadlocked, and orphaned resource trackers were left
behind. Review fixed it and pinned it with a spy test. Review also found that the DoD mutation
"predict uses frame column order" survived, because every test frame happened to have its columns
in fitted order. A reordered-columns test now pins it. Valid ROC-AUC on `churn_frame` (near-random
by construction) after tune → calibrated train: 0.51 / 0.57 / 0.58 for logistic regression,
random forest and HistGB. The point is that the whole 10 → 11 → 12 → 13 pipeline runs.

**Chunk 12 (encoding)** follows.



Eight encoding/scaling tools in `encoding.py`: one-hot, ordinal, target, scaler — each a
`fit_*`/`apply_*` pair, `phase="encoding"`, fit `allow_in=["feature_engineering"]`, apply
`allow_in=["feature_engineering", "champion_selection"]`. **Completes M4.** One import line in
`tools/__init__.py`. Nothing under `backend/`/`contracts/` changed.

**The helper move (§3.1), chunk 11 tests unedited.** `_require_columns`/`_save_frame`/`_load_fitted`
and `_as_str` moved from `cleaning.py` to `_frame.py` as public `require_columns`/`save_frame`/
`load_fitted`/`as_str`; `load_fitted` is now generic over an unconstrained PEP 695 `[T]`, so
chunks 12–15 add fitted-state classes without touching a constraint tuple. `cleaning.py` changed
only its imports and one local variable renamed (`as_str = [...]` → `mapped = [...]`) to stop it
shadowing the newly-public `as_str`; **no chunk 11 test was edited** (65 cleaning tests green).

**Only target encoding uses sklearn, and it is imported lazily.** Scaling and ordinal encoding are
pure pandas (readable dict fitted state); target encoding cross-fits a `TargetEncoder` out-of-fold
(D34c). sklearn pulls scipy (unpinned transitive), and `tools/__init__.py` imports every tool
module at init, so a module-level sklearn import broke chunk 09's `test_s11_spearman_does_not_
import_scipy` — the fix is a lazy import *inside* the two target-encoder bodies, recorded as a new
`00_index.md` fact. First module in the project to do this; chunk 13 (sklearn estimators) will too.

**Gates:** 1669 passed, 1 skipped (1670 total); **100 % line and branch** (3061 stmts, 812
branches, 0 missed); mypy `--strict` clean on **79 files**; ruff `check` + `format --check` clean.
`encoding.py` adds **no** `# type: ignore` (the 2 production suppressions are still only `tool.py`'s
`__signature__` gap), **no** `Any`, **no** `pragma: no cover`, **no** `reset_index`, **no** bare
`value_counts()`.

**The leak tests are real, not passing-by-construction.** The core target-encoder test compares the
returned train against BOTH `TargetEncoder.fit_transform` (must **equal** — out-of-fold) and
`fit(...).transform(train)` (must **differ** — in-sample); equality alone would pass an in-sample
implementation. The replay tests build a valid whose own statistics differ from train's (scaler
mean 10 std 2 → valid 14 scales to exactly 2.0 using the *train* scale). The train-label guard test
shows a frame sharing **one** label is refused, not just the whole-train frame.

**Mutation evidence** (each applied, `test_tools_encoding.py` run, restored; `encoding.py` has no
residual diff — `diff` against a backup is clean):

| Mutation | Tests that failed (count) |
|---|---|
| `apply_onehot_encoder` relearns levels from the frame | `..._levels_learned_on_train_replayed_on_valid`, `..._unseen_and_null_counted` (2) |
| `fit_target_encoder` uses `fit().transform()` instead of `fit_transform()` | `..._oof_equals_fit_transform_and_differs_from_insample`, `..._same_seed_same_output_different_seed_differs` (2) |
| remove the train-label overlap check in `apply_target_encoder` | `..._on_train_frame_is_tool_param`, `..._frame_sharing_one_label_is_refused` (2) |
| `apply_scaler` recomputes the parameters from the frame | `..._standard_learned_on_train_replayed_turns_14_into_2`, `..._int64_input_works` (2) |
| remove the zero-spread skip in `fit_scaler` | `..._zero_spread_column_skipped_with_warning` (1) |
| an unseen ordinal level maps to 0 instead of NaN | `..._unseen_becomes_nan_and_counted` (1) |

**Report answers.**

3. **Integration (`cast → split → impute → one-hot → scale` on `churn_frame`), final train columns
and dtypes:** `customer_id: float64`, `age: float64`, `plan__basic: bool`, `plan__premium: bool`,
`plan__standard: bool`, `signup_date: str`, `satisfaction__high: bool`, `satisfaction__low: bool`,
`satisfaction__medium: bool`, `notes_free_text: str`, `churned: int64` (the target). Identical
columns on train and valid; the engineered columns (`age`, the six dummies) have no nulls. The
`signup_date`/`notes_free_text` columns remain `str` because the integration pipeline does not
encode them (they would be dropped or feature-engineered by other node-8 tools, not these four).

4. **What surprised me about `TargetEncoder`.** Two things beyond P1–P3. (a) The `<5-rows` class
guard: `StratifiedKFold` raises a *`UserWarning`* ("least populated class has N members") **before**
the `ValueError`, so under `-W error` the warning is what crashes first — the proactive count check
has to fire ahead of the fit, which the spec called for and the probe confirmed. (b) `classes_`
holds `np.str_`, not builtin `str`, so the multiclass output names need an explicit `str(c)` or the
column labels carry numpy scalars. Also, `fit_transform` genuinely differs row-by-row from
`fit().transform()` even for a 2-level feature — the OOF values are *not* a reordering of the
in-sample ones, so the equal/differ pair of assertions is a strong, non-vacuous test.

5. **For chunk 13 — dtypes the encoders output that sklearn might reject or misread.** The outputs
are deliberately sklearn-safe: one-hot dummies are **plain `bool`** (not nullable `boolean`), the
scaler and ordinal/target encoders emit **`float64`** — all three go straight into a numpy array an
estimator accepts. The two risks chunk 13 should watch are *inputs that reach an estimator unencoded*,
not these outputs: (i) a **nullable `Int64`** feature left unscaled/unencoded — sklearn's estimators
reject pandas NA and need it cast to `float64` first (chunk 11's P1 is the same hazard on the clip
path); (ii) an **ordered `Categorical`** (from `cast_dtypes`) passed to an estimator directly —
sklearn treats category codes positionally and would misread the order unless it is ordinal-encoded
to `float64` first. The ordinal encoder's `float64` NaN for an unseen level also means chunk 13 must
impute after encoding if a model cannot take NaN. None of the chunk-12 outputs themselves are
`boolean`/`Categorical`/`Int64`, so no encoder output needs guarding — only un-encoded passthrough
columns do.




Ten cleaning tools in `cleaning.py`: pre-split (`drop_duplicate_rows`, `drop_high_missing_rows`,
`cast_dtypes`), decide-on-train (`drop_constant_columns`, `drop_high_missing_columns`, `fit_imputer`,
`fit_outlier_clipper`), apply (`drop_columns`, `apply_imputer`, `apply_outlier_clipper`). Fitted state
is a frozen dataclass (`FittedImputer`/`FittedClipper`) pickled as kind `transformer` — **no sklearn**.
One import line in `tools/__init__.py`; the `"transformer"` comment in `contracts/.../artifacts.py`
updated to "a fitted transformer (imputer, clipper, encoder, scaler)" — the only change outside
`mcp_server/`. Gates (after the 2026-10-01 review corrections): **1627 passed, 1 skipped** (1628
total), **100 % line and branch** (2792 stmts, 738 branches, 0 missed), mypy `--strict` clean on
**77 source files**, ruff `check` + `format --check`
clean. `cleaning.py` adds **no** `# type: ignore`, uses **no** sklearn, **no** `reset_index`, **no**
bare `value_counts()`.

**Review corrections (2026-10-01, see `11_tools_cleaning.md` §6).** Four defects fixed, each with a
test that fails on the pre-fix code (proven by revert-run-restore): (1) `cast_dtypes` now takes a
required `target` and never casts it — an agent `boolean` role for a 0/1 target no longer breaks the
`positive_class` contract check (end-to-end test); (2+3) a shared `_as_str` helper routes the
string/ordinal/boolean casts through `Int64`, so integral floats no longer stringify as `"1.0"` and
null every value; (4) `apply_outlier_clipper` casts to `float64` before `clip_iqr`/`winsorize` (the
Int64 clip crash, P1 again) and emits a plain-`bool` flag column with a null flagged `False`.

**The core leak test is real, not passing-by-construction.** `fit_imputer` on a train whose median is
10 then `apply_imputer` on a valid whose own median is 99 fills with **10**; the clipper's bounds learned
on a 0–10 train clip a 1000–3000 valid to the train upper bound. Both are built by hand so a refit on the
applied frame gives a different, detectable answer — proven by mutation M1 (recompute the fill from the
applied frame → 4 tests fail, incl. the leak test) and M2 (recompute bounds at apply → 2 fail).

**Mutation evidence** (each applied, the two cleaning test files run, restored; `cleaning.py` has no
residual diff — confirmed by `diff` against a backup):

| Mutation | Tests that failed (count) |
|---|---|
| `apply_imputer` recomputes the fill from the frame it is given | leak + 3 others (4) |
| the clipper's bounds are recomputed at apply | leak + 1 (2) |
| imputation `drop` uses `dropna` (rows) instead of dropping the column | `test_drop_removes_column_keeps_row_count` (1) |
| remove the target exclusion in `fit_imputer` (`_feature_columns` returns all) | 14 (target-exclusion is shared by the deciders too) |
| `>` → `<` (strict `>` loosened) in `drop_high_missing_rows` | `test_row_at_threshold_kept_above_dropped` (1) |
| `reset_index(drop=True)` on every frame output | 5 (label/order preservation across apply + deciders) |
| remove the `float64` cast before mean/median fills | `test_int64_fractional_median...` (1, the P1 crash) |
| remove the constant-column skip in the clipper | `test_constant_column_skipped_with_warning` (1) |

**Report answers.**

3. `fit_imputer` on `churn_frame`'s 144-row train from `split_stratified(seed=42)`, summary verbatim:
```json
{"strategies": {"customer_id": "median", "age": "median", "plan": "most_frequent", "signup_date": "most_frequent", "satisfaction": "most_frequent", "notes_free_text": "most_frequent"}, "fills": {"customer_id": 123.5, "age": 46.5, "plan": "basic", "signup_date": "2023-03-27", "satisfaction": "medium", "notes_free_text": "Customer discussed plan options extensively during the support call, mentioning competitor pricing and overall service quality concerns 100."}, "dropped": [], "fitted_rows": 144}
```
with four warnings, one per string column, that the `median` default fell back to `most_frequent`.

4. `cast_dtypes` on `messy_frame` with `infer_column_roles`' roles, summary verbatim:
```json
{"columns": {"target": {"role": "boolean", "source": "agent", "from": "int64", "to": "boolean", "newly_null": 0}}}
```
Only `target` is listed: it is the one column whose dtype actually changes (int64 → nullable boolean).
Every numeric/datetime column is already its role's dtype (no-op), and `many_levels` arrives as object
dtype, which `cast_dtypes` treats as already-string for a `categorical` role. No warnings.

5. **Where the fallback/all-null rules felt like the tool deciding.** The `median → most_frequent`
fallback is the one that comes closest. On `churn_frame` the agent passes the default `median` and gets
four silent strategy swaps for the string columns — the summary and a warning record each, but the tool
chose `most_frequent` without being asked. It is defensible (a median of strings is meaningless and the
only sensible statistic is the mode), and it is auditable (recorded in `strategies` + a warning), but it
is a decision the agent arguably should make per column. The all-null → `drop` rule felt less like
overreach: a column with no non-null value in train genuinely *cannot* be imputed, so the tool is
reporting an impossibility, not choosing between options — and it too warns.

6. **A pandas 3.0 surprise not in P1–P7:** building a `pd.Categorical` with a `categories=` list that
does not cover a present non-null value raises a `Pandas4Warning` (→ error under `-W error`); the old
silent out-of-category → NaN coercion is deprecated. `cast_dtypes`' ordinal branch therefore maps
out-of-level values (and nulls) to `None` *before* constructing the categorical. Also recorded:
`pandas.api.types.is_string_dtype(categorical)` is `True`, so the string-fill predicate must exclude
`CategoricalDtype` explicitly or `constant` on a categorical would silently fill instead of raising (P2).
Both added to `00_index.md`'s pandas 3.0 block.

7. **For chunk 12 — unseen category level at apply.** From the fixtures: `churn_frame`'s `plan` and
`satisfaction` are small closed vocabularies (3 levels each), but D15 targets small data and
`split_stratified(seed=42)` leaves train at 144 rows — a rare level landing only in valid/test is
plausible, and a hard error would make an otherwise-legal split un-encodable. So an unseen level should
be **representable, not an error**: map it to an all-zero one-hot row (or a dedicated "unseen" column),
and log it as a warning the way `cast_dtypes` logs `newly_null`. This mirrors `apply_imputer`'s stance
that an unseen *column* is a hard error (a structural mismatch the agent controls) while a null *value*
is routine (data the agent does not control); an unseen level is a value, not a column, so it falls on
the routine side.



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
