---
status: untouched
dependencies: [07, 12, 14]
completion_date:
implementation_notes: |
---

# Chunk 15 — Inference tools

**Goal.** The D7 second track. The tools are:
- the `SPECS` registry;
- the two inference-matrix tools (`build_dummy_matrix` `[v1]`, `assemble_inference_matrix`);
- the nine statsmodels tools from `PLAN.md` §6.3 (`fit_ols`, `fit_logit` and
  `coefficient_table` are `[v1]`).

Node 6 (`inferential_analysis`) calls them once per run on train+valid (D31). D35 lands here: a
coefficient is confirmatory only where pre-registered, and Holm correction runs across the goals.
M6's "known-answer inference test" is this chunk's NIST Longley test.

This chunk runs after chunk 14 in batch B2. It only touches `registry.py` and `training.py`'s
`list_estimators` among chunk 14's files.

---

## 1. Prerequisites & Context

| Symbol | Location | Use here |
|---|---|---|
| `registry.py` | chunk 13 / 14 | gains `SPECS` (§3.1) |
| `list_estimators` | `tools/training.py` | also reports `SPECS` |
| `InferenceGoal` | `statlab_contracts.contract` | the `goals` parameter type, reused as is |
| `as_str`, `observed_counts`, `json_scalar`, `require_columns`, `save_frame`, `load_fitted` | `tools/_frame.py` | |
| `save_model` (kind `inferential_fit`), `save_json` (kind `table`), `save_plot` | `statlab_tools/artifacts.py` | |

### Findings from probing (statsmodels 0.15.0, `-W error`)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | **NIST StRD Longley**, OLS through statsmodels: constant `-3482258.634597771` vs certified `-3482258.63459582`; GNPDEFL `15.0618722715` vs `15.0618722713733`; R² `0.9954790045772902` vs `0.995479004577296` | The known-answer test compares our tool's output to the **certified** values, hard-coded from NIST, to relative 1e-8 |
| P2 | Logit with one binary predictor reproduces the exact log-odds and log odds-ratio to 1e-10 | A second known answer, needing no reference data |
| P3 | **Perfect separation does not raise.** statsmodels emits `PerfectSeparationWarning` and `ConvergenceWarning`, and returns `converged=False` with meaningless coefficients. It installs its own `"always"` filter at import, so plain `-W error` does not stop it, but **pytest's per-test `filterwarnings=error` does turn them into exceptions** | Test and production behave differently. Handle both: catch those two warning classes raised as exceptions, **and** check `mle_retvals["converged"]`. Both become `ToolDataError("perfect separation or non-convergence: coefficients not identified")` |
| P4 | `result.wald_test(R, scalar=True)` works with no warning; `multipletests(method="holm")` works | Per-goal joint tests and Holm |
| P5 | `get_influence()` exists for OLS, GLM (`GLMInfluence`) and Logit (`MLEInfluence`); Cook's distance and leverage are available for all three | One influence tool for every fit |
| P6 | **`patsy` formulas execute arbitrary Python**: `y ~ I(__import__('os').getpid()*0 + x)` ran | No formula strings anywhere (D-a) |
| P7 | Results objects pickle and round-trip | Fitted state as a dataclass holding the result, kind `inferential_fit` |

### Decisions this chunk makes

**D-a. No formulas.** §6.3 writes `fit_ols(matrix, formula)`. A formula is agent-written text
that patsy evaluates as Python (P6), which is code execution at a trust boundary. Fits instead
take `matrix`, `target` and `predictors: tuple[str, ...] | None` (`None` means every non-target
column), and add the constant themselves. Transformations (logs, interactions) are therefore not
expressible in v1 inference. That is recorded as a known limit; D35 already labels any
post-EDA addition exploratory.

