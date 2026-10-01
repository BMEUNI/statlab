# Revisions to implemented chunks (01–09)

From the full review of 2026-10-01 (`PLAN.md` v1.8). Each item was a change to code that was already
committed. Plan-only fixes were applied directly to `PLAN.md` and `00_index.md` and are listed at the
end for traceability.

R1–R4 were applied as one batch before chunk 10. R1–R3 were wrong results in shipped tools. R4 was the
schema and wire foundation that chunks 10 (splitting) and 21 (`mcp_client`) build the D32 test-set
lock on, so writing them first meant no later migration. R5–R7 were small and rode along. All seven
were applied on 2026-10-01 and verified by the reviewer.

Same conventions as every chunk: tests that trigger the failure each fix prevents, 100 % line and
branch coverage, no new suppressions. `001_initial.sql` is edited **in place** for the same reason as
chunks 05 and 06: nothing is deployed, so a corrective migration would be archaeology.

| # | Severity | Chunk | What | Evidence | Status |
|---|---|---|---|---|---|
| R1 | high | 08 | `validate_contract` reports false mismatches on numeric targets that load as float | reproduced | applied 2026-10-01 |
| R2 | high | 08 | `detect_leaky_columns` never flags a continuous numeric leak against a class target | reproduced | applied 2026-10-01 |
| R3 | medium | 08 | rank-correlation leak rule skipped for numeric targets with ≤ 20 values | reproduced | applied 2026-10-01 |
| R4 | high | 02, 05, 06 | schema and wire fields for the D32 lineage lock, cross-run exposure and `superseded` | design gap, verified by reading | applied 2026-10-01 |
| R5 | low | 02 | `envelope.py` comment says "auto mode" | grep | applied 2026-10-01 |
| R6 | low | 09 | `detect_outliers` `share` uses a different denominator from every other `share` | reproduced | applied 2026-10-01 |
| R7 | low | 07 | decorator docstring does not state that a running body cannot be cancelled | reading | applied 2026-10-01 |

---

## R1 — `validate_contract`: compare target values numerically when the target is numeric

**Where.** `mcp_server/statlab_tools/tools/intake.py`, `_check_target`, C7 (line ~437) and C8
(line ~446).

**Problem.** C7 and C8 compare `str(declared)` against `{str(v) for v in observed}`. That works for
an `int64` target (`"1" == "1"`). But a binary 0/1 column with **one missing value** loads as
`float64`, because pandas has no nullable integer in `read_csv` by default. Then the observed set is
`{"1.0", "0.0"}`, and `positive_class=1` stringifies to `"1"`. Valid data gets a mismatch, and node 1's
gate shows the user an error that isn't there.

**Reproduced:**

```
int64 target   (no nulls)  positive_class=1  -> valid=True
float64 target (one null)  positive_class=1  -> valid=False  "positive_class 1 does not occur in the target"
```

C8 has the same defect for a numeric multiclass target with nulls and `class_order=["0","1","2"]`
(`class_order` is typed `tuple[str, ...]`, so the declared side is always strings).

**Change.** One helper used by both checks:

```python
def _declared_values_match(declared: Iterable[JsonValue], observed: pd.Series) -> ...:
    """Compare as floats when the target is numeric (is_numeric_column), as strings otherwise.
    A declared value that does not parse as a number against a numeric target is itself a
    mismatch, not an exception."""
```

**Tests.**
- int64 and float64 (with a null) binary targets, both with `positive_class=1`: no mismatch.
- A numeric multiclass target with a null and `class_order=["0","1","2"]`: no mismatch.
- A string target with `positive_class="yes"`: no mismatch.
- `positive_class=2` against a 0/1 target: one mismatch.
- `positive_class="abc"` against a numeric target: a mismatch, not a crash.

