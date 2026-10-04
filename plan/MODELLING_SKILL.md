---
status: complete
dependencies: [26]
completion_date: 2026-10-04
implementation_notes: User-authorized policy completion and Flash defaults; focused checks passed before user stopped tests. Full acceptance timed out, not accepted; see durable receipt.
---

# Modelling skill completion — 2026-10-04

## Goal and authority

Replace all 26 Heuristics/Pitfalls placeholders in `skills/modelling.md` with practical,
research-informed modelling guidance. User explicitly requested agent authorship on 2026-10-04,
overriding D12's previous human-only procedure. PLAN remains architecture authority; D12's
format/tool enforcement and D28/D32–D35 remain unchanged. This is an adjunct policy task,
not completion of B9 or a new numbered chunk.

## Prerequisites and reuse

Reuse committed B8 (`276cf36`), the validated skill loader, served tool registry and immutable
run snapshots. Existing `tests/test_skills.py` covers real registry membership, headings,
node isolation, snapshots/restarts and publication failures. No new parser guards, tests,
dependencies or compute APIs are needed. Runtime policy content changes require the focused
loader/registry check; accepted B8 source evidence remains valid for unchanged code.

## Owned files

Implementer owns `skills/modelling.md` and the existing policy-file assertion in
`tests/test_skills.py` (scope amendment after a demonstrated focused failure).
Main owns this spec, PLAN authorship amendment,
WHATS_NEXT stale placeholder references and the durable receipt. Preserve dirty work.

Follow-up ownership: implementer owns the one-line `backend/statlab/llm/routing.py` reasoning
default change to `deepseek/deepseek-flash`. User requests DeepSeek-V4.1-Flash for all roles and
explicitly stops tests. Preserve role/override precedence, reasoning-mode behavior and existing
Flash pricing/capability handling. No local env edit: safe inspection found no role overrides.
Official [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing/) map the
version to API name `deepseek-flash` and confirm thinking support (verified 2026-10-04).

## Frozen behavior

Preserve the exact 13 node names, all required headings, shared preamble, Purpose/Outputs text,
and every Tools section (including empty problem_definition tools). No Model overrides,
extra node/reference headings or permissions. Replace every TODO with concise actionable
bullets: roughly 3–5 heuristics and 2–4 pitfalls per node; clarity takes precedence over counts.
Keep all methods within existing tools and contract constraints. Sources below inform judgement;
project thresholds and decisions are explicitly project policy, not universal research findings.

- **intake:** verify target/task/positive class/units and prediction-time feature availability;
  treat detected IDs/leakage as evidence for review rather than automatic proof; reject invalid
  contract/data with typed escalation rather than quietly changing the modelling question.
- **problem_definition:** freeze prediction objective, metric/direction and false-positive/negative
  tradeoffs; pre-specify inference hypotheses, predictors, controls and alpha before profiling;
  distinguish association/prediction from causation. No tools here. Do not fabricate missing facts.
- **data_profiling:** schema, missingness, class balance, cardinality and distribution checks;
  record units, plausible extreme values, redundancy and target relations as descriptive evidence.
  EDA is before split in this spine: disclose whole-input influence, never call it train-only EDA;
  later target-driven inference is exploratory. Correlation/outlier flags do not prove causation/errors.
- **data_splitting:** one shared seeded split; stratify classification when supported; random split
  only for independent rows. Temporal/grouped dependency requires data escalation (unsupported v1),
  never a random fallback. Pre-split exact duplicate/high-missing row removal is contract-approved
  and target-independent; account for missing-target exclusions. Verify overlap/duplicates/counts;
  require valid/test >=30 rows and >=5/class for classification (D33d); no row removal after split.
- **experiment_design:** choose 3–5 candidates within max_candidates, include cheap interpretable
  baseline, justify each from EDA, diversify supported families and preprocessing; <=2/family unless
  explicit D28 justification. Fully specify transformations, estimator, params, bounds/constraints;
  deduplicate specifications. Served list_estimators is authority; no universal best model or
  unsupported ensembles, random/Bayesian search, resampling or external libraries.
