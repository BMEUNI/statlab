---
status: completed
dependencies: [13, 15, 20]
completion_date: 2026-10-02
implementation_notes: |
  Shipped ridge (regression estimator registry line, alpha grid 0.1/1/10/100, interpretable,
  no class_weight) and mnlogit (SPECS line + fit_mnlogit + multinomial coefficient_table).
  Deferred split_temporal/split_grouped (D-c). Served tool count 83 (82 + fit_mnlogit).
  Index order confirmed and oracle-tested: params/bse/pvalues (p, K-1); conf_int() (K-1, p, 2)
  read as [j, t, :]; cov_params() equation-major (F-order), goal restriction built on t + j*p so
  df = terms x (K-1); goal chi2 equals a hand-built b' V^-1 b from cov_params() to 1e-9.
  Separation surfaces as a numpy exp overflow, caught by np.errstate(over/invalid/divide=raise)
  (thread-local; proven to fire in a worker thread) mapped to ToolDataError; no
  warnings.catch_warnings. FittedInference gained classes: tuple[str, ...] = () (default keeps old
  pickles loading). compute_influence/plot_residuals/test_residuals refuse mnlogit (tool_param)
  with a ponytail comment naming per-equation response residuals as the upgrade. All five DoD
  mutations caught by a named test. Pinned assertions changed: test_registry.py six->seven
  estimators (+ridge); test_tools_training.py list_estimators len 6->7; test_tools_inference.py
  SPECS set and inferential_models set (+mnlogit). Gates: 2095 passed/1 skipped, 64s; 100% line
  and branch on inference.py and registry.py; mypy clean 103 files; ruff clean. Not committed.
---

# Chunk 20r — Close the M7 scope gap: `ridge`, `mnlogit`; defer two splits

**Goal.** `PLAN.md` §6.4 lists six items that "follow at M7". Four were never scheduled. This
revision closes the gap.
- **`ridge`**: an estimator registry line.
- **`mnlogit`**: a `SPECS` line plus `fit_mnlogit`, and multinomial support in
  `coefficient_table`. Without it, a multiclass target with inference goals has no inferential
  model, which breaks D7's "every run with an inference goal fits exactly one".
- **`split_temporal` / `split_grouped`**: deferred out of v1 (D-c).

---

## 1. Prerequisites & Context

### State

Committed; see `git log -1`. 2077 tests (2076 passed, 1 skipped by design), 100 % line and
branch coverage, mypy clean on 101 files, suite ≈ 64 s.

Read first:
- `registry.py`;
- `tools/inference.py` (`FittedInference`, `fit_logit` and its separation handling,
  `coefficient_table`, `inferential_fit_summary`, `compute_influence`, `plot_residuals`,
  `_str_level_counts`);
- `tests/test_registry.py`, `tests/test_tools_inference.py`.

### Findings from probing (statsmodels 0.15.0, `-W error`, 2026-10-02)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | `MNLogit(y, X).fit(disp=0)`: `params` and `bse` are (p, K−1), `pvalues` is (p, K−1), `conf_int()` is **(K−1, p, 2)**, `cov_params()` is (p(K−1))² | Index carefully. Each equation is a non-base class |
| P2 | `cov_params()` order is `params.ravel(order="F")` (equation-major): a restriction matrix built on that order gives the same χ² from `wald_test(R, scalar=True)` and by hand (68.97 both) | A goal's joint test covers its terms in **every** equation: df = terms × (K−1) |
| P3 | Perfect separation does **not** warn as `PerfectSeparationWarning`: `exp` overflows, which is a `RuntimeWarning` (fatal under `-W error`), and without `-W error` it silently returns `converged=True` | Fit inside `np.errstate(over="raise", invalid="raise", divide="raise")`, which works in a worker thread (P4), and map `FloatingPointError` to `ToolDataError`. This is the same intent as `fit_logit`'s two-path handling |
| P4 | `np.errstate` raises in a non-main thread too, gives no false positives on a normal MNLogit or Logit fit, and string `endog` is accepted | `errstate` is thread-local, unlike `warnings.catch_warnings`, so it is allowed |
| P5 | Results pickle (`MultinomialResultsWrapper`); `prsquared`, `llr`, `llr_pvalue` and `aic` are available | Same storage as the other fits; the summary shares Logit's branch |

### Decisions this chunk makes

**D-a. The base class is chosen, not alphabetical.** statsmodels takes the first sorted label as
the base. `fit_mnlogit` instead encodes `y` as integer codes with the base first, so it controls
the choice. The base is `target.class_order[0]` from the snapshot when that is set and observed;
otherwise it is the most frequent class (ties broken by string order), matching chunk 15's
reference-level rule. Classes are compared through `as_str`/`as_str_scalar` (the B2 rule).
`FittedInference` gains `classes: tuple[str, ...] = ()`: base first, then the equation order. It
is empty for the other models, and the default keeps old pickles loading.

**D-b. Multinomial coefficient rows carry their equation.**
- Each row of `coefficient_table` gains `equation` (the non-base class; `null` for the other
  models).
- A goal's joint Wald covers its terms across all K−1 equations (P2). Holm and the roles are
  unchanged.
- `inferential_fit_summary` shares Logit's branch, plus `base_class`.
- `compute_influence`, `plot_residuals` and `test_residuals` refuse `mnlogit` with `tool_param`,
  because residuals have no single scale here. Mark this with a `ponytail:` naming
  per-equation response residuals as the upgrade.
- `compute_vif` reads the matrix only and works unchanged.