**Outcome.** Implemented in `intake.py` as `_declared_matches_observed(declared, observed)` plus
`_as_float_repr(value)`; `_check_target`'s C7 and C8 branches now call the helper instead of
stringifying inline. When the target is numeric (`is_numeric_column`, bool excluded), both sides are
rendered as `repr(float(...))`; a declared string that does not parse as a number becomes a sentinel
that cannot equal any observed float, so it falls out as a mismatch rather than raising. Tests in
`tests/test_tools_intake_validate.py`: `test_r1_positive_class_int_against_int64_target_no_mismatch`,
`test_r1_positive_class_int_against_float64_target_with_null_no_mismatch`,
`test_r1_class_order_against_numeric_multiclass_with_null_no_mismatch`,
`test_r1_positive_class_string_against_string_target_no_mismatch`,
`test_r1_positive_class_absent_from_numeric_target_is_a_mismatch`,
`test_r1_unparseable_declared_value_against_numeric_target_is_a_mismatch_not_a_crash`. Reviewer-measured:
float64 0/1 target with one null and `positive_class=1` → valid; `positive_class=2` or `'abc'` →
mismatch; numeric multiclass with a null and `class_order=['0','1','2']` → valid.

---

## R2 — `detect_leaky_columns` L3: continuous features are skipped before they are binned

**Where.** `intake.py`, the L3 branch (line ~725) and `_level_purity` (line ~617).

**Problem.** Two defects. The first is mine: chunk 08's spec §3.6 said "L3 also skips features with
unique share > 0.5". The code applies that to the *raw* feature, before `_level_purity` bins numeric
features into deciles. Every continuous numeric feature has a unique share near 1.0, so **L3 skips
all of them**. Against a binary or multiclass target, the only rule that could catch a continuous leak
never runs.

**Reproduced** with 400 rows, a balanced binary target, and `leak = target * 10 + N(0, 0.01)`, a
perfect leak: `detect_leaky_columns` returned **no suspects**. The tests passed because they plant
leaks only in categorical features.

The second defect: `_level_purity` divides by `len(frame)`, but `groupby(observed=True)` drops rows
whose binned level is NaN (feature missing). With 20 % missing, a perfect leak scores ≤ 0.80 even once
it is reached. The same probe measured 0.7625.

**Change.**
- Apply `LEAK_UNIQUE_SHARE_SKIP` only when the feature is **not** `is_numeric_column`. Numeric
  features are binned first, so their raw uniqueness is irrelevant. The skip exists for string IDs.
- **Amended after the first implementation (review of 09r):** for a *numeric* feature against a
  class target, replace binned purity with a **rank-based one-vs-rest AUC**. For each class with at
  least `LEAK_MIN_CLASS_ROWS: Final = 10` rows, compute
  `auc = (R1 − n1(n1+1)/2) / (n1·n0)` from average ranks (`Series.rank()`; no scipy), take
  `max(auc, 1 − auc)`, and score the feature by the maximum over classes. Flag at
  `LEAK_AUC: Final = 0.99`. Binned purity stays for *non-numeric* features. Why: binned purity of a
  perfect leak reaches 1.0 only when the class boundary falls exactly on a decile edge. Measured on
  400 rows: 1.000 at 200/400 positives, 0.975 at 210, 0.9525 at 221, 0.950 at 180, 60 and 20. So the
  first fix passed its own test (an exact 200/200 split) and still missed the case it was for. The
  rank AUC of the same perfect leak is 1.000 at every balance, against 0.555 for a random feature and
  0.514 for a tied three-value feature. AUC is insensitive to class balance, so the majority-share
  guard is not needed on this path; `LEAK_MIN_CLASS_ROWS` keeps tiny classes from producing noisy
  extremes. Rule name in the summary: `rank_auc`.
- In `_level_purity`, compute over rows with a non-null level and a non-null target, and return
  `None` when fewer than `LEAK_MIN_ROWS: Final = 20` such rows remain. A new constant, not
  `MIN_ROWS_FOR_ID`: same value, different meaning, and they must be free to diverge.

**Tests.**
- The perfect continuous leak above is flagged with rule `rank_auc`, **parametrised over 200, 210,
  221, 60 and 20 positives out of 400**. An exactly balanced split alone is not evidence.