- **inferential_analysis:** shared train-only canonical inference matrix (median/mode imputation,
  explicit reference dummy levels, no predictive scaling/selection except collinearity removal).
  OLS continuous, Logit binary, MNLogit multiclass, Poisson GLM count; check returned diagnostics,
  convergence, VIF, residuals and influence; report limitations for overdispersion or violations.
  Coefficient table supplies predeclared-goal joint tests and Holm adjustments; use adjusted p at
  goal alpha, show raw/adjusted p and effect intervals. Post-EDA changes exploratory; no p-hacking,
  significance-as-effect-size or causal claims. Do not propose unsupported robust/negative-binomial
  correction as available tool behavior.
- **preprocessing:** candidate-local train-fitted column decisions, imputation/clipping then apply
  same frozen artifacts to valid; defer test transforms until champion. Median/mode reasonable
  baseline subject to contract; all-missing/constant columns and missingness carefully disclosed.
  Extreme plausible observations need rationale for clipping; no automatic row deletion, target
  imputation or re-estimation on valid/test. Match actual estimator missing-value support.
- **feature_engineering:** one-hot nominal moderate-cardinality, ordinal only true contract order;
  scale regularized linear models when appropriate; consider compact target encoding only justified
  high-cardinality non-ID features. Use fit_target_encoder's OOF train output, apply encoder only
  to heldout; do not replace OOF train with in-sample application. Train-only binning/selection;
  interactions/ratios/datetime features need prediction-time availability and domain rationale;
  preserve target/indices/order and finite denominators. Do not invent missing-indicator API.
- **model_training:** train only; bounded small supported grids when useful, record evaluated/total
  points and warnings/budget truncation; respect contract CV folds and reproducible seed, n_jobs=1.
  Calibrate only through supported train_estimator contract path (train-internal CV), never on valid.
  Current tune_estimator cross-validates an estimator on an already prepared frame: it does NOT
  refit learned preprocessing/selection inside folds. CV can be optimistic, especially with target
  encoding/selection; do not label it leakage-free pipeline CV or nested unbiased evaluation.
  Validation remains selection authority. No unsupported search algorithms or suppressed warnings.
- **model_evaluation:** compare identical validation rows and contract metric/direction; classification
  inspect balance, probability quality, confusion/PR/ROC as appropriate; D33 default pr_auc when
  minority_share<0.1 otherwise roc_auc unless audited override. pr_auc here is average precision
  (macro for multiclass), not trapezoidal PR area. Confirm label/probability alignment; threshold
  chosen on validation for stated objective then frozen. Regression RMSE/MAE/R2 convey distinct
  errors and units; no percentage metrics invented. Train-only CV/learning curves diagnostic;
  validation selection scores optimistic after search and threshold tuning. No test diagnostics.
- **model_interpretation:** validate model quality before interpreting, use validation permutation
  importance/error analysis; coefficients depend on encoding/scale, correlated predictors can mask
  permutation importance; PDP on correlated features can depict unsupported combinations. Diagnose
  supported slices with counts, document limitations, no tiny-slice certainty or causal conclusions.
  Any validation-driven changes count as further search; never inspect test to revise a candidate.
- **champion_selection:** select exclusively validation; respect hard constraints, prefer simpler
  model when comparable (not contended wall-clock), report winner/runner-up and selection history.
  Use returned 95% bootstrap CIs (project policy: 1000 seeded percentile resamples); overlap phrase
  'not distinguishable on this validation set' is descriptive policy, not a formal equality test.
  Freeze champion/transforms/threshold, apply frozen operations to test, call exactly ONE task
  score tool. No fitting/refitting on train+valid, second score, test-based threshold or diagnostics
  even when listed Tools permits a name. After score use returned evidence/reporting artifacts only.
  Report prior fingerprint exposure and qualify repeated test estimates; never change winner on test.
- **artifact_persistence:** export actual successful pipeline and snapshot, preserve fitted artifact
  IDs/order, contract/seeds/versions/skill snapshot hash/lineage/exposure/limitations in records through
  existing tools. Reference actual outputs, no invented evidence/replay claim; no secrets in artifacts,
  no arbitrary formula evaluation or untrusted pickle. Snapshots are immutable; edits apply to future
  unsnapshotted runs. Do not claim cross-version or deployment-distribution performance guarantees.

## Research evidence (primary sources, consulted 2026-10-04)

