# Modelling policy

Use the confirmed contract. Fit only on training data; select on validation data.
Score the selected champion on test once. Return typed decisions and cite recorded evidence.

## intake

### Purpose
Load data and validate the agreed contract.

### Heuristics
- Confirm target, task type, positive class and units against the agreed contract.
- Check that every feature exists at prediction time; treat ID and leakage flags as review evidence.
- Validate schema and contract before modelling proceeds.

### Tools
load_csv
infer_column_roles
validate_contract
detect_id_columns
detect_leaky_columns

### Outputs
Validated contract and registered input artifact.

### Pitfalls
- Do not silently change the target, task, units or positive class to fit the observed data.
- Do not treat an ID or leakage flag as proof; resolve uncertainty through typed escalation.

## problem_definition

### Purpose
Clarify the modelling objective within the confirmed contract.

### Heuristics
- Freeze the prediction objective, primary metric, direction and relevant error tradeoffs.
- Predeclare inferential hypotheses, predictors, controls and alpha before profiling.
- State whether each question is predictive or associational.

### Tools


### Outputs
Problem statement and task objectives.

### Pitfalls
- Do not invent missing domain facts or imply that association or prediction establishes causation.
- No tools are available in this node; carry unresolved contract questions forward for escalation.

## data_profiling

### Purpose
Describe the input schema, distributions and data quality.

### Heuristics
- Check schema, missingness, class balance, cardinality and feature/target distributions.
- Record units, plausible extremes, redundancy and target relationships as descriptive evidence.
- Disclose that profiling occurs before the split and uses the whole input.

### Tools
cast_dtypes
profile_schema
summarize_numeric
summarize_categorical
report_missingness
summarize_target
compute_correlations
detect_outliers
plot_distribution
plot_target_relation

### Outputs
Profile summaries and diagnostic plots.

### Pitfalls
- Whole-input profiling can influence later choices; do not describe it as train-only analysis.
- Correlations and outlier flags do not establish errors or causal relationships.
- Treat target-driven inference after profiling as exploratory.

## data_quality

### Purpose
Check data integrity before the split and apply only safe, reported fixes.

### Heuristics
- Read every check's status and name failing checks in the findings.
- Mark reported placeholder texts (such as "?" or "unknown") as missing when they clearly stand for a missing value; keep numeric sentinels unless the column cannot take that value.
- Merge spelling variants only when they clearly name the same category.
- Report duplicates, conflicting labels and rare levels; the split and preprocessing handle them.
- Columns empty where the target is missing are probably measured only afterwards: name them in the findings as likely leakage.
- A target that follows the row order means a random split overstates the scores: say so in the findings.

### Tools
check_data_quality
fix_data_quality

### Outputs
Quality report and, when fixes are chosen, the fixed frame the split uses.

### Pitfalls
- Never change the target or apply values the check did not report.
- The checks read the whole input before the split; describe them as such.

## data_splitting

### Purpose
Prepare one shared train, validation and locked test split.

### Heuristics
- Create one shared seeded split; stratify classification when supported.
- Use random splitting only for independent rows. Time-ordered rows need split.strategy temporal with split.time_column in the contract; flag temporal dependence when the contract has no such split. Grouped rows have no split yet: escalate.
- Remove only contract-approved, target-independent exact duplicates or high-missing rows before splitting; account for missing targets.
- Verify counts, overlap and duplicates; require at least 30 validation and test rows and 5 per class for classification.

### Tools
drop_duplicate_rows
drop_high_missing_rows
cast_dtypes
drop_columns
split_random
split_stratified
split_temporal
verify_split

### Outputs
Split artifacts and verification report.

### Pitfalls
- Never fall back to random splitting when rows are grouped or time-dependent.
- Do not remove rows after splitting or inspect test data to alter the split.
- Do not proceed when the minimum validation/test counts fail.

## experiment_design

### Purpose
Declare candidates for comparison on the shared split.

### Heuristics
- Declare up to max_candidates (normally 3–5), including a cheap, interpretable baseline.
- Tie candidates to profiling evidence; diversify supported families and preprocessing, with at most two per family unless justified.
- Specify transformations, estimator, parameters and bounds; deduplicate candidate specifications.
- Use list_estimators as the authority on available models.