- The same leak with 20 % missing is flagged.
- A perfect leak against a three-class target is flagged.
- A class with fewer than `LEAK_MIN_CLASS_ROWS` rows is not scored.
- An unrelated continuous feature on the same balanced target is not flagged.
- A unique string ID column is still skipped.
- The existing imbalanced-target guard test and `churn_frame`-has-no-suspects test still pass,
  unchanged.

**Outcome.** Implemented in `intake.py`: a new `_rank_auc(feature, target)` (folded
`max(auc, 1 − auc)` from `Series.rank()`, maxed over classes with ≥ `LEAK_MIN_CLASS_ROWS = 10` rows,
flagged at `LEAK_AUC = 0.99`, rule name `rank_auc`); `_level_purity` is now **non-numeric only** —
its `pd.qcut` branch and `_QCUT_BINS` were deleted, it computes over paired non-null rows and returns
`None` below `LEAK_MIN_ROWS = 20`. `detect_leaky_columns` splits L3 into L3a (numeric feature, class
target → `_rank_auc`) and L3b (non-numeric → `_level_purity`), and `LEAK_UNIQUE_SHARE_SKIP` now gates
non-numeric features only. The first fix (skip the unique-share rule for numeric features + purity
over paired non-null rows) was found **incomplete** in review: binned purity of a perfect leak
(`target*10 + N(0, 0.01)`, 400 rows) only reaches 1.000 at an exact 200/200 split and misses most real
leaks, so its own test passed only because it used an exactly balanced `np.tile` target.

| positives / 400 | binned purity | rank AUC |
|---|---|---|
| 200 | 1.000 | 1.000 |
| 210 | 0.975 | 1.000 |
| 221 | 0.9525 | 1.000 |
| 180 | 0.950 | 1.000 |
| 60 | 0.950 | 1.000 |
| 20 | 0.950 | 1.000 |
| 10 | — | 1.000 |

Reviewer's probe: the perfect leak is flagged at 221, 210, 60, 20 and 10 positives of 400, and with
20 % nulls; a random feature vs a 10/400 minority class gives 0 false positives in 200 seeds;
`churn_frame` has no suspects. Mutation check: setting `LEAK_AUC` to 1.01 fails 9 tests. Tests in
`tests/test_tools_intake_detect.py`:
`test_r2_perfect_continuous_leak_is_flagged_as_rank_auc_at_every_balance` (parametrised over 200, 210,
221, 60, 20 positives), `test_r2_perfect_leak_with_20pct_missing_still_flagged`,
`test_r2_perfect_leak_against_balanced_three_class_target_is_flagged`,
`test_r2_class_below_min_class_rows_is_not_scored`, `test_r2_random_continuous_feature_is_not_flagged`,
`test_r2_tied_three_value_feature_mixed_within_values_is_not_flagged`, `test_r2_leak_constants_are_named`,
`test_r2_level_purity_returns_none_below_min_rows`.

---

## R3 — `detect_leaky_columns` L2: run whenever feature and target are both numeric

**Where.** `intake.py` line ~703, `not is_class_target and ...`.

**Problem.** L2 (rank correlation) runs only for targets with more than `CLASS_TARGET_MAX_LEVELS` (20)
distinct values. A regression target with 15 distinct values (a Likert sum, a small count) is treated
as a class target, so L2 doesn't run. After R2, L3 does run, but ten decile bins can't reach 0.99
purity against 15 levels. **Reproduced:** a 15-level numeric target with `dup = 2·y + N(0, 0.001)`
yields no suspects.

**Change.** Drop the `not is_class_target` condition from L2, and use `is_numeric_column` (which
excludes bool) for both sides instead of `is_numeric_dtype`.

