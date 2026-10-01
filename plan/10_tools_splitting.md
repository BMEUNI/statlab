---
status: untouched
dependencies: [07, 08, 09, 09r]
completion_date:
implementation_notes: |
---

# Chunk 10 — Splitting tools

**Goal.** The three walking-skeleton splitting tools that node 4 (`data_splitting`) uses:
`split_random`, `split_stratified` and `verify_split`. Every candidate shares one split (D26),
so this is the only place where rows are assigned to train, valid and test.

The tools carry three duties from the v1.8 review. Each split frame says which split it is
(`ArtifactMeta.split`), so the lineage lock (D32a) has a typed fact to key on. The test set has a
fingerprint, so cross-run exposure can be counted (D32c). Undersized splits are refused rather
than produced (D33c). Together with chunk 09 this completes milestone **M3**: "every tool tested
on fixtures; `verify_split` catches a deliberately overlapping split".

---

## 1. Prerequisites & Context

### State (verified, committed — see `git log -1`)

1484 tests (1483 passed, 1 skipped by design), 100 % line and branch coverage (2289 statements,
588 branches), mypy clean on 71 files. Revision batch 09r is applied: `SplitRole`,
`ArtifactMeta.split`, `runs.test_fingerprint` and `repository.set_test_fingerprint` exist.

| Symbol | Location | Use here |
|---|---|---|
| `tool`, `ToolParamError`, `ToolDataError`, `ORCHESTRATOR_SUPPLIED` | `statlab_tools/tool.py` | as in chunks 08–09; `seed` is orchestrator-supplied exactly like `load_csv`'s `dataset` |
| `load_dataframe`, `save_dataframe` | `statlab_tools/artifacts.py` | `save_dataframe` gains `split` (§3.2) |
| `is_numeric_column`, `json_scalar`, `json_list`, `CLASS_TARGET_MAX_LEVELS` | `statlab_tools/tools/_frame.py` | gains `is_class_target` (§3.1) |
| `ArtifactRef`, `new_artifact_ref`, `SplitRole` | `statlab_contracts` | refs and the split vocabulary |
| `churn_frame` / `churn_frame_fixture` | `tests/conftest.py` | 240 rows, `churned` ≈ 30 % positive. At 0.2/0.2 that is 48 rows in each of valid and test, ≈ 14 positives each: above both D33c floors |

### Four findings from probing before writing this spec

Verified against pandas 3.0.5 under `-W error`.

| # | Behaviour | Consequence |
|---|---|---|
| P1 | **Parquet keeps a non-range index.** A frame subset to labels `[5, 1, 3]` reads back with exactly those labels; a `RangeIndex` stays a `RangeIndex` | `verify_split`'s index-overlap check works on stored artifacts. Split tools must never `reset_index` |
| P2 | **`pd.util.hash_pandas_object` is deterministic across processes.** The same digest under `PYTHONHASHSEED=1` and `=2`, over `str`, `category`, `datetime64[us]`, `float` with NaN, `bool` and `Int64` with `NA`, with no warnings | Usable for both the fingerprint and the duplicate-row check |
| P3 | `hash_pandas_object(frame, index=False)` ignores the index: the same values under different labels hash equal | Check 2 catches a relabelled duplicate that check 1 cannot |
| P4 | **`runs` has no reference to a dataset row**, and a split tool receives only an artifact ref | The tool cannot compute D32c's original `sha256(dataset sha256, …)`. Hence D-c |

### Decisions this chunk makes

**D-a. Two strategies now, two at M7.** `split_temporal` and `split_grouped` are not `[v1]` and
are not built here (`PLAN.md` §6.4). When the contract asks for one, the escalation happens in
node 4 (chunk 27a), because no tool exists to fail. The split tools never choose a strategy.

**D-b. Tools return facts; the backend records them.** `mcp_server` has no database access (D13).
The split tools set `ArtifactMeta.split` and return `test_fingerprint` in the summary. Chunk 21's
`mcp_client` registers the rows (`derived_from_test` is true for `meta.split == "test"`) and calls
`set_test_fingerprint`. This corrects the earlier WHATS_NEXT wording, which had the tools call
the repository.

**D-c. The fingerprint is a content digest** (D32c amended):