### Tools
list_estimators

### Outputs
Candidate specifications and rationale.

### Pitfalls
- Do not claim a universally best model or propose unsupported ensembles, search methods, resampling or libraries.
- Do not exceed the candidate limit or leave candidate settings ambiguous.

## inferential_analysis

### Purpose
Fit the declared inferential analysis once on train+valid, never test.

### Heuristics
- Use the shared train+valid inference matrix with median/mode imputation and explicit reference levels; remove collinearity only, without predictive scaling or selection.
- Match model to target: OLS for continuous, Logit for binary, MNLogit for multiclass and Poisson GLM for counts.
- Review convergence, VIF, residual and influence diagnostics; report assumption limits and overdispersion.
- Use coefficient_table's joint tests for predeclared goals; compare its Holm-adjusted p-value (raw p-value for a single test) with goal alpha, and report p-values, effects and intervals.

### Tools
build_dummy_matrix
assemble_inference_matrix
fit_ols
fit_logit
fit_mnlogit
fit_glm
coefficient_table
inferential_fit_summary
compute_vif
test_residuals
compute_influence
plot_residuals

### Outputs
Inferential model, coefficients and diagnostics.

### Pitfalls
- Profiling-driven hypotheses are exploratory; do not p-hack or equate significance with effect size.
- Do not make causal claims or promise unsupported robust/negative-binomial corrections.
- Check convergence and diagnostics before interpreting coefficients.

## preprocessing

### Purpose
Fit candidate preprocessing on training data and apply fitted transforms.

### Heuristics
- Make column decisions and fit imputation/clipping on candidate training data, then apply the frozen transforms to validation.
- Use contract-compatible median/mode imputation; disclose all-missing and constant columns and missingness.
- Clip plausible extremes only with a stated rationale; defer test transforms until champion selection.

### Tools
drop_constant_columns
drop_high_missing_columns
fit_imputer
fit_outlier_clipper
drop_columns
apply_imputer
apply_outlier_clipper

### Outputs
Fitted transforms and prepared candidate frames.

### Pitfalls
- Do not delete rows automatically, impute the target, or refit transforms on validation or test.
- Match missing-value handling to the chosen estimator; do not assume every estimator accepts missing values.

## feature_engineering

### Purpose
Fit candidate feature transforms on training data and apply them.

### Heuristics
- One-hot encode nominal features; use ordinal encoding only when the contract defines a real order.
- Scale regularized linear models when appropriate; consider target encoding only for justified high-cardinality non-ID features.
- Use the encoder's out-of-fold training output and apply the fitted encoder to held-out rows.
- Fit binning and selection on training data; require available-at-prediction-time inputs and domain rationale for derived features.

### Tools
fit_onehot_encoder
apply_onehot_encoder
fit_ordinal_encoder
apply_ordinal_encoder
fit_target_encoder
apply_target_encoder
fit_scaler
apply_scaler
add_datetime_parts
add_interaction_terms
add_ratio_feature
fit_binner
apply_binner
select_by_variance
select_by_mutual_info
drop_columns

### Outputs
Fitted feature transforms and candidate feature frames.

### Pitfalls
- Do not replace out-of-fold target-encoded training values with in-sample encoded values.
- Preserve target, row indices and order; ensure ratio denominators are finite and nonzero.
- Do not invent unavailable missing-indicator behavior or derive features unavailable at prediction time.

## model_training

### Purpose
Train or tune the declared candidate estimator.

### Heuristics
- Train on training data only; use bounded small grids when tuning adds value and report evaluated versus total points and budget warnings.
- Respect contract CV folds, the reproducible seed and n_jobs=1.
- Calibrate only through the supported train_estimator path, which uses training-internal CV.
- Treat validation as the authority for candidate selection.

### Tools
list_estimators
train_estimator
tune_estimator
get_model_params

### Outputs
Fitted candidate estimator and training summary.

### Pitfalls
- tune_estimator cross-validates on the prepared frame; it does not refit learned preprocessing or selection inside folds.
- CV can be optimistic, especially after target encoding or selection; do not call it leakage-free pipeline CV or nested unbiased evaluation.
- Do not calibrate on validation or claim unsupported search methods.