This can't produce false positives on few-level targets. Measured: the largest possible rank
correlation between a feature and a k-level target (a perfectly monotone feature, equal classes) is
0.866 at k=2 (0.378 at 5 % positives), 0.943 at k=3, 0.980 at k=5, and 0.986 at k=6. So L2 can't fire
below 6 levels, where L3's purity rule covers the leak. From 6 levels up, a score ≥ 0.98 really is a
near-monotone copy of the target.

**Tests.** The 15-level case is flagged by L2. A continuous feature that perfectly separates a binary
target is caught by L3 and not by L2. A bool feature is not passed to L2. `churn_frame` still has no
suspects.

**Outcome.** Implemented in `intake.py`: L2's guard changed from
`not is_class_target and pd.api.types.is_numeric_dtype(...)` to `is_numeric_column(series) and
is_numeric_column(target_series)`, so L2 (rank correlation) runs whenever both sides are numeric
(bool excluded), and a constant-column skip (`nunique() > 1` on both sides) was added because L2 now
also sees class targets. Measured bound: the max rank correlation of a perfectly monotone feature with
a k-level target is 0.866 (k=2), 0.943 (k=3), 0.980 (k=5), 0.986 (k=6), so L2 cannot fire below 6
levels — below that L3 covers the leak. Reviewer-measured: a 15-level target with a near-duplicate
feature is flagged `rank_correlation`. Tests in `tests/test_tools_intake_detect.py`:
`test_r3_fifteen_level_numeric_target_near_duplicate_is_flagged_by_l2`,
`test_r3_perfect_separating_continuous_feature_on_binary_is_l3_not_l2`,
`test_r3_bool_feature_is_not_passed_to_l2`.

---

## R4 — Schema and wire fields for the test-set lineage lock (D32, D36b)

**Where.** `contracts/statlab_contracts/vocabulary.py`, `artifacts.py`;
`backend/statlab/persistence/migrations/001_initial.sql`, `models.py`, `repository.py`;
`backend/statlab/storage.py`.

**Problem.** D32 needs three facts the schema can't store, and D36b names a status that doesn't exist:

1. **Which artifact is the test split.** Today the split frames would be three anonymous `a_…`
   dataframes, and the only way to tell them apart is `ArtifactMeta.label`, a free string. Keying a
   security property on a label is the implicit string convention `WHATS_NEXT.md` forbids.
2. **Whether an artifact is test-derived.** The lineage lock propagates through every tool call.
   Recomputing it by walking `tool_calls` on every check is fragile, and after a restart it would
   have to be rebuilt.
3. **Which test set a run scored.** Needed to count cross-run exposures through forks.
4. **`superseded`.** D17 and §5 both say a rewind "marks [the parent] superseded"; `RunStatus` has
   no such member, and the DDL CHECK would reject it.

`artifacts.kind` is also unconstrained `TEXT`, unlike every status column, which carries a CHECK
policed by a drift test.

**Change.**

| Layer | Change |
|---|---|
| `vocabulary.py` | `SplitRole = Literal["train", "valid", "test"]` |
| `artifacts.py` | `ArtifactMeta.split: SplitRole \| None = None`. Only split tools set it |
| `models.py` | `RunStatus` gains `"superseded"`; `RunRow.test_fingerprint: str \| None`; `ArtifactRow.split: SplitRole \| None`, `ArtifactRow.derived_from_test: bool` |
| `001_initial.sql` `runs` | status CHECK gains `'superseded'`; `test_fingerprint TEXT` (nullable) |
| `001_initial.sql` `artifacts` | `split TEXT CHECK (split IN ('train','valid','test'))` nullable; `derived_from_test INTEGER NOT NULL DEFAULT 0 CHECK (derived_from_test IN (0, 1))`; `kind` gains a CHECK over `ArtifactKind` |
| `repository.py` | `set_test_fingerprint(conn, run_id, fingerprint) -> bool`: write-once, `UPDATE … WHERE test_fingerprint IS NULL`, the same shape as `claim_test_scoring`. Also `count_prior_test_scorings(conn, *, project_id, fingerprint, exclude_run_id) -> int` |
| `storage.py` | `create_artifact` gains keyword `split` and `derived_from_test`. `fork_artifacts` is **unchanged**: see the note below |