```python
h = hashlib.sha256(json.dumps([str(c) for c in test.columns]).encode())
h.update(pd.util.hash_pandas_object(test.sort_index(), index=True).to_numpy().tobytes())
test_fingerprint = h.hexdigest()
```

It does not depend on row order, and it changes if the row labels or any value changes. It
matches across runs exactly when the same rows of the same frame land in test. That covers both
cases D32c cares about: a fork, and a re-split with the same seed. It also needs no dataset
identity (P4).

**D-d. Both split tools take `target` and drop rows with a missing target.** Such rows can be
neither fitted nor scored. Dropping them reads no target *value*, so it is leak-free (D34d
amended). The count goes into the summary and a warning. `split_random` therefore gains `target`
(§6.3 amended). It also needs it for the class-count guard: a random split of a 5 %-minority
target can leave a class with almost nothing in test.

**D-e. The guard runs on the actual partitions, before anything is written.** The tool splits in
memory, then runs `verify_split`'s size and balance checks on the result. If either fails it
raises `ToolDataError` and writes no file. One rule, applied in two places: refusing in the split
tool, and reporting in `verify_split`. Thresholds apply uniformly to all three splits: at least
`MIN_SPLIT_ROWS = 30` rows and, for a class target, at least `MIN_CLASS_ROWS_PER_SPLIT = 5` of
every class. D33c names valid and test; train is the largest split in every legal ratio except
extreme ones, where the floor is just as necessary.

**D-f. No sklearn.** Splitting is per-group permutation and cut with
`np.random.default_rng(seed)`. One group gives a random split; one group per class gives a
stratified one. This avoids `train_test_split`'s own preconditions (a class with fewer than 2
members raises `ValueError`), which would otherwise need a pre-check that duplicates D-e. Output
frames keep the input's row order and index labels.

**D-g. `verify_split` reads `test` in node 4.** It is the one exception to D32b's "no tool
receives a test-derived artifact before node 12". It produces no artifact, so nothing downstream
inherits test lineage from it.

**D-h. Drift tolerance is a constant, not a parameter.** §6.3 says "configurable", but the agent
has no basis for choosing a tolerance and the node logic never varies it.
`BALANCE_DRIFT_WARN = 0.05` is an absolute share difference, and it only warns. Failures come
from D33c's counts.

### What this chunk does NOT do

| Not here | Where |
|---|---|
| `split_temporal`, `split_grouped` | M7 (registry-line tools, §6.4) |
| Escalating when the contract's strategy has no tool; choosing a strategy | 27a (node 4) |
| Structural pre-split row drops (duplicates, `drop_rows_above`) | 11; the tools run in node 4 (D34d) |
| Registering artifacts, setting `derived_from_test`, storing `test_fingerprint`, filling `seed` from `split.seed` | 21 (`mcp_client`) |
| Copying `test_fingerprint` to a fork | 31 (rewind handler) |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/splitting.py` |
| modify | `mcp_server/statlab_tools/tools/__init__.py`: one import line |
| modify | `mcp_server/statlab_tools/tools/_frame.py`: add `is_class_target` |
| modify | `mcp_server/statlab_tools/tools/profiling.py`: replace its two inline copies of the rule with `is_class_target` |
| modify | `mcp_server/statlab_tools/artifacts.py`: `save_dataframe(..., split=None)` |
| create | `tests/test_tools_splitting.py`, `tests/test_tools_verify_split.py` |
| modify | `tests/test_tools_frame_helpers.py`, `tests/test_tool_artifacts.py`: additions only |
| modify | this file's frontmatter, `00_index.md` row 10, `WHATS_NEXT.md` |

Nothing under `backend/` or `contracts/` changes.

---

## 3. Implementation Spec

### 3.1 `_frame.is_class_target`

```python
def is_class_target(series: pd.Series) -> bool:
    """A class target unless numeric with more than CLASS_TARGET_MAX_LEVELS distinct non-null
    values (summarize_target's rule, now shared)."""
    return not is_numeric_column(series) or series.nunique() <= CLASS_TARGET_MAX_LEVELS
