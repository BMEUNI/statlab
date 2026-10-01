---
status: untouched
dependencies: [07, 11]
completion_date:
implementation_notes: |
---

# Chunk 12 — Encoding tools

**Goal.** The eight encoding tools from `PLAN.md` §6.3: one-hot, ordinal and target encoders, and
a scaler, each as a `fit_*`/`apply_*` pair. Four are walking-skeleton `[v1]`: the one-hot and
scaler pairs. Node 8 (`feature_engineering`) uses them to build a candidate's predictive matrix.
This completes **M4**. Batch B1 pairs it with chunk 13, which consumes these matrices.

---

## 1. Prerequisites & Context

### State

Committed; see `git log -1`. 1628 tests (1627 passed, 1 skipped by design), 100 % line and
branch coverage, mypy clean on 77 files.

**Read `mcp_server/statlab_tools/tools/cleaning.py` first.** This chunk copies its shape exactly:
- a frozen dataclass of fitted state, pickled as kind `transformer`;
- `apply_*` type-checks the loaded object, so a wrong ref is `tool_param`;
- the target is never transformed;
- labels and row order are preserved;
- `json_scalar` is used on every summary number.

| Symbol | Location | Use here |
|---|---|---|
| `_require_columns`, `_save_frame`, `_load_fitted` | `tools/cleaning.py` | **move to `tools/_frame.py`** as `require_columns`, `save_frame`, `load_fitted` (§3.1) |
| `is_numeric_column`, `is_class_target`, `observed_counts`, `json_scalar`, `json_list` | `tools/_frame.py` | as before |
| `ORCHESTRATOR_SUPPLIED` | `tool.py` | `fit_target_encoder`'s `seed` |

### Findings from probing (sklearn 1.9.0, pandas 3.0.5, `-W error`)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | `TargetEncoder(shuffle=, random_state=)` **raises `FutureWarning`** (deprecated in 1.9) | Pass `cv=StratifiedKFold(5, shuffle=True, random_state=seed)` (class target) or `KFold(...)` (continuous) instead |
| P2 | `TargetEncoder.fit_transform` is out-of-fold and differs from `fit(...).transform(...)` on the same rows. It works on `str` dtype and is deterministic for a fixed splitter seed | D34c comes for free. The leak test compares the two |
| P3 | Multiclass target: one output column per class (`plan_a`, `plan_b`, …). An unseen level transforms to the target mean. NaN is its own level | Naming and unseen handling below |
| P4 | A `bool` dummy is excluded by `is_numeric_column` | The scaler's default column selection skips dummies and chunk 11's `__outlier` flags with no special case |

### Decisions this chunk makes