## model_evaluation

### Purpose
Evaluate candidates using training and validation evidence.

### Heuristics
- Compare candidates on identical validation rows with the contract metric and direction.
- Inspect class balance, probability quality and relevant confusion, ROC or precision-recall diagnostics; align labels and probabilities.
- For classification, use pr_auc (average precision, macro for multiclass) when minority share is below 0.1; otherwise use roc_auc unless an audited override applies.
- Choose and freeze any threshold on validation for the stated objective; report RMSE and MAE in target units, and unitless R2.

### Tools
predict
predict_proba
get_model_params
score_classification
score_regression
compute_confusion_matrix
compute_roc_curve
compute_pr_curve
compute_calibration_curve
select_threshold
plot_curve
cross_validate_estimator
compute_learning_curve
compare_models

### Outputs
Validation metrics and diagnostic artifacts.

### Pitfalls
- Search and threshold tuning make validation selection scores optimistic; do not use test diagnostics.
- Do not describe average precision as trapezoidal PR area or invent percentage metrics.
- Use training-only CV and learning curves as diagnostics, not as a replacement for validation selection.

## model_interpretation

### Purpose
Interpret candidate predictions and diagnostics.

### Heuristics
- Confirm candidate quality before interpreting; use validation permutation importance and error analysis.
- Inspect supported slices with counts and describe uncertainty for small groups.
- Explain coefficient dependence on encoding/scale and permutation importance limits under correlated predictors.

### Tools
predict
predict_proba
select_threshold
plot_curve
permutation_importance
coefficient_importance
partial_dependence
analyze_errors

### Outputs
Model interpretation and error analysis.

### Pitfalls
- Correlated inputs can mask permutation importance; partial dependence can show implausible feature combinations.
- Do not infer causality or certainty from tiny slices.
- skip_reason skips the whole step (no importance, partial dependence or error analysis); to leave out coefficients for a non-linear model keep coefficients false.
- Any validation-driven change is additional search; never inspect test to revise a candidate.

## champion_selection

### Purpose
Select the champion using validation evidence, then score it on test once.

### Heuristics
- Select only from validation evidence, satisfying hard constraints; prefer a simpler comparable model, not a model solely for contended fit wall-clock time.
- Report the winner, runner-up and selection history; use returned seeded 1000-resample percentile 95% bootstrap intervals and describe overlap as not distinguishable on this validation set.
- Freeze champion, transforms and threshold, apply frozen operations to test, then call exactly one task score tool.
- Report prior fingerprint exposure and qualify repeated test estimates; use returned evidence for reporting.

### Tools
rank_models
select_champion
drop_columns
apply_imputer
apply_outlier_clipper
apply_onehot_encoder
apply_ordinal_encoder
apply_target_encoder
apply_scaler
predict
predict_proba
get_model_params
score_classification
score_regression
compute_confusion_matrix
compute_roc_curve
compute_pr_curve
compute_calibration_curve
select_threshold
plot_curve
compare_models
add_datetime_parts
add_interaction_terms
add_ratio_feature
apply_binner
render_metrics_table
assemble_report

### Outputs
Champion identity, final test metrics and report.

### Pitfalls
- Never fit or refit on train plus validation, select a threshold on test, or run a second test score.
- Do not inspect test diagnostics or change the winner based on test results, even if a tool is listed here.
- Overlapping bootstrap intervals are descriptive, not a formal equality test; repeated exposure weakens test estimates.

## artifact_persistence

### Purpose
Export and snapshot the completed run.

### Heuristics
- Export the actual successful pipeline and snapshot the run using existing tools.
- Preserve artifact IDs and order, contract, seeds, versions, skill snapshot hash, lineage, exposure and limitations.
- Cite actual tool outputs and artifacts in the record.

### Tools
export_pipeline_script
snapshot_run

### Outputs
Reproducible pipeline and durable run snapshot.

### Pitfalls
- Do not claim evidence or replay that was not produced; never include secrets or evaluate arbitrary formulas.
- Do not load untrusted pickle artifacts or imply cross-version or deployment-distribution guarantees.
- Snapshots are immutable; edits affect future unsnapshotted runs only.