```

Replace `profiling.py`'s two inline copies (in `summarize_target` and `plot_target_relation`) with
calls to it. Profiling tests must pass **without edits**. `intake.detect_leaky_columns` has a
different rule (`n_distinct <= CLASS_TARGET_MAX_LEVELS` regardless of dtype), and this chunk does
not touch it.

### 3.2 `save_dataframe` gains `split`

Add the keyword `split: SplitRole | None = None` and pass it into `ArtifactMeta`. Every existing
call site keeps working. Nothing else in `artifacts.py` changes.

### 3.3 `splitting.py`

Constants (all `Final`): `MIN_SPLIT_ROWS = 30`, `MIN_CLASS_ROWS_PER_SPLIT = 5`,
`BALANCE_DRIFT_WARN = 0.05`, `OVERLAP_EXAMPLES = 5`. The split order is always
`("train", "valid", "test")` and the pairs are `train/valid`, `train/test`, `valid/test`.

**Check results.** Each check returns `dict[str, JsonValue]` with `"status"` in
`"pass" | "warn" | "fail" | "skipped"` plus its details. Every number passes through
`json_scalar`.

- `_check_size(splits)` gives `{"status", "rows": {split: n}, "problems": [str]}`. It fails when
  any split has fewer than `MIN_SPLIT_ROWS` rows.
- `_check_balance(splits, target)` returns `{"status": "skipped", "reason": "continuous target"}`
  when `not is_class_target(pooled)`, where `pooled` is the three target columns concatenated.
  Otherwise:
  - The classes are those of `pooled.dropna()`. Per-split counts come from
    `value_counts().reindex(classes, fill_value=0)`, so a class absent from a split shows as `0`.
    Do not reuse `summarize_class_target` here: it omits absent classes, which is exactly the case
    this check exists for. Class keys are `str(value)`.
  - It fails when any class has fewer than `MIN_CLASS_ROWS_PER_SPLIT` rows in any split.
  - It warns when `max_drift` exceeds `BALANCE_DRIFT_WARN`. `max_drift` is the largest
    `|share_in_split − share_in_pooled|`, with shares computed over non-null values.
  - Result: `{"status", "classes": {split: {class: count}}, "pooled_shares": {class: share},
    "max_drift", "problems": [str]}`.
- `_check_overlap(splits)` gives `{"status", "pairs": {"train/valid": n, …}, "examples":
  [≤ OVERLAP_EXAMPLES labels]}` from `a.index.intersection(b.index)`. It fails when any pair is
  non-zero.
- `_check_duplicates(splits)` has the same shape. For each pair (a, b) it counts the rows of b
  whose `hash_pandas_object(row, index=False)` value occurs in a's hashes. It fails when any pair
  is non-zero. Hash collisions on 64 bits are negligible at D15 sizes; say so in the docstring.

**Partitioning.** `_cut(positions, rng, test_size, valid_size)` permutes the positions and takes
the first `round(n * test_size)` for test, the next `round(n * valid_size)` for valid, and the rest
for train. `split_random` cuts all positions once. `split_stratified` cuts each class's positions,
iterating classes in `sorted(..., key=str)` order with **one** generator, and unions the results.
Each output frame is `frame.iloc[np.sort(positions)]`, so the input's row order and labels are
kept (P1).

**Shared preparation** (both split tools, in this order):

1. Load the frame.
2. If `target` is missing, raise `ToolParamError` with field `target`.
3. If `test_size + valid_size >= 0.9`, raise `ToolParamError` with field `valid_size`. This
   mirrors contract rule V10.
4. Drop rows whose target is null and keep the count.

**Shared finish:**

1. Build the three frames.
2. Run `_check_size` and `_check_balance`. If either fails, raise `ToolDataError`; its message
   starts `split refused (D33c):` and lists every problem.
3. Otherwise save the three frames as kind `dataframe` with labels `train` / `valid` / `test` and
   matching `split`, and return:

```
summary  = {"rows": {split: n}, "rows_dropped_null_target": k,
            "test_fingerprint": "<64 hex>",
            "checks": {"size": …, "target_balance": …}}