**D-a. An unseen level at apply is representable, not an error** (chunk 11's report, question 7).
Small splits make a rare level that appears only in valid or test plausible. In every case the
count goes into the summary with one warning per column:
- one-hot: an all-`False` row;
- ordinal: NaN;
- target: the target mean (sklearn's own behaviour, P3).

**D-b. One-hot dummies are `bool`, named `f"{col}__{level}"`, and replace the source column.**
Every observed train level gets a column; nothing is dropped. The predictive matrix tolerates full
dummies (chunk 15 builds the inferential matrix with an explicit reference level). A NaN at apply
is an all-`False` row, counted as `null`. A generated name that collides with an existing column
is `tool_param`.

**D-c. `max_cardinality` bounds one-hot.**
- An explicit column above it is `tool_param`, with a message saying to target-encode it.
- With `columns=None`, the encoder selects every non-numeric, non-bool, non-datetime feature with
  at most `max_cardinality` observed levels. The ones above are listed in `skipped_high_cardinality`
  with a warning.

**D-d. `apply_target_encoder` refuses train rows.** §6.3 says "valid/test only, never train":
applying the full-data encoder to train is exactly the in-sample leak that D34c's out-of-fold
encoding prevents. The fitted state keeps train's index labels, and apply refuses a frame whose
index intersects them (`tool_param` on `frame`, pointing at `fit_target_encoder`'s encoded train).
Chunk 10 keeps labels and `verify_split` proves the splits disjoint, so the check is exact. That
is two guarantees from earlier chunks paying off.

**D-e. Scaling and ordinal encoding are pure pandas; only target encoding uses sklearn.** A
`(center, scale)` pair or a level→rank map is a dict, which is readable in the summary, as in
chunk 11. Cross-fitting is the one piece worth not rewriting.

**D-f. Every `fit_*` takes `train` and `target`.** Naming the target in `columns` is `tool_param`.
The parameter name `train` puts all four under chunk 21's lineage rule.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/encoding.py` |
| modify | `tools/_frame.py`: receives the three helpers; `tools/cleaning.py` imports them. **Chunk 11 tests pass without edits** |
| modify | `tools/__init__.py`: one import line |
| create | `tests/test_tools_encoding.py` |
| modify | this file's frontmatter, `00_index.md` row 12, `WHATS_NEXT.md` |

---

## 3. Implementation Spec

**For all eight tools:**
- `phase="encoding"`.
- `fit_*` use `allow_in=["feature_engineering"]`; `apply_*` use
  `allow_in=["feature_engineering", "champion_selection"]`.
- `apply_*` require every fitted column to be present. Other columns pass through untouched, and
  the target may be present or absent.
- Every frame output is kind `dataframe`.

### 3.1 Helper move

`require_columns(frame, names, field)`, `save_frame(paths, frame, label)` and
`load_fitted(paths, ref, expected: type[T], field) -> T` move to `_frame.py` unchanged in
behaviour. `load_fitted` becomes generic over any type: a PEP 695 `[T]` with no constraint list,
so chunks 12–15 do not each edit a constraint tuple.

### 3.2 One-hot (`[v1]`)

**`fit_onehot_encoder(train, target, columns: tuple[str, ...] | None = None, max_cardinality: int = Field(30, ge=2))`**
- Levels are `observed_counts(train[col])`, ordered by `sorted(key=str)`.
- Fitted state: `FittedOneHot(target, levels: dict[str, tuple[object, ...]])`. Label
  `onehot_encoder`.
- Summary: `{"columns": {col: n_levels}, "skipped_high_cardinality": {col: n_levels},
  "output_columns": n}`.
- No frame output: train goes through apply.

**`apply_onehot_encoder(encoder, frame)`**
- Each source column is replaced by its `bool` dummies, inserted at its position.
- Summary: `{"unseen": {col: n}, "null": {col: n}}`, nonzero entries only. Label
  `onehot_encoded`.

### 3.3 Ordinal

**`fit_ordinal_encoder(train, target, columns: tuple[str, ...], level_order: dict[str, tuple[str, ...]] = {})`**
- The order for each column is:
  - `level_order[col]` if given;
  - otherwise an ordered `Categorical`'s categories (chunk 11's `cast_dtypes` produces these);
  - otherwise the observed train levels, `sorted(key=str)`. That order is arbitrary, and the
    docstring says it is meaningful only for tree models.
- A given `level_order` missing an observed train level is `tool_param` on `level_order`: an
  incomplete order silently NaNs real data.
- Level comparison uses the string form, through chunk 11's `_as_str`. Integral floats must match
  `"1"`, not `"1.0"`; this is chunk 11's addendum lesson. Move `_as_str` to `_frame.py` too if it
  is needed here.
- Fitted state: `FittedOrdinal(target, ranks: dict[str, dict[str, int]])`.

**`apply_ordinal_encoder(encoder, frame)`**
- Writes `float64` ranks `0..k−1` in place. NaN stays NaN; unseen levels become NaN and are
  counted (D-a). Label `ordinal_encoded`.

### 3.4 Target (D34c)

**`fit_target_encoder(train, target, columns: tuple[str, ...], smoothing: float | Literal["auto"] = "auto", seed: int = <ORCHESTRATOR_SUPPLIED, required>)`**
- `columns` has `min_length=1`. `smoothing` as a float takes `ge=0`.
- Target type:
  - `binary`: `is_class_target` and 2 classes;
  - `multiclass`: more than 2 classes;
  - `continuous`: otherwise.
- The splitter is `StratifiedKFold(5, shuffle=True, random_state=seed)` for a class target and
  `KFold(...)` otherwise (P1). A class with fewer than 5 rows in train is a `ToolDataError`, so
  sklearn's `ValueError` never surfaces as `bug`.
- `TargetEncoder(target_type=..., smooth=smoothing, cv=splitter)`. Then
  `oof = fit_transform(train[columns], train[target])`.
- Output columns are `f"{col}__te"`, or `f"{col}__te_{class}"` for multiclass. They replace the
  source columns in the returned train frame.
- **Two artifacts:** the encoder (`FittedTargetEncoder(target, columns, encoder: object,
  output_columns, train_labels: pd.Index)`, label `target_encoder`) and the out-of-fold-encoded
  train (label `target_encoded_train`).
- Summary: `{"target_type", "columns", "output_columns", "smoothing", "folds": 5}`.

**`apply_target_encoder(encoder, frame)`**
- D-d: if any frame label is in `train_labels`, raise `tool_param`.
- Otherwise `encoder.transform`, with an unseen count per column (D-a). Label `target_encoded`.

### 3.5 Scaler (`[v1]`)

**`fit_scaler(train, target, method: Literal["standard", "minmax", "robust"], columns: tuple[str, ...] | None = None)`**
- With `columns=None`, selects every `is_numeric_column` feature. That skips bool dummies and
  flags (P4). An explicit non-numeric column is `tool_param`.
- `(center, scale)` per method, over non-null train values:
  - `standard`: mean and `std(ddof=0)`;
  - `minmax`: min and `max − min`;
  - `robust`: median and IQR.
- `scale == 0` is skipped with a warning (chunk 09's P6).
- Fitted state: `FittedScaler(target, method, params: dict[str, tuple[float, float]])`.
- Summary: `{"method", "params", "skipped"}`.

**`apply_scaler(scaler, frame)`**
- `(x − center) / scale` as `float64` (an `Int64` input must not crash: chunk 11's P1). NaN
  stays NaN. Label `scaled`.

---

## 4. Test Suite Spec (~75)

Build inputs by hand, with known answers.

- **Learned on train, replayed** (the core leak tests):
  - the one-hot levels, ordinal ranks and scaler parameters fitted on train are applied unchanged
    to a valid whose own statistics differ;
  - a scaler fitted on train mean 10, std 2 turns valid value 14 into exactly 2.0.
- **Target encoder:**
  - the returned train equals `TargetEncoder(...).fit_transform` and **differs** from
    `fit(...).transform(train)` (P2);
  - the same seed gives the same output, and a different seed a different one;
  - `apply_target_encoder` on train's frame, or on any frame sharing one label with train, is
    `tool_param`; on valid it succeeds;
  - multiclass output naming;
  - a class with 4 rows is `data`.
- **Unseen and null (D-a):**
  - one-hot unseen gives an all-`False` row and a count;
  - ordinal unseen gives NaN and a count;
  - target unseen gives the target mean.
- **Cardinality (D-c):**
  - an explicit column above `max_cardinality` is `tool_param`;
  - `columns=None` skips and lists it;
  - a categorical with unused categories counts observed levels only (`observed_counts`).
- **Ordinal order:**
  - an explicit order is respected;
  - an ordered `Categorical` from `cast_dtypes` uses its categories;
  - an incomplete `level_order` is `tool_param`;
  - float-coded levels `1.0`/`2.0` match `"1"`/`"2"`.
- **Scaler:**
  - each method's parameters match a hand computation;
  - a zero-spread column is skipped with a warning;
  - `Int64` input works;
  - bool dummies are not scaled when `columns=None`.
- **Invariants:**
  - the target is never encoded or scaled, even when named or when it is a string column;
  - labels and order are preserved on a non-range index;
  - a dummy-name collision is `tool_param`;
  - a wrong-type fitted ref is `tool_param` for each apply.
- **Integration:** chunk 11 into chunk 12 on `churn_frame`: cast → split → impute → one-hot →
  scale. The result has only numeric/bool columns besides the target, with no nulls, and
  identical columns on train and valid.
- Strict JSON for every tool's result on `churn_frame`. One MCP round-trip,
  `fit_onehot_encoder` → `apply_onehot_encoder`.

---

## 5. Definition of Done

- [ ] `make test` exits 0. Coverage **100 %** line and branch. Chunk 11 tests pass **without
      edits** after the helper move.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - `apply_onehot_encoder` relearns levels from the frame;
  - `fit_target_encoder` uses `fit().transform()` instead of `fit_transform()`;
  - remove the train-label check;
  - `apply_scaler` recomputes the parameters;
  - remove the zero-spread skip;
  - an unseen ordinal level maps to 0 instead of NaN.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`, no bare `value_counts()`, no `reset_index`.
- [ ] Frontmatter; `00_index.md` row 12 completed and **M4 marked completed**; new environment
      facts (P1 at least) as one-liners; `WHATS_NEXT.md` gets a short "Chunk 12 — completed"
      summary. Chunk 13's stage updates the state table and "Next". **Do not commit.**

### Stop and ask if

P1–P4 do not reproduce; a chunk 11 test needs editing after the helper move; anything needs a
change under `backend/` or `contracts/`.

### Report back with

1. Gate numbers.
2. The mutation table.
3. The integration test's final train column list and dtypes.
4. Anything about `TargetEncoder` that surprised you.
5. For chunk 13: is there any column dtype the encoders can output that sklearn would reject or
   misread (nullable `boolean`, ordered `Categorical`, `Int64`)?
