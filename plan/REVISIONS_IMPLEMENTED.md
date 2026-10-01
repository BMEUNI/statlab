# Revisions to implemented chunks (01–09)

From the full review of 2026-10-01 (`PLAN.md` v1.8). Each item is a change to code that is already
committed. Plan-only fixes were applied directly to `PLAN.md` and `00_index.md` and are listed at the
end for traceability.

**Apply R1–R4 as one batch before chunk 10.** R1–R3 are wrong results in shipped tools. R4 is the
schema and wire foundation that chunks 10 (splitting) and 21 (`mcp_client`) build the D32 test-set
lock on, so writing them first would mean migrating later. R5–R7 are small and can ride along.

Same conventions as every chunk: tests that trigger the failure each fix prevents, 100 % line and
branch coverage, no new suppressions. `001_initial.sql` is edited **in place** for the same reason as
chunks 05 and 06: nothing is deployed, so a corrective migration would be archaeology.

| # | Severity | Chunk | What | Evidence |
|---|---|---|---|---|
| R1 | high | 08 | `validate_contract` reports false mismatches on numeric targets that load as float | reproduced |
| R2 | high | 08 | `detect_leaky_columns` never flags a continuous numeric leak against a class target | reproduced |
| R3 | medium | 08 | rank-correlation leak rule skipped for numeric targets with ≤ 20 values | reproduced |
| R4 | high | 02, 05, 06 | schema and wire fields for the D32 lineage lock, cross-run exposure and `superseded` | design gap, verified by reading |
| R5 | low | 02 | `envelope.py` comment says "auto mode" | grep |
| R6 | low | 09 | `detect_outliers` `share` uses a different denominator from every other `share` | reproduced |
| R7 | low | 07 | decorator docstring does not state that a running body cannot be cancelled | reading |

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
- In `_level_purity`, compute over rows with a non-null level and a non-null target, and return
  `None` when fewer than `LEAK_MIN_ROWS: Final = 20` such rows remain. A new constant, not
  `MIN_ROWS_FOR_ID`: same value, different meaning, and they must be free to diverge.

**Tests.**
- The perfect continuous leak above is flagged with rule `level_purity`.
- The same leak with 20 % missing is flagged with purity ≥ 0.99.
- An unrelated continuous feature on the same balanced target is not flagged.
- A unique string ID column is still skipped.
- The existing imbalanced-target guard test and `churn_frame`-has-no-suspects test still pass,
  unchanged.

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
| `storage.py` | `create_artifact` gains keyword `split` and `derived_from_test`. `fork_artifacts` copies both onto the child rows; a hard-linked test artifact is still test |

The status, split and kind CHECKs are covered by the existing CHECK-versus-`Literal` drift test. Extend
it to the two new columns rather than writing a second mechanism.

**Tests.**
- `superseded` is accepted by the DB and the model, and the drift test covers it.
- A non-member `kind` or `split` is rejected by the DB.
- `derived_from_test` defaults to false.
- `set_test_fingerprint` succeeds once and refuses a second write.
- `count_prior_test_scorings` counts only runs in the same project with the same fingerprint and a
  non-null `test_scored_at`, excluding the current run.
- A forked test artifact keeps `split="test"` and `derived_from_test=True`.
- An `ArtifactMeta` with `split` round-trips byte-identically through `meta_json`.

**Out of scope here:** setting these fields during a run. Chunk 10's split tools set `split`, and
chunk 21's `mcp_client` sets `derived_from_test` and enforces the lock. R4 only makes them storable.

---

## R5 — "auto mode" in `envelope.py`

`contracts/statlab_contracts/envelope.py:17` says "even in auto mode". The canonical token is
`autonomous` (D36a). Change the comment. No test.

---

## R6 — `detect_outliers`: name the denominator

`profiling.py:504`: `share = count / mask.size`, where `mask` comes from the non-null values. Every
other `share` in the profiling summaries is over all rows (`report_missingness`) or over non-null rows
of a *categorical* distribution. A consumer comparing outlier share with missingness share is comparing
different bases without knowing it. Rename the key to `share_of_non_null`, and update the test that
reads it. Don't change the value.

---

## R7 — State the cancellation limit in the decorator

`mcp_server/statlab_tools/tool.py`'s module docstring should say what D36c records: tool bodies run in
worker threads via `anyio.to_thread.run_sync`, a running thread can't be interrupted, so cancellation
and pause take effect only between tool calls, and any time budget must be enforced inside the body.
Without this, chunk 31 will look for a timeout hook on the decorator that can't exist. Documentation
only.

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