**D-b. Dummy coding with an explicit reference.** For each non-numeric predictor the reference
level is `reference_levels[col]` if given, else the **most frequent** observed level (ties go to
the first by string order). The reference is dropped and reported. Dummies are `float64` 0/1 named
`f"{col}__{level}"`. `bool` becomes 0/1 `float64`. Numeric columns pass through unscaled (D31: the
coefficients stay in original units).

**D-c. The manifest is an artifact.** Both matrix tools write a second artifact, a `table`
manifest:
- `terms`: term → source column;
- `references`;
- `imputed`;
- `indicators`;
- `dropped`, each with a reason.

`coefficient_table` reads it to map the goals' source columns to terms. Parsing names on `__`
would break on column names that contain it.

**D-d. `assemble_inference_matrix` is node 6's canonical recipe** (D31). It takes `train` and
`valid` (never test) and:
1. concatenates them; overlapping labels are `tool_param`;
2. imputes median (numeric) or mode (otherwise) on the concatenation;
3. adds an indicator `f"{col}__missing"` for a predictor with missing share ≥
   `MISSING_INDICATOR_SHARE = 0.05`;
4. dummy-codes (D-b);
5. drops constant columns, then **perfectly collinear** ones: columns are added one at a time,
   and any column that does not raise `numpy.linalg.matrix_rank` is dropped, with a warning for
   each.

`build_dummy_matrix` is step 4 alone, on any frame, and refuses NaN in a coded column
(`tool_param`: impute first).

**D-e. Confirmatory means pre-registered** (D35). `coefficient_table(fit, manifest, goals)`:
- A term is `confirmatory` if its source column is in some goal's `predictors`, else
  `exploratory`, with `p_note: "not pre-specified"`. The intercept is `intercept`.
- Each goal gets **one joint Wald test** over all its terms. A three-level predictor is two
  dummies, and the question "does plan affect churn" is about both. The result is the statistic,
  df and raw p.
- With more than one goal, Holm-adjusted p is added, and each goal's `alpha` is applied to the
  adjusted value.
- A goal whose predictors have no terms is reported `untestable` with the manifest's reason and is
  excluded from Holm. An absent column was dropped as constant or collinear, or was never a
  predictor. That is not an error.

**D-f. Logit's positive class** is the snapshot's `target.positive_class`, matched by string
form. Otherwise it is the second level in string order, and the summary records which. A target
with other than exactly two levels is `tool_param`.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/inference.py`: all eleven tools |
| modify | `registry.py`: `SPECS` (§3.1). `training.list_estimators` adds `"inferential_models"` to its summary |
| modify | `tools/__init__.py`: one import line |
| create | `tests/test_tools_inference.py`, `tests/test_tools_inference_matrix.py` |
| modify | this file's frontmatter, `00_index.md` row 15, `WHATS_NEXT.md` (state table measured; "Next" set to batch B3, chunk 16 alone; a combined `## Batch B2 — completed (summary)` absorbing chunk 14's short section) |

---

## 3. Implementation Spec

**Conventions:**
- statsmodels is imported **lazily inside tool bodies**.
- statsmodels is untyped here, like sklearn: keep it behind `object` fields and narrow at the
  boundary.
- `phase="inference"`, `allow_in=["inferential_analysis"]` for all eleven.
- The three fits are `heavy=True`.

### 3.1 `SPECS`

```python
@dataclass(frozen=True)
class InferentialEntry:
    tool: str                          # the fit tool that fits it
    target_types: tuple[TargetType, ...]
    family: Literal["poisson", "gamma"] | None = None

SPECS = {
    "ols":         InferentialEntry("fit_ols",   ("continuous",)),
    "logit":       InferentialEntry("fit_logit", ("binary",)),
    "glm_poisson": InferentialEntry("fit_glm",   ("count",), "poisson"),
    "glm_gamma":   InferentialEntry("fit_glm",   ("continuous",), "gamma"),
}
```

`list_estimators` reports it, so node 1 can validate `modelling.inferential_model` without
importing server code.