artifacts = (train_meta, valid_meta, test_meta)
warnings  = one line if k > 0; one line if target_balance is "warn"
```

**The three tools** (`phase="splitting"`, `allow_in=["data_splitting"]`, not heavy). The frame
parameter is named `frame`, as in every implemented tool; §6.3's `df` is notation.

```python
def split_random(paths, frame: ArtifactRef, target: str, test_size: float = 0.2,
                 valid_size: float = 0.2, seed: int = <ORCHESTRATOR_SUPPLIED, ge=0>) -> ToolSuccess
def split_stratified(...same...) -> ToolSuccess
def verify_split(paths, train: ArtifactRef, valid: ArtifactRef, test: ArtifactRef,
                 target: str) -> ToolSuccess
```

- **Parameter bounds** match `SplitRules`: `test_size` with `gt=0, lt=1`; `valid_size` with
  `ge=0, lt=1`. `seed` is required, with `description=ORCHESTRATOR_SUPPLIED`.
- **`split_stratified`** raises `ToolParamError` with field `target` when
  `not is_class_target(target)`: "N distinct numeric values; stratifying a continuous target is
  meaningless; use split_random".
- **`verify_split`**:
  - Raises `ToolParamError` with field `target` when the target is missing from any frame.
  - Raises `ToolParamError` with fields `valid` and/or `test` when a frame's column *set* differs
    from train's.
  - Otherwise it reorders valid and test to train's column order before hashing, because the row
    hash depends on column order.
  - Summary: `{"passed": bool, "checks": {"index_overlap", "duplicate_rows", "target_balance",
    "size"}}`. `passed` is false iff any check's status is `"fail"`.
  - Warnings: one line per check whose status is `warn` or `fail`.

Docstrings and `Field(description=)` are mandatory (the decorator enforces both). The split tools'
docstrings say when to use each one. `verify_split`'s docstring names the four checks.

---

## 4. Test Suite Spec

Every guard is tested by triggering the failure it prevents. Tools are called directly
(`split_random(run_paths, ref, target=..., seed=...)`), except for the one MCP test. Fixture
frames are saved with `save_dataframe` and `new_artifact_ref()`.

### 4.1 `test_tools_splitting.py` (~45)

- **Sizes and partition:**
  - On `churn_frame` at 0.2/0.2: rows are exactly 144/48/48.
  - The three index sets are pairwise disjoint and their union is the input index.
  - Each output keeps the input's order (`index.is_monotonic_increasing` on a monotonic input).
  - Labels are preserved on an input whose index is not a `RangeIndex`.
- **Determinism:**
  - The same seed twice gives `assert_frame_equal` on all three frames and the same fingerprint.
  - A different seed gives a different test index and a different fingerprint.
- **Stratification:** each class's count per split equals `round(n_c * size)` exactly. A
  multiclass and a `bool` target both work.
- **Null target:**
  - Three null targets give `rows_dropped_null_target == 3`, the warning is present, and no
    output holds a null target.
  - The same rows minus the nulls give the same partition sizes.
- **Guard (D33c):** each case raises `ToolDataError` (`error_class == "data"` via the decorator
  path), and the run's `artifacts/` directory holds **no new file**:
  - 100 rows (test would hold 20);
  - `valid_size=0`;
  - a 20-row minority class under `split_stratified` (4 in test);
  - a tiny class under `split_random`.
- **Params:** each case is `tool_param` with the right field:
  - missing target;
  - `0.5 + 0.4`;
  - `split_stratified` on a continuous target (churn's `age`).
- **Continuous target under `split_random`:** succeeds, and `target_balance` is `"skipped"`.
- **Fingerprint:**
  - It equals D-c's formula, recomputed in the test from the *loaded* test artifact.
  - It is unchanged by reversing row order.
  - It changes when one value changes, and when the labels are shifted.
- **Artifacts:** `meta.split` and `label` are `train` / `valid` / `test` in that order;
  `rows`/`cols` are right.
- **Strict JSON:** every split tool's result, and `verify_split`'s, survives
  `json.dumps(model_dump(mode="json"), allow_nan=False)` on `churn_frame` and on
  `pd.concat([messy_frame] * 5, ignore_index=True)` with target `target`. `messy_frame` alone
  has 60 rows, below the guard. Five copies keep every dtype hazard (`Int64` with `NA`, `bool`,
  `datetime64[us]`, `$` in column names) flowing through partitioning and hashing. The copies
  are exact duplicates, so `verify_split` on that input must fail `duplicate_rows`; assert that
  too.
- **MCP** (one test, `mcp_session` + `run_paths_over_mcp`): `split_stratified` round-trips, and the
  structured content's artifacts carry `"split": "test"`. In `tools/list`, `seed`'s description is
  `ORCHESTRATOR_SUPPLIED`. The `meta` carries `phase="splitting"` and `allow_in=["data_splitting"]`.

### 4.2 `test_tools_verify_split.py` (~30)

- A clean `split_stratified` output passes all four checks and `passed` is true.
- **M3 acceptance:** a deliberately overlapping split (test shares 10 labels with train) fails
  `index_overlap` with the right pair count and examples, also fails `duplicate_rows`, and `passed`
  is false.
- A relabelled copy (train rows re-indexed into test) passes `index_overlap` and fails
  `duplicate_rows`. This is P3, and it is the test that fails if the hash ever includes the index.
- Shuffled column order in test still detects the duplicate. A different column set is
  `tool_param` with field `test`.
- **Balance:**
  - A drifted split warns, and `passed` stays true.
  - A split with zero of a class fails.
  - One with 4 of a class fails.
  - A continuous target is `"skipped"`.
- **Size:** a 29-row valid fails. A missing target is `tool_param`.
- `warnings` has one line per non-pass, non-skipped check.

### 4.3 Additions

- `test_tools_frame_helpers.py`: `is_class_target` on str, bool, a 20-level int, a 21-level int, a
  float with NaN, and the all-null case.
- `test_tool_artifacts.py`: `save_dataframe(split="test")` sets `meta.split`. The default is `None`.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **1550**.
- [ ] Coverage **100 %** line and branch; project stays 100 %.
- [ ] Profiling and intake tests pass **without edits** after `is_class_target` is extracted.
- [ ] **Mutation evidence:** each change below makes at least one test fail. Restore after each
      and confirm `git diff` shows only intended changes.
  - `MIN_SPLIT_ROWS = 0`;
  - `MIN_CLASS_ROWS_PER_SPLIT = 0`;
  - dropping `sort_index()` from the fingerprint;
  - `index=True` in the duplicate hash;
  - removing the null-target drop;
  - `reset_index(drop=True)` on the outputs.

**Static analysis**
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new `# type: ignore`.
- [ ] No `sklearn` import in `splitting.py`. No `reset_index` in `splitting.py`.