These are guidance sources, not authorization to extend tool capabilities. Current sklearn stable
docs report 1.9.1 while the repository pins 1.9.0; only established concepts matching inspected tools
are used. Statsmodels documentation and pin both report 0.15.0. No dependency change.

| Source | Applied guidance |
|---|---|
| [sklearn common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html) | Train-only learned transforms; CV preprocessing limitation; reproducibility |
| [sklearn cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html) | Independent observations and split design; selection vs generalization |
| [sklearn preprocessing](https://scikit-learn.org/stable/modules/preprocessing.html) | Scaling, nominal/ordinal encoding choices |
| [sklearn imputation](https://scikit-learn.org/stable/modules/impute.html) | Missing-value strategies and estimator support; no unavailable indicator tool |
| [sklearn target cross fitting](https://scikit-learn.org/stable/auto_examples/preprocessing/plot_target_encoder_cross_val.html) | OOF training encoding differs from fit then transform |
| [sklearn parameter search](https://scikit-learn.org/stable/modules/grid_search.html) | Budget-aware search; implemented bounded grid only |
| [sklearn metrics](https://scikit-learn.org/stable/modules/model_evaluation.html) | Metric choice/units, AP definition, averaging |
| [sklearn thresholds](https://scikit-learn.org/stable/modules/classification_threshold.html) | Separate probabilities from decisions; heldout threshold tuning |
| [sklearn calibration](https://scikit-learn.org/stable/modules/calibration.html) | Calibrator needs predictions held out from base-estimator fitting |
| [sklearn permutation importance](https://scikit-learn.org/stable/modules/permutation_importance.html) | Evaluate quality first; correlated feature masking |
| [sklearn partial dependence](https://scikit-learn.org/stable/modules/partial_dependence.html) | Correlated inputs can generate implausible combinations |
| [statsmodels pitfalls](https://www.statsmodels.org/stable/pitfalls.html) | Rank deficiency, convergence and separation limits |
| [statsmodels multiple testing](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html) | Holm is distinct from Holm–Sidak and unadjusted tests |
| [ASA p-value statement](https://www.amstat.org/docs/default-source/amstat-documents/p-valuestatement.pdf) | Effect size, uncertainty and full reporting; no p-value-only scientific conclusions |

## Checks and stop conditions

Worker: `make test-fast TESTS=tests/test_skills.py`, no coverage, existing 900-second wrapper.
Correct the stale scaffold assertion to require nonempty completed Heuristics/Pitfalls without
TODO, preserving registry/snapshot coverage; rename that test to describe the completed policy.
Run scoped Ruff check/format and mypy for `tests/test_skills.py`. No new tests.
Check diff whitespace, no remaining TODO, preserved non-Heuristics/Pitfalls sections and exact
Tools text. Do not add tests that merely copy prose. No package Python source, dependency/configuration,
new methods, permission widening, commits or plan edits. Stop at focused green terminal handoff:
changed files, base commit, skill SHA256, commands, actual exits, summaries and unique log paths.

Main: consolidate content/capability review, check planning links/diff, then one stable acceptance
gate if treating changed runtime skill text as semantic product input. Record actual outcomes in
`plan/receipts/modelling-skill-2026-10-04.md`; no paid live LLM calls. No new mutation probes required.

**User override, 2026-10-04:** stop all tests. The preceding full acceptance attempt exited 2
after its pytest subprocess exceeded 900 seconds; it produced no final coverage result and did
not reach global static targets. Do not retry it or run checks for the model-default follow-up.
Task completion means the requested reviewed policy/model edits are delivered; full acceptance
remains unproved. Reuse only the recorded focused evidence and unchanged accepted B8 inputs.

## Consolidated review corrections

Initial focused execution demonstrated that `test_scaffold` requires TODO content; this test-only
historical expectation must change to completed policy content. Loader/Pydantic guarantees and
registry assertions already cover shape/permissions; no new guard or prose-specific tests.
Content review: validate before modelling, not before load_csv registers its required artifact;
consume coefficient_table's joint goal tests and adjusted significance; distinguish unitless R2;
make candidate cap win over the normal 3–5 shortlist; record seeded 1000-resample policy and
exclude contended fit wall-clock from champion preferences. Worker owns these corrections.