### 3.2 Matrix tools

**`build_dummy_matrix(frame, target, columns: tuple[str, ...], reference_levels: dict[str, str] = {})`**
- D-b on `columns`; other columns pass through.
- Two artifacts: the matrix (label `inference_matrix`) and the manifest (D-c).
- An unknown column, the target in `columns`, or a reference level not observed is `tool_param`.

**`assemble_inference_matrix(train, valid, target, predictors: tuple[str, ...], reference_levels={})`**
- D-d. The output has only `predictors`' terms plus the target, with no NaN.
- Same two artifacts. Summary: `{"n_obs", "terms", "dropped", "indicators", "references"}`.

### 3.3 Fits

All three take `(matrix, target, predictors=None)`:
- Every predictor must be numeric or bool with no NaN, else `tool_param` ("use
  assemble_inference_matrix").
- `n_obs ≤ n_params + 1` is a `ToolDataError`.
- Each saves `FittedInference(model: str, target, terms: tuple[str, ...], n_obs,
  positive_class: str | None, result: object)` as kind `inferential_fit`.
- Summary: `{"model", "n_obs", "n_params", "converged", "aic"}`.

| Tool | Model | Target check |
|---|---|---|
| `fit_ols` | `sm.OLS` | numeric |
| `fit_logit` | `sm.Logit(...).fit(disp=0)`, P3 handling | exactly two levels (D-f) |
| `fit_glm(..., family: Literal["poisson", "gamma"])` | `sm.GLM` with the canonical link | poisson: non-negative integers; gamma: strictly positive. Otherwise `tool_param` |

### 3.4 Reporting and diagnostics

**`coefficient_table(fit, manifest, goals: tuple[InferenceGoal, ...] = ())`**
- D-e. Rows `{"term", "source_column", "role", "coef", "se", "stat", "p", "ci_low", "ci_high",
  "p_note"}` with a 95 % CI.
- Goal rows `{"question", "predictors", "terms", "stat", "df", "p_raw", "p_holm", "alpha",
  "reject", "status"}`.
- Writes a `table` artifact. Summary: the goal rows plus `n_terms`, `n_confirmatory`.

**`inferential_fit_summary(fit)`**
- Model-appropriate fields:
  - OLS: R², adjusted R², F with its p;
  - Logit: McFadden pseudo-R², LLR with its p;
  - GLM: `pseudo_rsquared()` and deviance.
- All models: AIC, BIC, log-likelihood, `n_obs`, `df_model`, `converged`.

**`compute_vif(matrix, target, predictors=None)`**
- VIF per predictor, with the constant added and excluded from the output.
- `flagged` when VIF > `VIF_WARN = 10`. Table artifact.

**`test_residuals(fit, tests: tuple[Literal["normality", "heteroskedasticity", "autocorrelation"], ...] = all three)`**
- **OLS only**; another model is `tool_param`.
- Jarque–Bera (statistic, p), Breusch–Pagan (LM, p) and Durbin–Watson (statistic only; the
  docstring says it is meaningful only for ordered data).

**`compute_influence(fit)`**
- Cook's distance and leverage for every model, plus DFFITS for OLS.
- Summary: `{"n_above_cooks_4_over_n", "top": [≤ INFLUENCE_TOP_K = 10 rows {"label",
  "cooks", "leverage"}]}`. Labels are the matrix's index.
- A `table` artifact holds every row.

**`plot_residuals(fit)`**
- Residuals vs fitted, plus a normal QQ plot built from stdlib
  `statistics.NormalDist().inv_cdf`, not scipy.
- OLS uses `resid`; GLM and Logit use deviance residuals. PNG, pyplot-free, `parse_math=False`.

---

## 4. Test Suite Spec (~70)

- **Known answers:**
  - `fit_ols` on statsmodels' Longley data matches the **NIST certified** coefficients and
    standard errors to relative 1e-8, and R² as well. Hard-code these values in the test, citing
    NIST StRD "Longley". They were checked against statsmodels to about 1e-11 when this spec was
    written. Column order is const, GNPDEFL, GNP, UNEMP, ARMED, POP, YEAR.
    - coef: `-3482258.63459582, 15.0618722713733, -0.0358191792925910, -2.02022980381683,
      -1.03322686717359, -0.0511041056535807, 1829.15146461355`
    - SE: `890420.383607373, 84.9149257747669, 0.0334910077722432, 0.488399681651699,
      0.214274163161675, 0.226073200069370, 455.478499142212`
    - R² `0.995479004577296`; residual SD `304.854073561965`;
  - `fit_logit` on a hand-built 2×2 reproduces the exact log-odds and log odds-ratio to 1e-9 (P2);
  - Poisson GLM on simulated `exp(0.5 + 0.3x)` recovers coefficients within 3 SE.
- **Separation (P3):** a perfectly separated logit is `data`. Prove **both** paths:
  - under pytest (the warning raised as an exception);
  - with `converged=False` and no warning, by monkeypatching the fit to return a non-converged
    result.
- **Dummy coding:**
  - the default reference is the most frequent level, and an explicit one is respected;
  - the reference column is absent;
  - names and dtypes;
  - bool becomes 0/1;
  - numeric columns are unscaled (values identical);
  - NaN in a coded column is `tool_param`;
  - an unobserved reference level is `tool_param`.
- **Assemble:**
  - train+valid are concatenated, and test is impossible by signature;
  - overlapping labels are `tool_param`;
  - median/mode imputation uses the concatenation;
  - the indicator appears at a 5 % missing share and not at 4 %;
  - a duplicated column and a dummy-trap combination are dropped as collinear, with warnings;
  - no NaN in the output.
- **`coefficient_table`:**
  - roles are confirmatory, exploratory or intercept;
  - a three-level goal is one joint test with `df == 2`, and its p equals statsmodels'
    `wald_test` on the same restriction;
  - Holm across 3 goals equals `multipletests(method="holm")`;
  - `reject` uses the adjusted p;
  - one goal gives no Holm column;
  - a goal on a dropped column is `untestable` and excluded from Holm.
- **Diagnostics:**
  - VIF on Longley flags the known collinear columns (GNP, POP and YEAR, all above 10);
  - `test_residuals` on non-OLS is `tool_param`, and the statistics equal statsmodels' direct
    calls;
  - influence `top` is sorted by Cook's distance with matrix labels;
  - `plot_residuals` renders for all three model types.
- **Registry:** `SPECS` names and target types; `list_estimators` reports them; every
  `SPECS[...].tool` is a registered tool name.
- **End to end:** on `churn_frame` with an inference goal `plan → churned`, run
  `assemble_inference_matrix(train, valid)` → `fit_logit` → `coefficient_table` gives one
  confirmatory joint test with `df == 2`.
- Strict JSON on every result. One MCP round-trip of `fit_ols` (heavy).

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 75 s. Coverage **100 %** line and branch. Chunk 13/14 tests
      pass **without edits** (only the `list_estimators` assertions may gain the new key).
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - the reference level is not dropped;
  - per-term p-values are used instead of a joint Wald test;
  - Holm replaced by raw p for `reject`;
  - remove the `converged` check;
  - assemble imputes from train only;
  - the collinearity drop is removed;
  - exploratory terms are labelled confirmatory.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`. **No `statsmodels.formula` import anywhere.**
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

The NIST values disagree beyond 1e-8. **Do not edit the certified values**; report. Also stop if
P3–P7 do not reproduce, or anything needs `backend/` or `contracts/`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The end-to-end `coefficient_table` summary, verbatim.
4. Which Longley columns VIF flagged.
5. For chunk 16 (the skeleton gate): the exact tool sequence you would run for node 6 on the
   regression fixture.