**Behaviour**
- [ ] A refused split writes no file.
- [ ] Every number in every summary passes through `json_scalar`.

**Housekeeping**
- [ ] This file's frontmatter (`status: completed`, date, `implementation_notes`).
- [ ] `00_index.md`: row 10 completed, M3 marked completed. Add any new verified environment fact
      as a one-liner.
- [ ] `WHATS_NEXT.md`: state table measured; "Next" set to chunk 11, carrying the D34d note from
      `00_index.md` row 11; and a `## Chunk 10 — completed (summary)` section in the existing
      style.
- [ ] **Do not commit.**

### Stop and ask if

- P1–P3 do not reproduce.
- A profiling or intake test needs editing after the `is_class_target` move.
- `churn_frame` at 0.2/0.2 is refused by the guard.
- Anything seems to need sklearn, a database connection, or a change under `backend/` or
  `contracts/`.

### Report back with

1. Test count (passed/skipped), coverage statements/branches, mypy file count, ruff.
2. The mutation table: change → which tests failed → restored.
3. `split_stratified` on `churn_frame` (seed 42), with its summary verbatim.
4. `verify_split`'s summary for the deliberately overlapping split, verbatim.
5. Across seeds 0–99, how often `split_random` on `churn_frame` warns on balance. This tests
   whether `BALANCE_DRIFT_WARN = 0.05` is a useful signal or noise.
6. Anything about parquet or hashing that surprised you. "None" is fine if true.
7. For chunk 11: no catalogue tool drops rows above `drop_rows_above` missing share, and D34d
   needs one in node 4. Should that be a new tool, or a mode of an existing one? Base the answer
   on what you saw of the frame shapes here.
