# Archived workflow and completion history — 2026-10-03

Historical snapshot, preserved verbatim below. Not startup reading or current procedure.
Current state: [WHATS_NEXT.md](./WHATS_NEXT.md). Current validation: [AGENTS.md](../AGENTS.md#validation-policy).
Old commands, counts, exceptions, and Next headings do not override current policy.

# What's next

Live continuation state. Read this first, then relevant `00_index.md` blocks and the active spec.
Read only the `PLAN.md` sections the task needs. Validation policy lives in root `AGENTS.md`.

This file holds what is **not** written down elsewhere: live state, how the working loop actually
operates, and the lessons that cost something to learn. Architecture lives in `PLAN.md`; the chunk
map, hard rules and verified environment facts live in `00_index.md`. Do not duplicate either here.

---

## State

**22 of 41 chunks complete, plus revision batches 09r and 20r.** Phase A and Phase B complete;
M7b passed. **B6 (21 MCP client + 25 LLM client) completed and accepted 2026-10-03.** Next batch is
B7: chunk 22 engine core, then chunk 23 restart reconciliation. Implementation committed with B6.

| Accepted B6 candidate: HEAD `901e840` plus recorded source/test hashes | |
|---|---|
| Tests | 2189 passed, 1 designed skip (chunk 06 F9); 72.08 s |
| Coverage | 100% line and branch; 6030 statements, 0 missed; 1566 branches, 0 partial |
| mypy | Full strict gate clean on 109 source files; `init_typed = true` |
| Ruff | Full check and format gates clean; 110 files formatted |
| Warnings/typing | Warnings are errors; no new ignores, explicit forbidden typing tokens or coverage suppressions |
| Served tools | 83 (v1), unchanged by B6 |
| Git | Acceptance parent `901e840`; B6 source and agent workflow updates committed |

All four final gate exit codes were 0. Candidate aggregate SHA256:
`1acc77712c7d252f3afea487329d615bfccf102704066aa6315fbb66f4a77889`.
The 17 source/test/config inputs were unchanged after execution. Canonical acceptance receipt:
[chunk 21 section 8](./21_mcp_client.md#8-final-b6-acceptance-2026-10-03); full hashes and gate
commands/exits in `/private/tmp/statlab-b6-acceptance-candidate.json` and
`/private/tmp/statlab-b6-acceptance-gates.json`. Planning/status edits preserve product evidence.
At startup inspect git status/HEAD and relevant input changes under root AGENTS; do not run full
baseline gates merely to continue.

Recovery at `901e840` restored three missing, formerly untracked files from rewound `02f1739`:
MCP client, domain lineage policy and MCP client tests. Cancellation auditing and credential-safe
cleanup were reviewed. Fifteen MCP and nine LLM mutations killed intended assertions and restored
source exactly. Standalone classification ROC AUC `0.7304292929292929` and regression RMSE
`13.640760279602766` reproduce with absolute difference `0.0`. Historical pre-B6 counts are
superseded by the accepted candidate above; no paid LLM requests were used.

Milestones passed: **M1** (chunk 07) — an external MCP client completes `tools/list` and a
round-trip `call_tool` over real HTTP; a tokenless request is rejected. **M2** (chunk 08) — the
five intake tools pass contract tests, mismatches produce precise field-level errors, and a real
CSV loads to a `raw` artifact over a real MCP session. **M3** (chunks 09 + 10) — every intake,
profiling and splitting tool tested on fixtures; `verify_split` catches a deliberately
overlapping split (and, via P3, a relabelled duplicate that index overlap cannot see). **M4**
(chunks 11 + 12) — every `fit_*` learns on train and every `apply_*` replays it unchanged on a
frame with different statistics; the target encoder is out-of-fold and refuses train rows.
**M5** (chunks 14 + 15) — every `score_*` carries a seeded bootstrap CI and `rank_models` applies
the contract's constraints; the inferential track fits OLS/Logit/GLM, reproduces the NIST Longley
certified coefficients, and labels confirmatory vs exploratory with Holm correction (D35).
**M6** (chunk 16) — the 33-tool walking skeleton runs end to end over a real MCP session, zero
LLM, on a classification *and* a regression fixture, reproducing the probe's numbers to the
printed precision. Chunk 21 replaces the former test-local allow-list/lineage checks with the
production client's dispatch guards and moves `select_champion` before the one test-score replay;
the normative `SKELETON_TOOLS` set remains 33 tools.
**M7a** (chunks 17 + 18) — the node-7/8 feature tools (three row-wise constructors, the fitted
binner, two train-side selectors) and the node-11 interpretation tools (permutation importance,
coefficient importance, partial dependence rendered as a `plot_curve` kind, error analysis by
segment), each oracle-checked: permutation importance equals `sklearn.inspection.permutation_
importance` to 1e-12, standardised coefficients equal `coef × population sd` by hand (incl. the
`classes_[0]` sign flip), a noiseless linear PD slope equals the fitted coefficient to 3.5e-15,
and error analysis aligns by label so a shuffled prediction order gives an identical table.
**M7b** (chunk 20) — the champion's path is sliced out of the tool log (D-c) and
`export_pipeline_script` writes a standalone `pipeline.py` that, run in a **fresh subprocess**
(minimal env PATH+HOME, fresh empty cwd), reproduces the champion's recorded test metric for both
skeleton fixtures: classification roc_auc 0.730429 and regression rmse 13.640760, each **exactly**
(diff 0.0, well under the ±0.005 ceiling); the generated script is injection-safe (values via
`repr` over JSON types, refs as expressions, names validated against the served registry/schema,
`compile`d before writing) and `snapshot_run` writes a content run hash + per-file digests with no
environment variable ever reaching the run folder.

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

Root [AGENTS.md](../AGENTS.md#validation-policy) is the single validation authority. One cycle per
execution batch; dependency-ready stages run sequentially. Main plans/reviews/accepts; a fresh Luna
implementer edits/debugs. No product source, test, or mutation edits by the main agent.

### Step 1 — Main drafts a bounded spec

Use existing environment facts from `00_index.md`; probe only a new task-relevant assumption, not
facts already verified. Keep the house format:

```text
---
status: untouched | in_progress | completed
dependencies: [available prerequisite chunks]
completion_date: null
implementation_notes: ""       # main updates after review/acceptance
---
# Chunk NN — title
Goal: relevant PLAN sections/decisions.
1. Prerequisites & Context: recorded candidate evidence, reused symbols/paths, relevant facts.
2. Files to Create / Modify: exact ownership.
3. Implementation Spec: frozen signatures, validation, error mapping, decisions.
4. Test Suite Spec: meaningful passing/failing cases and focused selectors.
5. Definition of Done: behavior criteria, one final batch gate by main, only required probes,
   out of scope, architectural stop conditions, report fields.
6+. Addendum: review correction, affected evidence, focused commands, next-stage hazards.
```

Specs/plan-only commits require document review, not product regression. Test counts are estimates,
not quotas. Preserve architecture/security/lineage/typing/coverage requirements.

### Step 2 — Main hands off one stage

Give a fresh implementer the task/spec, exact owned files, relevant architecture excerpts, likely
reuse points, the previous stage's concise report, acceptance criteria, focused commands, and stop
conditions. Root AGENTS applies; select only needed index/convention blocks. Do not send the entire
repository or historical reports, or ask it to read all of PLAN/WHATS_NEXT before starting.

```text
Implement only <bounded task> in <owned files>. You are not alone; preserve others' edits.
Read <spec sections> and <relevant PLAN/index blocks>; reuse <symbols>.
Implement -> make test-fast TESTS='<affected modules/node IDs>' -> fix -> focused tests.
Use targeted ruff and scoped mypy when useful. No full suite/coverage/global gates.
Report files, commands, exit codes, concise results, log paths, unresolved risks, next-stage API.
Do not update plan status, commit, expand scope, or decide architectural ambiguities.
Stop at focused green handoff. Main owns final review/acceptance.
```

### Step 3 — Luna implements and reports focused evidence

No startup full tests. Broaden focused regressions only for affected dependencies. Reports include
candidate HEAD/relevant diff or hashes, command scope, genuine exit status, summary, and log path.
Keep long logs in temporary files. Intermediate stages may hand off after main reviews this focused
evidence; final batch acceptance comes after all stages are stable.

### Step 4 — Main reviews, then accepts once

Inspect the diff, actual focused evidence, behavior/invariants, and risks. Independence comes from
review and owning final acceptance, not repeating every worker command. Delegate focused corrections;
repeat only affected focused checks. Run spec-required mutations/probes once for the affected property,
with the implementer doing source mutation/restoration. Do not inherit historical probe lists.

At stable complete-batch acceptance main runs `make check`: ONE full pytest WITH coverage, full mypy,
ruff check/format. `make test` alone is the same expensive coverage gate, never an iteration command.
If final execution is explicitly delegated, main reviews its receipt and does not repeat it. Capture
real exit codes and inspect summaries/failures, not progress streams or giant coverage tables.

Record successful gates and their candidate inputs. Source/test/fixture/runtime-config changes
invalidate affected evidence; docs/status/AGENTS edits do not. Formatting-only Python changes use
ruff and semantic judgment. Reuse unchanged mutation/probe/static evidence. Detailed invalidation
and proportionality rules are in AGENTS.md; do not create a competing command checklist here.

### Step 5 — Main updates state and commits when authorized

After acceptance update chunk frontmatter, index rows, measured state, and the single Next heading.
Those document edits do not rerun product gates. Stage exact authorized files only; never push.
Keep implemented, focused-tested, reviewed, accepted, and committed distinct.

### The addendum pattern

**Four of seven chunks needed one.** When verification finds a defect, the fix goes into a new
numbered section of the same spec (`## 6. Addendum`, `## 7. Addendum`, `## 8. Addendum`), the
frontmatter goes back to `in_progress`, and dependent work waits for reviewed focused evidence. Final
acceptance remains once per stable complete batch. The addendum states what was wrong,
what evidence proved it, the fix, and its own DoD and report
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

- **Full mypy at final acceptance catches gaps a scoped development pass can miss.** Chunk 07's third bug
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

Twenty-three defects, plus one methodology review (17). Rows 14–17 came from the full review of
2026-10-01; their fixes are in `PLAN.md` D32–D36 and `REVISIONS_IMPLEMENTED.md`. Rows 18–19 are two
defects in my *own* 09r revision text and its first implementation, caught in review before 09r was
accepted. Rows 20–21 came from batch B1's review. Row 22 came from batch B2's review. Row 23 was
found by the batch B3 walking-skeleton gate itself. Defects caught in chunk 10/11 reviews
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
| 22 | Class labels and levels were stringified two ways (`str()` vs `as_str`), breaking scoring of float-coded targets, one-hot of float-coded columns, and silently replacing an unmatched contract positive_class | One scalar helper, `as_str_scalar` (agrees with `as_str` element-for-element); never a silent default — an unmatched contract positive_class is a `ToolDataError` |
| 23 | `describe_artifact` could not describe the comparison **table** the §3.3 skeleton drives it over at node 13: it supported only frame kinds (`dataframe`/`predictions`) and returned a `ToolDataError` for a `table`, pinned by a test whose own docstring called the gap temporary ("today", "does not yet support"). Found by the chunk-16 gate, not a review | `_describe_json` added in `tools/artifacts.py` (no `backend/`/`contracts/`, no D-decision): a JSON `table`/`curve` is summarised by top-level keys and list lengths. The pinned test was rewritten to assert the new behaviour, proven revert → fail → restore |
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

## Next: batch B7 (chunks 22 engine core + 23 restart reconciliation)

Write the bounded chunk-22 spec using PLAN's declared spine, checkpointer/event-bus requirements
and available B6 client/repository seams. Stage 22 implements shared-node traversal and
prune-on-success (D24); stage 23 follows its reviewed focused handoff with boot/status scan and
node-boundary restart reconciliation. B7 closes M8a. Follow index batch dependencies and root
AGENTS validation policy; one writer, focused stage handoffs, one stable final batch gate.

## Batch B6 — completed (summary)

- **Chunk 21:** production MCP discovery/cache, both allow-lists, trusted injection/schema checks,
  recursive artifact ownership/lineage guards, durable one-test-score claim, execution/call rows
  and persisted `LoggedCall` reconstruction. M6/reporting/M7b now use the client over real HTTP.
  Cancellation keeps its identity even if auditing fails; cleanup notes exclude foreign text.
- **Chunk 25:** typed LiteLLM schema/JSON/prompt tiers, prefix overrides, exactly one output repair,
  timeout 60 and no hidden retries, per-client semaphore (default 4), safe errors and per-attempt
  metadata/cached-token cost accounting. Import and real mock completion validated offline.
  Ordinary SDK chat fields are validated by the SDK; arbitrary usage extras are checked here.
- **Review/acceptance:** all 24 prescribed mutations killed intended assertions and restored;
  pipeline artifacts reviewed and both metrics reproduced exactly. Full accepted results are in
  the State table and linked shared receipt. No source gates repeated for status/doc edits.
- **Next-stage hazards:** reuse `create_node_execution` and the MCP `CallContext`/DB log APIs;
  share an LLM client to share its semaphore. Routing/keys/prompts belong to 26, recovery/backoff
  to 31. Flash costs are informational peak upper estimates; unknown pricing records zero with
  a warning. No new migrations/dependencies or concrete engine were introduced in B6.

Historical summaries below preserve old commands, counts, and receipts. They do not add binding
validation steps to the current workflow; see AGENTS.md.

## Batch B5 — completed (summary)

Two chunks, one stage each: chunk 19 (node-12/13 champion + reporting tools) and chunk 20 (node-13
reproducibility tools). Together they close **M7b** and complete Phase B. Gates after B5: **2075
passed, 1 skipped** (2076 total), **100 % line and branch** (5331 stmts, 1380 branches, 0 missed),
mypy `--strict` clean on **101 source files**, ruff `check` + `format --check` clean, suite ≈ 62 s.
B5 added **zero** `Any`, **zero** `pragma: no cover`, **zero** new production `# type: ignore` (still
only `tool.py`'s 2 `__signature__` gaps). **Not committed.**

### Chunk 20 (reproducibility) — M7b

Two tools in `tools/reproducibility.py` (`phase="reproducibility"`,
`allow_in=["artifact_persistence"]`, **stdlib only**): `export_pipeline_script` and `snapshot_run`.
`compute_run_hash` and `prune_intermediate_artifacts` are **removed** (D-a): the run hash is a field
of `snapshot_run`'s manifest, and pruning is an engine step on node success (it sets `pruned_at` in
a DB the stateless MCP server cannot reach). The catalogue is 84 served tools.

- **D-c, the champion's path is a backward slice over refs.** From the call that produced the target
  (the champion's test score table), repeatedly add the latest earlier call that produced each ref an
  included call consumes. Refs are recognised by `ARTIFACT_ID_PATTERN`, recursively through lists and
  dict values (a ref inside a `candidates` dict is followed). This drops other candidates' branches,
  node 6, profiling, `compare_models`/`rank_models` and the whole inferential branch
  (`assemble_inference_matrix`/`fit_logit`/`coefficient_table`) automatically — the preprocessing
  **transformer** fits (`fit_imputer`/`fit_onehot_encoder`/`fit_scaler`) *are* on the path and kept
  (the §4 "no fit_*" means the inferential model fits, not these). A consumed ref with no earlier
  producer, or a target no call produced, is `tool_param`.
- **D-d, the script is unrolled, readable and injection-safe.** One `_call` statement per kept call,
  commented with its original position. Tool names validated against the served registry, argument
  names against the tool's input schema (`project`/`run` dropped), values rendered with `repr` over
  JSON types only with refs replaced by `r{i}.artifacts[k].ref` expressions, the contract snapshot
  embedded as a `repr`'d JSON string, the dataset sha256 from the slice's single `load_csv`. The
  source is `compile`d before writing; no value is ever `eval`'d. No timestamp or absolute path, so
  the same log gives byte-identical bytes. The injection test parses the rendered value back with
  `ast.literal_eval` and walks the AST for any `system`/`eval`/`exec`/`__import__` call (none).
- **D-e/D-f, the manifest.** `run_hash` is the sha256 of canonical JSON (sorted keys, no whitespace)
  over the dataset sha256, the parsed contract and every call with `project`/`run` dropped and each
  ref replaced by `"@{call}.{output}"` — so two runs with the same data/contract/calls hash alike even
  though their refs differ. The manifest also holds the Python version, `platform.platform()`, the
  versions of 11 modelling packages (`null` when not installed) and a sha256+size for every run file
  except `manifest.json`. It **never** captures environment variables — the bearer token never reaches
  a run folder (a test sets `STATLAB_MCP_TOKEN` to a known value and asserts it is absent).

**M7b gate** (`tests/test_spine_reporting.py`, both fixtures): after the node-12 reporting flow,
`export_pipeline_script(calls=driver.log, target=<champion test table>)` and
`snapshot_run(calls=driver.log)` run over MCP in `artifact_persistence`; the kept tools contain
exactly one `train_estimator` and none of the comparison/ranking/inference branch; then
`subprocess.run([sys.executable, pipeline.py, csv], env={PATH, HOME}, cwd=<fresh empty tmp>,
timeout=300)` exits 0 and its last stdout line's `metrics[primary_metric].value` matches the recorded
test value within ±0.005. **Both fixtures reproduce exactly: classification roc_auc 0.730429,
regression rmse 13.640760, diff 0.0.** A one-byte-changed CSV makes the script exit non-zero with the
sha message. The subprocess with a minimal env in a fresh cwd is what proves the script does not lean
on the test process's env/cwd/imports.

**All 7 §5 DoD mutations KILLED** (each a one-string replace, `count == 1` asserted, affected tests via
`subprocess` timeout=300, originals restored in `finally`; `git diff --stat` afterward showed only
intended files):

| Mutation | Test(s) that failed |
|---|---|
| the slice keeps every call | `test_slice_drops_profiling_and_node6_calls` |
| the slice takes the first earlier producer instead of the latest | `test_slice_binds_consumer_to_the_latest_earlier_producer` |
| refs are not normalised in the run hash | `test_run_hash_same_for_logs_differing_only_in_ref_values` |
| argument values rendered with `str()` instead of `repr()` | `test_render_value_covers_every_json_shape`; `test_render_injection_string_is_a_literal_not_code` |
| the `compile()` check is removed and a broken render goes through | `test_export_rejects_a_non_compiling_render` |
| the sha256 check in the script header is removed | `test_m7b_pipeline_reproduces_classification` |
| the manifest includes `os.environ` | `test_manifest_omits_the_token_value` |

**For chunk 21 — the exact `LoggedCall` the client records per call.**
`LoggedCall(tool: str, arguments: dict[str, JsonValue], outputs: tuple[str, ...])` in
`contracts/statlab_contracts/reporting.py` (frozen, `extra="forbid"`): `tool` the registered name,
`arguments` the arguments **as sent** over the wire with the injected `project`/`run`/`seed`
included (the reproducibility tools drop `project`/`run` themselves), `outputs` the produced artifact
refs in envelope order. The skeleton `Driver` already appends one per call (`driver.log`); the only
`test_skeleton.py` change this chunk made was that append. Over the wire pass each as
`call.model_dump(mode="json")` so `outputs` is a JSON list, not a tuple.

### Chunk 19 (champion + reporting)

Node-12/13 champion + reporting tools. `select_champion` (phase `champion`), `render_metrics_table`
and `assemble_report` (phase `reporting`), all `allow_in=["champion_selection"]`. The report states
every number from artifacts, never from LLM text (D-c): the LLM contributes only the narrative
`sections` between Inference and Figures.

- **D-a** — `rank_models` ranking rows now carry `ci_low`/`ci_high` (copied from the comparison
  rows), and its table payload carries `top_eligible`, `top_two_eligible_distinguishable`,
  `selection_note`, `n_candidates` (previously summary-only). Chunk 14 tests read only `metric`
  and `ranking`, so they passed unedited.
- **D-b** — `select_champion` is a validated pointer: refuses an ineligible candidate (`tool_param`
  with its `ineligible_reason`), allows overriding `top_eligible` (records `overrides_top: true`),
  writes a `champion` table. It never sees test (phase `champion`).
- **D-c/D-e** — `assemble_report` writes `# title`, `## Result` (test line always; the
  not-distinguishable/exposure/override sentences each present iff their condition), `## Validation
  comparison`, `## Inference` (iff a coefficient table), the LLM sections verbatim, `## Figures`
  (iff plots). Numbers `.4g`, CI `v [lo, hi]`, `null` an em dash, cells escape `|` and fold
  newlines, headings single-line. Plots copied by reference to `report/plots/<ref>.png`.
- **D-d** — reading test is allowed for reporting after the one score. `tests/test_skeleton.py`'s
  `check_lineage` gained a `phase` parameter and a node-12 clause: a `phase == "reporting"` tool
  may receive a test-derived input (after the single score); `select_champion` (phase `champion`)
  never may; the driver reads `meta.phase` off `tools/list`. New guard cases test a reporting tool
  before the score (rejected) and after (accepted), and `select_champion` with test (rejected in
  every state). The skeleton G1–G7 gate is otherwise unchanged.

`ReportSection(heading 1–80, body ≤ 8000)` lives in `contracts/statlab_contracts/reporting.py`
(frozen, `extra="forbid"`, stdlib + pydantic) — the only `contracts/` change. `evaluation`'s score
table loader was made public (`ScoreTable`/`load_score_table`, no behaviour change) and reused by
`render_metrics_table` rather than re-parsing. `tests/test_spine_reporting.py` reuses the skeleton's
`_open_driver`/`_run_spine` (SpineResult gained `ranking_ref`, `models`, `valid_frame`) and drives
the reporting flow end to end over MCP on both fixtures.

Gates: **2046 passed, 1 skipped**, suite ≈ 53 s; **100 % line and branch** (reporting, champion,
evaluation and `contracts/reporting.py` all 100 %, TOTAL 0 missed); mypy `--strict` clean on 99
files; ruff `check` + `format --check` clean. Zero `Any`, zero `pragma: no cover`, zero new
production `# type: ignore` (one test `# type: ignore[arg-type]` on a `get_args` comparison; the
`α` column header carries a `# noqa: RUF001`). All 7 §5 DoD mutations KILLED; every `check_lineage`
clause including the new reporting one killed by deletion. No `backend/` touched.

## Batch B4 — completed (summary)

Two chunks, one review cycle: chunk 17 (node-8 feature tools + the cross-cutting `transform_only`
flag) and chunk 18 (node-11 interpretation tools). Together they close **M7a**. Gates after B4:
**2000 passed, 1 skipped** (2001 total), **100 % line and branch** (4924 stmts, 1246 branches, 0
missed), mypy `--strict` clean on **95 source files**, ruff `check` + `format --check` clean, suite
≈ 48 s. B4 added **zero** `Any`, **zero** `pragma: no cover`, **zero** new production `# type:
ignore` (still only `tool.py`'s 2 `__signature__` gaps).

### Chunk 17 (features)

Node-8 feature tools plus the cross-cutting `transform_only` flag (D-a). Seven tools in
`tools/features.py` (`phase="features"`): three stateless row-wise constructors
(`add_datetime_parts` replaces its source; `add_interaction_terms`/`add_ratio_feature` add
columns), the fitted `fit_binner`/`apply_binner` pair (binned column replaces its source as a
`b{i}` string, picked up by `fit_onehot_encoder`), and two train-side selectors
(`select_by_variance`, `select_by_mutual_info`) that write a reduced train frame plus
`dropped_columns` for `drop_columns` to replay.

`@tool` gained `transform_only: bool = False`, served in `meta`. It is `True` on exactly 13 tools:
the 7 `apply_*`, `drop_columns`, `predict`, `predict_proba`, and the 3 constructors. A consistency
test over the real `tools/list` pins the set and its three properties (not `fit_*`, no `train`
parameter, lists `champion_selection`). `tests/test_skeleton.py`'s `check_lineage` now reads the
flag off `tools/list` instead of matching `apply_*`/`predict` name prefixes — which already missed
`drop_columns` and the constructors; the skeleton G1–G7 and its gate are unchanged, and all five
lineage clauses were re-verified by deletion. All 7 chunk-17 DoD mutations killed. Hazards P1–P8 all
handled (NaT mask, edge dedup+warn, ±inf outer edges, zero-denominator → NaN, `to_numpy(float64)`,
NaN refused / bool discrete / sklearn lazy / `n_jobs=1`, `ddof=0`).

### Chunk 18 (interpretation)

Four node-11 tools in `tools/interpretation.py` (`phase="interpretation"`,
`allow_in=["model_interpretation"]`): `permutation_importance` and `partial_dependence` (both
`heavy=True`), `coefficient_importance`, `analyze_errors`. The model knows its own `target` and
`features` (D-a), so the tools take a frame with no separate target parameter.

**Reuse, not duplication (§1).** The evaluation/training helpers this chunk calls were made public
by dropping the leading underscore, with no behaviour change: `predict_matrix`/`check_metric_task`
in `training.py`; `proba_columns`/`class_labels`/`check_truth_classes_have_columns`/
`load_truth_target`/`snapshot`/`hard_labels`/`new_figure`/`save_curve` in `evaluation.py`. **No
chunk 13/14/17 test was edited.** Two helpers stayed **private** — `_align` and `_positive_class` —
because `test_tools_evaluation.py` monkeypatches `ev._align` / `ev._positive_class` *by name* in two
mutation tests, so renaming them would silently defeat those tests; the spec's "no test references
them today" was inaccurate for exactly these two, and `interpretation.py` imports the private names
(a legal cross-module import that keeps the existing tests green). `plot_curve` gained the
`partial_dependence` kind (one line per series over `x_original` when a scaler mapped the grid back,
else `x` with a "(scaled)" axis hint).

**The numbers are oracle-checked, not eyeballed.** The tool `permutation_importance` shadows
sklearn's function, so sklearn's is aliased inside the body; a test calls sklearn's directly as the
oracle and asserts equality to **1e-12**. `std_coef` equals `coef × population sd (ddof=0)` computed
by hand to **1e-9**, including the P4 sign flip: binary `LogisticRegression.coef_` refers to
`classes_[1]`, so when the contract's positive class is `classes_[0]` every coefficient is negated
(a dedicated test flips the snapshot's `positive_class` to `classes_[0]` and asserts each sign
inverts). A **noiseless** linear-regression PD has the known answer — consecutive PD slopes equal the
fitted coefficient (3.0) to **3.5e-15**. `analyze_errors` aligns predictions to truth **by label**
(the shared `_align`), so a shuffled prediction order yields a byte-identical table; a planted
all-wrong segment ranks first in `worst`, and a 5-row segment is `small` and excluded.

**Mutation evidence** (all 7 §5 mutations KILLED; each replaced via a harness that asserted ≥1
failing test and restored `interpretation.py` byte-for-byte in a `finally`, confirmed by
`git diff --stat` afterward):

| Mutation | Test that failed |
|---|---|
| `random_state` not passed to permutation importance | `test_permutation_different_seed_differs` |
| `std_coef` without the sd factor | `test_coef_std_coef_equals_coef_times_sd` |
| no sign flip for a `classes_[0]` positive class | `test_coef_sign_flips_for_classes0_positive` |
| PD overwrites the column on the first row only | `test_pd_linear_regression_known_answer` |
| `x_original` computed as `(x − center) / scale` | `test_pd_scaler_maps_x_original` |
| `analyze_errors` aligns by position | `test_errors_shuffled_prediction_order_identical_table` |
| `worst` includes `small` segments | `test_errors_small_segment_excluded_from_worst` |

**Deliberately not added** (DO NOT ADD list): grouped permutation (left as the `ponytail:` comment
D-b asks for — a `groups` parameter fed from the encoder's levels is the upgrade), ICE curves, 2-D
PDP, SHAP, a separate `plot_partial_dependence` tool (PDP renders through `plot_curve`'s new kind,
D-d). Nothing in `backend/` or `contracts/` changed.

**For chunk 19 — what `assemble_report` needs that these summaries do not carry.** (1)
`permutation_importance`'s summary carries only the **top 10** rows (`top`) plus `baseline`; the full
per-feature table is in the `table` artifact's `features` list, so a report wanting every feature
reads the artifact, not the summary. (2) `coefficient_importance`'s summary likewise carries only the
top 10 by `|std_coef|` and the intercept(s); the full per-(class,)feature rows are in the artifact.
(3) `partial_dependence` puts **all** the curve data (`x`, `x_original`, per-series `y`) only in the
`curve` artifact — the summary carries `column`, `task`, `grid_points` and the series names, so a
report rendering the curve reads the artifact. (4) `analyze_errors`'s summary carries `overall_error`,
`n_segments` and the top-5 non-`small` `worst` rows; every `(column, segment)` row (and each
segment's `n`/`bias`/`small`) is only in the `table` artifact. In short: each tool's summary is a
headline; `assemble_report` must open the `table`/`curve` artifact for the full rows, exactly as the
chunk-16 gate reads `coefficient_table`'s rows off disk.



One chunk, one review cycle: chunk 16, the walking-skeleton gate. **M6 passed.** The 33 `[v1]`
tools (PLAN.md §6.4) are driven end to end over a real MCP session, zero LLM, on a classification
fixture (`subscribers`, 400 rows) and a regression fixture (`houses`, 300 rows), three estimators
each. The gate is one test module (`tests/test_skeleton.py`) that plays the orchestrator and
enforces chunk 21's two rules — the allow-list and the D32 lineage lock — on itself before every
call, exactly where chunk 21 will. No production module was added for the gate itself.

Gates after B3: **1913 passed, 1 skipped** (1914 total); **100 % line and branch** (4454 stmts,
1128 branches, 0 missed); mypy `--strict` clean on **91 source files**; ruff `check` +
`format --check` clean; suite ≈ 44 s; `test_skeleton.py` ≈ 8 s (classification 4.29 s, regression
1.06 s). B3 added **zero** `Any`, **zero** `pragma: no cover`, **zero** new `# type: ignore`.

**The wire run reproduces the §1 probe numbers to the printed precision.** Classification: valid
`roc_auc` logistic 0.8125 [0.720, 0.888], RF 0.722, HGB 0.716; `top_two_distinguishable` false;
champion logistic, test `roc_auc` 0.730; inference `n_obs` 320 (train 240 + valid 80); planted
`tenure_months` −0.0547, `plan__premium` −1.96, `plan__standard` −0.705, each inside §1's CI; the
`plan` goal is a joint Wald test df 2, p ≈ 0, rejected; the `region` goal df 3, p 0.510, not
rejected. Regression: valid `rmse` linear 15.42 [13.01, 17.88], HGB 17.08, RF 17.52; champion
linear, test `rmse` 13.64 (`r2` 0.949); `n_obs` 240 (train 180 + valid 60); planted
`neighbourhood__centre` +37.7 ∈ [33.32, 42.09], `neighbourhood__rural` −25.9, `rooms` +8.24, each
inside §1's CI; `neighbourhood` goal df 2 p ≈ 0 rejected, `condition` goal df 2 p 0.975 not
rejected. `area_m2`'s CI [1.120, 1.200] is on the edge of its true 1.2, so §1 does not assert it
and neither does the gate.

**All six §5 DoD mutations were caught** (each replaced exactly one string, the affected
`test_skeleton.py` tests run, originals restored; `git diff --stat` afterwards showed only the
intended files): apply the imputer to test in `preprocessing` → the lineage guard fires; score the
champion on test twice → the exactly-once check fires; call `fit_scaler` in `preprocessing` → the
allow-list fires; `predict_proba` reversed class order → the real-metrics floor (valid roc_auc ≥
0.65) fails; `assemble_inference_matrix` ignores `valid` → `n_obs` ≠ train + valid; `compare_models`
sorts `roc_auc` ascending → the ranking-direction assertion fails.

**The gate found exactly one defect, fixed at root cause.** §3.3's `artifact_persistence` node
calls `describe_artifact` on the comparison *table*, but `describe_artifact` only supported frame
kinds (`dataframe`/`predictions`) and returned a `ToolDataError` for a `table`, pinned by
`test_describe_artifact_table_kind_raises_not_yet_supported`. Its docstring and that test both
framed the limitation as temporary ("today", "does not yet support"), so this is a defect the gate
found rather than a design decision: it was fixed in `mcp_server/statlab_tools/tools/artifacts.py`
(no `backend/`/`contracts/` change, no D-decision) by adding a minimal `_describe_json` that
summarises a JSON `table`/`curve` by its top-level keys and the length of any list-valued entry.
The pinned regression test was rewritten to assert the new behaviour and proven by revert → fail →
restore (the three new JSON-describe tests fail on the pre-fix production code). No `allow_in` was
widened, no `check_lineage` relaxed, no tool body called directly, and no spine step skipped — the
single conflict surfaced and was fixed where it lived, which is the gate working as designed. See
the corrections table, row 23.

**For chunk 21 — argument shapes that were awkward over the wire.** (1) `coefficient_table`'s term
coefficients and CIs are only in the `table` artifact's JSON payload, not in the tool's `summary`
(which carries the goal rows and counts), so the gate reads them off disk; the client will need the
same read, or `describe_artifact` could be extended to surface a table's rows. (2) `list_estimators`
is the one spine tool with `needs_run=False`, so it has no `project`/`run`/`seed` in its schema —
the driver keys injection strictly on each tool's served `inputSchema` properties, never on a
hardcoded list, which is the shape chunk 21 must copy. (3) Split outputs must be told apart by
`ArtifactMeta.split` and the inference matrix/manifest by `label`, never by artifact position; the
wire preserves both. (4) `goals` crosses the wire as a list of `InferenceGoal` objects (dicts), and
`candidates`/`estimators` as `{label: ...}` dicts — the lineage walk recurses into dict values so a
test-derived ref hidden in such a dict is still caught.
## Batch B2 — completed (summary)

Two chunks, one review cycle: chunk 14 (evaluation + champion) and chunk 15 (inference). Together
they close **M5**. Gates after B2 (incl. review corrections): **1897 passed, 1 skipped** (1898 total), **100 % line and
branch** (4444 stmts, 1122 branches, 0 missed), mypy `--strict` clean on **90 source files**, ruff
`check` + `format --check` clean, suite ≈ 44 s. B2 added **zero** `# type: ignore` (still only
`tool.py`'s 2 `__signature__` gaps in production; 23 in tests), **zero** `Any`, **zero**
`pragma: no cover`.

### Chunk 15 (inference) — the D7 second track

Eleven tools in `tools/inference.py`: the two matrix builders (`build_dummy_matrix`,
`assemble_inference_matrix`) and the nine statsmodels tools (`fit_ols`, `fit_logit`, `fit_glm`,
`coefficient_table`, `inferential_fit_summary`, `compute_vif`, `test_residuals`,
`compute_influence`, `plot_residuals`). `registry.SPECS` names the four inferential models (ols,
logit, glm_poisson, glm_gamma); `training.list_estimators` now also reports `inferential_models`.
Only `registry.py` and `training.list_estimators` were touched among chunk 14's files, plus one
import line in `tools/__init__.py`. Nothing under `backend/`/`contracts/` changed.

**The NIST known-answer is independent.** `fit_ols` on statsmodels' Longley data is compared to the
**certified** NIST StRD coefficients, SEs and R² hard-coded in the test (never statsmodels' own
output); the max relative error is **1.03e-11** on the coefficients, well under the 1e-8 ceiling.
Logit on a hand-built 2×2 reproduces `log(1/3)` and `log 9` to <1e-9 with no reference data; a
Poisson GLM recovers `exp(0.5 + 0.3x)` within 3 SE.

**Three hazards held exactly as the spec named them.** (P3) Perfect separation does not crash in
production — statsmodels' own import-time `always` filter emits the warning and returns
`converged=False` — but pytest's `filterwarnings=error` raises it. `fit_logit` handles **both**:
it catches `PerfectSeparationWarning`/`ConvergenceWarning` raised as an exception **and** checks
`mle_retvals["converged"]`, each mapping to the same `ToolDataError`. Both paths are tested (the
warning path under pytest; the `converged=False` path by monkeypatching `sm.Logit` to a
non-converged result). No `warnings.catch_warnings` anywhere (process-global; bodies run in
threads). (P6) No `statsmodels.formula`/patsy anywhere — fits take `(matrix, target, predictors)`
and add the constant themselves. statsmodels is untyped: results live behind an `object` field and
are narrowed at the boundary — zero new `# type: ignore`, zero `Any`.

**One deviation, recorded.** Gamma's canonical InversePower link raises a `DomainWarning` fatal
under `-W error`, and `warnings.catch_warnings` is banned (threads). `fit_glm` therefore uses the
domain-safe **log link** for both GLM families (Poisson's canonical link is log anyway); the
coefficients stay multiplicative in original units (D31). Marked with a `ponytail:` note.

**A real bug found while testing, fixed.** Dummy-level and binary-target derivation first used
`str(observed_counts(series).index)`, which gives `"1.0"` for an integral float column, while the
dummy columns and `y_bin` compared against `as_str(series)` which gives `"1"` — so every binary
0/1 target silently became all-zeros and every Logit/GLM "failed to converge". Fixed with a shared
`_str_level_counts` that routes level derivation through `_frame.as_str`, the same chunk 11/12
integer-string lesson. Caught because the known-answer and end-to-end tests exercise a real fit,
not a mock.

**Mutation evidence** (each applied to `inference.py`, the affected test file run, restored;
`inference.py` `diff`-clean against a backup afterwards):

| Mutation | Tests that failed (count) |
|---|---|
| the reference level is not dropped | `..._default_reference_is_most_frequent`, `..._explicit_reference_is_respected`, `..._reference_column_is_absent_mutation_guard` (3) |
| per-term p-values instead of a joint Wald test | `..._three_level_goal_is_one_joint_test_df_two`, `..._per_term_p_mutation_guard`, `..._end_to_end_churn_plan_to_churned` (3) |
| Holm replaced by raw p for `reject` | `..._holm_to_raw_mutation_guard` (1) |
| remove the `converged` check | `..._non_convergence_production_path`, `..._remove_converged_check_mutation_guard` (2) |
| assemble imputes from train only | `..._median_mode_imputation_uses_concatenation`, `..._imputes_from_train_only_mutation_guard` (2) |
| the collinearity drop is removed | `..._collinear_duplicate_dropped_with_warning`, `..._collinearity_drop_removed_mutation_guard` (2) |
| exploratory terms labelled confirmatory | `..._roles_confirmatory_exploratory_intercept`, `..._exploratory_terms_carry_p_note`, `..._exploratory_labelled_confirmatory_mutation_guard`, `..._end_to_end_churn_plan_to_churned` (4) |

**VIF on Longley** flags `GNPDEFL`, `GNP`, `UNEMP`, `POP`, `YEAR` (all > 10); the spec's named
`GNP`, `POP`, `YEAR` are a subset, so the test asserts that subset is flagged. `const` is excluded
from the output. The end-to-end `plan → churned` run gives one confirmatory joint Wald test with
`df == 2` (two plan dummies) and `n_confirmatory == 2`.

### Chunk 14 (evaluation + champion)

Eleven evaluation tools in `evaluation.py` (`score_classification`, `score_regression`,
`compute_confusion_matrix`, `compute_roc_curve`, `compute_pr_curve`, `compute_calibration_curve`,
`select_threshold`, `plot_curve`, `cross_validate_estimator`, `compute_learning_curve`,
`compare_models`) plus `rank_models` in `champion.py` (its catalogue phase; chunk 19 adds
`select_champion` and reports). This is where D33 became code: every `score_*` carries a seeded
bootstrap 95 % CI, `compare_models` reports `top_two_distinguishable` on CI overlap (D-f), and
`rank_models` labels the top validation score a selection score over N candidates (D33a).

**Reuse, not duplication (§1).** Chunk 13's matrix builder, CV splitter and scorer mapping were
made public in `training.py` (`build_matrix`, `cv_splitter`, `feature_columns`,
`check_features_usable`, `check_no_missing`, `check_task`, `check_class_counts`,
`tuning_from_snapshot`); `scorer_for`/`greater_is_better`/`is_negated`/`is_binary_only` were
already public in `registry.py`. `EstimatorEntry` gained `interpretable` (true for
`logistic_regression` and `linear_regression` only), reported by `list_estimators`. **No chunk 13
test was edited** (46 training tests green).

**Gates:** 1805 passed, 1 skipped (1806 total); **100 % line and branch** (3939 stmts, 990
branches, 0 missed); mypy `--strict` clean on **87 source files**; ruff `check` + `format --check`
clean; suite ≈ 42 s. `evaluation.py`/`champion.py` add **no** `# type: ignore`, **no** `Any`,
**no** `pragma: no cover`.

**The metrics are tested, not right-by-construction.** Every point estimate is asserted equal to
sklearn's own function called directly in the test as the oracle, binary and multiclass. The
stratified-bootstrap test (5 positives / 43 negatives) scores every metric with no warning under
`-W error`, and **fails under a plain bootstrap** (P2: a plain resample drops the rare class and
AUC raises). The alignment test shuffles the prediction order and asserts an identical score, so
an implementation that aligned by position would differ. The positive-class test flips binary
precision via the contract's `positive_class`, so an always-last-column implementation disagrees.

**Mutation evidence** (each applied to source, the affected test file run, restored; both source
files `diff`-clean against a backup afterwards):

| Mutation | Tests that failed (count) |
|---|---|
| plain bootstrap instead of stratified | `..._stratified_bootstrap_scores_rare_class...`, `..._real_1000_resamples...` (2) |
| point estimate = bootstrap mean | the three `..._point_estimates_equal_sklearn`, `..._point_estimate_is_full_data...`, `..._shuffled_prediction_order...` (5) |
| truth not reordered to predictions' order | `..._shuffled_prediction_order...`, `..._mutation_truth_not_reordered...` (2) |
| remove the fingerprint check | `..._compare_mismatched_fingerprints...`, `..._mutation_remove_fingerprint_check...` (2) |
| CI overlap vs the third candidate not the second | 4 compare tests (IndexError with 2 candidates) + `..._mutation_ci_overlap_against_third...` (5) |
| `rank_models` ignores `interpretability_required` | `..._interpretability_required_makes_forest_ineligible`, `..._mutation_ignore_interpretability...`, `..._no_eligible_candidate...` (3) |
| positive class always the last column | `..._contract_positive_class_flips_precision`, `..._mutation_always_last_column...` (2) |

**Hazards that held exactly as the spec named them.** P1: real 1000 resamples on 48 rows (9
classification metrics) is **3.4 s** — one test runs it, every other bootstrap test uses 50
resamples via an autouse fixture (seed 1 is one where a plain bootstrap still drops the rare class
at 50, so the mutation test stays valid). P4: `roc_curve`'s first threshold is `inf`; it is stored
as `null`, and every curve/table artifact survives `json.dumps(..., allow_nan=False)`. Under
`-W error`: `zero_division=0` on precision/recall/f1, `n_jobs=1` in every sklearn call (a
`cross_val_score` spy asserts it), sklearn imported lazily inside tool bodies (package import stays
scipy-free). sklearn's dynamic values narrowed at the boundary (`np.asarray(..., dtype=float)`,
`float(...)`) — no `Any`, no new suppression.

**End-to-end `rank_models` summary** (B1 pipeline → `predict_proba` → `score_classification` ×3 →
`compare_models` → `rank_models` on `churn_frame`, near-random by construction):
```json
{"ranking": [{"rank": 1, "candidate": "random_forest_classifier", "estimator": "random_forest_classifier", "value": 0.5464646464646465, "eligible": true, "ineligible_reason": null, "meets_success_threshold": null}, {"rank": 2, "candidate": "hist_gb_classifier", "estimator": "hist_gb_classifier", "value": 0.4686868686868687, "eligible": true, "ineligible_reason": null, "meets_success_threshold": null}, {"rank": 3, "candidate": "logistic_regression", "estimator": "logistic_regression", "value": 0.4080808080808081, "eligible": true, "ineligible_reason": null, "meets_success_threshold": null}], "top_eligible": "random_forest_classifier", "top_two_eligible_distinguishable": false, "selection_note": "the top validation score is a selection score over 3 candidates; only the test score estimates performance", "n_candidates": 3}
```

**For chunk 16's skeleton path:** the evaluation side is complete — `score_classification`,
`compare_models` and `rank_models` run end to end and the gate is self-contained. The one thing the
skeleton still needs that this chunk does not provide is chunk 15's inference tools (the skeleton
fits an inferential model, which is why 15 precedes 16). No evaluation tool is missing for the
skeleton's classification/regression scoring path.


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