The status, split and kind CHECKs are covered by the existing CHECK-versus-`Literal` drift test. Extend
it to the two new columns rather than writing a second mechanism.

**Tests.**
- `superseded` is accepted by the DB and the model, and the drift test covers it.
- A non-member `kind` or `split` is rejected by the DB.
- `derived_from_test` defaults to false.
- `set_test_fingerprint` succeeds once and refuses a second write.
- `count_prior_test_scorings` counts only runs in the same project with the same fingerprint and a
  non-null `test_scored_at`, excluding the current run.
- After a fork, the linked file's stem is the parent's ref, and that ref resolves to the parent's
  row with `split="test"` and `derived_from_test=True`. No child row is created.
- An `ArtifactMeta` with `split` round-trips byte-identically through `meta_json`.

**Forks keep the parent's refs; the child gets no new rows (amended after the first implementation).**
My original R4 text said `fork_artifacts` copies `split`/`derived_from_test` onto *child rows*. That
contradicts the implemented chunk 06 design: `fork_artifacts` hard-links each file into the child's
`artifacts/` under the **same** ref and creates no rows. The first implementation's test "proved"
the copy by registering a child row with a new ref pointing at a file named with the old ref,
which breaks chunk 06's `row.id == Path(row.path).stem` invariant. The implemented design is the
right one. An artifact ref is globally unique (64 random bits), so the registry is keyed by ref, not
by `(run, ref)`. A fork's state refers to the parent's refs, the hard-linked file keeps the ref as its
stem, and the lineage flags live on the single row and are inherited for free. Nothing has to be
copied, so nothing can disagree. The R4 test asserts exactly that: after a fork, the linked file's
stem is the ref, and resolving that ref yields the parent's row with `split="test"` and
`derived_from_test=True`. Consequences, recorded in `PLAN.md` D11 and `00_index.md`: listing a run's
artifacts must follow `forked_from_run_id` (chunk 31), and a future run-deletion feature must refuse
while a fork still references the run's artifacts, because `artifacts.run_id` cascades.

**Out of scope here:** setting these fields during a run. Chunk 10's split tools set `split`, and
chunk 21's `mcp_client` sets `derived_from_test` and enforces the lock. R4 only makes them storable.

**Outcome.** Landed across `vocabulary.py` (`SplitRole`), `artifacts.py` (`ArtifactMeta.split`),
`models.py` (`RunStatus + 'superseded'`, `RunRow.test_fingerprint`, `ArtifactRow.split`/
`derived_from_test`), `001_initial.sql`, `repository.py` and `storage.py`. `repository.set_test_fingerprint`
is write-once (raises `PersistenceError` on an unknown run, `False` on a second write) and
`count_prior_test_scorings` counts same-project runs with the same fingerprint and a non-null
`test_scored_at`, excluding the asking run. `storage.create_artifact` gained keyword `split` and
`derived_from_test` with a `meta.split == split` guard, and reads route through `_artifact_row_from`,
which maps the INTEGER 0/1 column to `bool` explicitly. `fork_artifacts` is unchanged: it hard-links
each file under the **same** ref and creates no rows, so lineage is inherited. The CHECK-vs-`Literal`
drift test was extended to `kind` and `split`. Reviewer probed every guard directly: bad
kind/split/`derived_from_test=2` rejected, second fingerprint write refused, prior-scoring counts
correct, `superseded` accepted.

The final runs and artifacts DDL, verbatim from `001_initial.sql`:

```sql
CREATE TABLE runs (
    id                 TEXT PRIMARY KEY NOT NULL,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    contract_id        TEXT NOT NULL REFERENCES contracts(id),
    mode               TEXT NOT NULL CHECK (mode IN ('guided', 'autonomous')),
    status             TEXT NOT NULL CHECK (
        status IN (
            'pending', 'running', 'awaiting_user', 'paused', 'failed', 'cancelled',
            'completed', 'superseded'
        )
    ),
    current_node       TEXT,
    champion_model_id  TEXT REFERENCES models(id),
    forked_from_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    config_json        TEXT NOT NULL,
    test_scored_at     TEXT,
    test_fingerprint   TEXT
);

CREATE TABLE artifacts (
    id                TEXT PRIMARY KEY NOT NULL,
    run_id            TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (
        kind IN (
            'dataframe', 'predictions', 'transformer', 'model', 'inferential_fit',
            'curve', 'table', 'plot', 'report'
        )
    ),
    path              TEXT NOT NULL,
    meta_json         TEXT NOT NULL,
    produced_by       TEXT NOT NULL,
    ts                TEXT NOT NULL,
    pruned_at         TEXT,
    split             TEXT CHECK (split IN ('train', 'valid', 'test')),
    derived_from_test INTEGER NOT NULL DEFAULT 0 CHECK (derived_from_test IN (0, 1))
);
```

Tests: `tests/test_contract_vocabulary.py::test_split_role_members`;
`tests/test_contracts_artifacts.py::test_artifact_meta_split_defaults_to_none`,
`::test_artifact_meta_split_accepts_every_role`, `::test_artifact_meta_split_rejects_non_member`,
`::test_artifact_meta_with_split_round_trips_byte_identically`;
`tests/test_db_schema.py::test_artifacts_kind_check_matches_artifact_kind_literal`,
`::test_artifacts_split_check_matches_split_role_literal`, `::test_artifacts_kind_check_rejects_non_member`,
`::test_artifacts_split_check_rejects_non_member`, `::test_artifacts_derived_from_test_check_rejects_two`,
`::test_artifacts_derived_from_test_defaults_to_zero`, `::test_runs_status_check_accepts_superseded`;
`tests/test_repository.py::test_new_run_has_no_test_fingerprint`,
`::test_set_test_fingerprint_first_write_returns_true`, `::test_set_test_fingerprint_second_write_is_refused`,
`::test_set_test_fingerprint_unknown_run_id_raises`, `::test_run_row_test_fingerprint_round_trips`,
`::test_count_prior_test_scorings_counts_matching_runs_excluding_self`,
`::test_count_prior_test_scorings_ignores_runs_not_yet_scored`,
`::test_count_prior_test_scorings_ignores_other_fingerprints`,
`::test_count_prior_test_scorings_ignores_other_projects`,
`::test_count_prior_test_scorings_excludes_the_asking_run_itself`;
`tests/test_storage.py::test_create_artifact_split_and_derived_default`,
`::test_create_artifact_records_split_and_derived_from_test`, `::test_create_artifact_rejects_meta_split_mismatch`,
`::test_create_artifact_allows_none_meta_split_with_set_split_arg`,
`::test_forked_test_artifact_keeps_split_and_derived_from_test`;
`tests/test_storage_fork.py::test_f1_linked_file_is_same_inode`, `::test_f2_fork_keeps_parent_refs`
(renamed from `test_f2`, asserts same stem, same inode, resolved row `split='test'` and
`derived_from_test=True`, and `list_artifacts(child) == ()`). One extra integration fix:
`tests/test_db_migrations.py` `known_valid_values` gained `'kind': 'dataframe'` because the new kind
CHECK fired before the FK in `test_m13`.

---

## R5 — "auto mode" in `envelope.py`

`contracts/statlab_contracts/envelope.py:17` says "even in auto mode". The canonical token is
`autonomous` (D36a). Change the comment. No test.

**Outcome.** The comment in `envelope.py` was changed to the canonical `autonomous` token. Comment
only, no test.

---

## R6 — `detect_outliers`: name the denominator

`profiling.py:504`: `share = count / mask.size`, where `mask` comes from the non-null values. Every
other `share` in the profiling summaries is over all rows (`report_missingness`) or over non-null rows
of a *categorical* distribution. A consumer comparing outlier share with missingness share is comparing
different bases without knowing it. Rename the key to `share_of_non_null`, and update the test that
reads it. Don't change the value.