**D-c. `split_temporal` and `split_grouped` leave v1.** D25 already called them "~10 lines each
when needed", and node 4 (chunk 27a) escalates a temporal or grouped request as `data` instead
of falling back to random, so a user gets a clear refusal rather than a leaky split. The
catalogue marks them `[deferred]`. The v1 catalogue is **83 served tools**: 84 − 2 + `fit_mnlogit`.

**D-d. `ridge` is a linear, interpretable regressor.**
- `Ridge(alpha=1.0, random_state=seed)`, with `default_grid={"alpha": (0.1, 1.0, 10.0, 100.0)}`;
- `handles_missing=False`, `supports_class_weight=False`, `interpretable=True`.

It is a second linear family member for chunk 27b's two-per-family cap, and
`coefficient_importance` works on it unchanged.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| modify | `registry.py`: `ridge` builder and entry; `SPECS["mnlogit"] = InferentialEntry("fit_mnlogit", ("multiclass",))` |
| modify | `tools/inference.py`: `fit_mnlogit` (heavy, `allow_in` as the other fits); D-a field; the D-b branches |
| modify | `tests/test_registry.py`, `tests/test_tools_training.py`: the pinned six-estimator assertions become seven, and the pinned `SPECS` set gains `mnlogit`. **Only** those assertions change; list each one |
| modify | `tests/test_tools_inference.py` (or a new `tests/test_tools_inference_mnlogit.py`): §4 |
| modify | `PLAN.md`: §6.3 adds `fit_mnlogit`, marks the two splits `[deferred]`, header "83 tools (v1)"; §6.4's "follow at M7" sentence records what shipped and what was deferred; D25's note |
| modify | `00_index.md`: a row `20r` (completed) and the B5 row's gap note marked closed; `WHATS_NEXT.md`: replace the "Open scope gap" paragraph with a one-line resolution, and re-measure the state table |

No `backend/` or `contracts/` change.

---

## 3. Implementation Spec

**`fit_mnlogit(matrix, target, predictors: tuple[str, ...] | None = None)`** (heavy)
- It follows `fit_logit`'s structure: the matrix and constant handling, no formulas.
- The target must have ≥ 3 observed classes; 2 is `tool_param` ("use `fit_logit`").
- Fit under `np.errstate(...)` (P3). `FloatingPointError`, a `ConvergenceWarning` or
  `PerfectSeparationWarning` raised as an exception, or `converged == False` each become
  `ToolDataError("perfect separation or non-convergence: coefficients not identified")`.
- Saves `FittedInference(model="mnlogit", classes=..., positive_class=None, ...)`.
- Summary: the shared fit summary plus `base_class` and `classes`.

**`coefficient_table`** for `mnlogit`:
- One row per (equation, term). `stat` is z; the CI comes from `conf_int()[j, t, :]` (P1).
- Goal tests build R on the F-order flattening (P2).
- `n_terms` counts the distinct terms, not the rows.

---

## 4. Test Suite Spec (~25)

**Ridge.**
- It trains, predicts and tunes over its grid.
- With `alpha=1e-10` its coefficients equal `linear_regression`'s to 1e-6.
- At `alpha=100` the coefficient norm is smaller than at `alpha=0.1`.
- `coefficient_importance` works on it.

**mnlogit known answer.** Fit on a planted three-class frame, using the P1 probe's generator:
- `x1`'s effect is +0.8 in equation b and −0.6 in equation c;
- group dummy `g_b` is +1.0 in equation b; `g_c` is +1.2 in equation c;
- one pure-noise column.

Assertions:
- every planted coefficient is within ±0.15 absolute (§14.1's inference tolerance), with n
  large enough;
- the goal on `g` has df = 2 × 2 = 4 and rejects; the goal on noise is not rejected (§14.1:
  p > α);
- the goal statistic equals a hand-built χ² from `cov_params()` to 1e-9.

**Base class.**
- Default base = the most frequent class.
- `class_order` sets it, and a different base flips the signs of the corresponding equation's
  coefficients.
- A 0.0/1.0/2.0 float-coded target is handled through `as_str` (B2's class of bug).

**Errors.**
- A 2-class target is `tool_param`.
- A separated target is `ToolDataError` both under pytest's `filterwarnings=error` and with the
  errstate path exercised in a worker thread.
- `compute_influence`, `plot_residuals` and `test_residuals` on an mnlogit fit are `tool_param`.

**Summary.** `inferential_fit_summary` reports `pseudo_rsquared`, `llr_pvalue` and `base_class`.

**SPECS.** `list_estimators` reports `mnlogit` with tool `fit_mnlogit`.

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 80 s. Coverage **100 %** line and branch. Prior tests
      pass, with edits only where §2 names them.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - goal R built on C-order instead of F-order;
  - goal R covers the first equation only;
  - the base class is the alphabetical first;
  - the errstate guard is removed (the separated case must still fail somewhere: prove which
    test catches it);
  - CI read as `conf_int()[t, j]` (transposed).
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`, no `warnings.catch_warnings`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

P1–P4 do not reproduce; a chunk 15 test needs edits beyond §2; anything needs `backend/` or
`contracts/`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The planted-vs-fitted mnlogit coefficients and both goal rows.
4. The served tool count and the names (it must be 83).

---

## 6. Addendum — review corrections (2026-10-02)

**The errstate guard was only tested under pytest's warning rules.** Both separation tests
expected `ToolDataError`. With the guard removed, they still failed, but only because
`filterwarnings=error` turns numpy's overflow `RuntimeWarning` into an exception. In production
that warning is not fatal, and without the guard MNLogit silently returns `converged=True` with
diverged coefficients, which is P3's whole point.

Added `test_separated_target_is_tool_data_error_in_production`. It ignores `RuntimeWarning`, as
production does, so only the guard can raise. It passes on the code and fails with the guard
removed.