**Outcome.** `detect_outliers`' summary key was renamed from `share` to `share_of_non_null`; the value
(the non-null denominator) is unchanged. Test:
`tests/test_tools_profiling_summaries.py::test_r6_detect_outliers_share_key_is_share_of_non_null`.

---

## R7 — State the cancellation limit in the decorator

`mcp_server/statlab_tools/tool.py`'s module docstring should say what D36c records: tool bodies run in
worker threads via `anyio.to_thread.run_sync`, a running thread can't be interrupted, so cancellation
and pause take effect only between tool calls, and any time budget must be enforced inside the body.
Without this, chunk 31 will look for a timeout hook on the decorator that can't exist. Documentation
only.

**Outcome.** The `@tool` decorator's module docstring in `mcp_server/statlab_tools/tool.py` now states
that bodies run in worker threads via `anyio.to_thread.run_sync`, a running thread cannot be
interrupted, cancellation and pause take effect only between tool calls, and any time budget must be
enforced inside the body. Documentation only, no test.

---

## Reviewed and deliberately not changed

Recorded so these don't get re-investigated.

| Finding | Why no change |
|---|---|
| `_FIT_SEMAPHORE_CACHE` as a race | `anyio.Semaphore` is loop-agnostic, and the dict is only touched on the event loop. Verified across two separate `anyio.run` loops |
| A float column holding only {0.0, 1.0} infers as `boolean` | A 0/1 column that loaded as float because of a null is a flag in practice; that's the common case, not the exception |
| `ArtifactMeta` has no `produced_by` | The backend registry already stores it, and the LLM doesn't need it in the envelope. Adding it would change every tool's output for no consumer |
| `skew` and z-score on very small n | `skew` is `NaN` below 3 values and becomes `null`, which is correct. z-scores can't exceed 3 on tiny samples, so no false positives |
| Token comparison, `load_csv` traversal defence, `json_scalar` ordering | Re-checked and correct |
| Trimming the data contract's modelling knobs | Already implemented and validated. `PLAN.md` §4.1 now states their semantics (constraints on node 5, not a frozen shortlist), which removes the ambiguity without code churn |

---

## Plan-only fixes applied in the same review

No code involved; listed so the review is traceable in one place.

- **`PLAN.md` v1.8:** decisions D32–D36. Inline corrections to D7, D10, D16, §4 (node 4 row,
  two-matrix table), §4.1 (`modelling` semantics), §4.2 (lock enforcement, rule 3), §5.1 (crash
  recovery is not a rewind), §6.3 (85 tools; injected parameters stated once; stale `(run, …)`
  signatures; `seed`/`calibrate`; target-encoder and `cast_dtypes` notes; `compare_models` marked
  `[v1]`, making the skeleton's 33 true), §8 (canonical `autonomous`), §9.2 (allow-list checked
  against each tool's `allow_in`), §10.1 (`memory.json` dropped), §10.2 (new columns and
  `superseded`), §11.1 (replay and ordering semantics), M5/M6/M7b/M9, §14.3 (champion-identity,
  lineage and SSE tests).
- **`00_index.md`:**
  - Chunks 15 and 16 swapped. Inference now lands before the walking-skeleton gate, because the
    skeleton fits an inferential model.
  - Chunk 27 split into 27a (nodes 1–4) and 27b (nodes 5–6).
  - Project memory given an owner (chunk 26).
  - Dependencies fixed: 22 (+06), 26 (+05, +22), 27b (+13, +15), 19 and 28 renumbered.
  - Hard rule 5 corrected for `mcp_server`'s local error classes.
  - D32–D36 carried into the rows of chunks 10, 12, 13, 14, 21, 24, 29.
- **Docs:** `contract.py` and chunk 03's spec now say chunk 15 owns the inference registry;
  `WHATS_NEXT.md` names chunk 16 as the keystone.
