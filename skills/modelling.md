# Modelling policy

Use the confirmed contract. Fit only on training data; select on validation data.
Score the selected champion on test once. Return typed decisions and cite recorded evidence.

## intake

### Purpose
Load data and validate the agreed contract.

### Heuristics
TODO: Human author supplies domain heuristics.

### Tools
load_csv
infer_column_roles
validate_contract
detect_id_columns
detect_leaky_columns

### Outputs
Validated contract and registered input artifact.

### Pitfalls
TODO: Human author supplies domain pitfalls.

## problem_definition

### Purpose
Clarify the modelling objective within the confirmed contract.

### Heuristics
TODO: Human author supplies domain heuristics.

### Tools


### Outputs
Problem statement and task objectives.

### Pitfalls
TODO: Human author supplies domain pitfalls.

## data_profiling

### Purpose
Describe the input schema, distributions and data quality.

### Heuristics
TODO: Human author supplies domain heuristics.

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
TODO: Human author supplies domain pitfalls.

## data_splitting

### Purpose
Prepare one shared train, validation and locked test split.

### Heuristics
TODO: Human author supplies domain heuristics.

### Tools
drop_duplicate_rows
drop_high_missing_rows
cast_dtypes
drop_columns
split_random
split_stratified
verify_split

### Outputs
Split artifacts and verification report.

### Pitfalls
TODO: Human author supplies domain pitfalls.

## experiment_design

### Purpose
Declare candidates for comparison on the shared split.

### Heuristics
TODO: Human author supplies domain heuristics.

### Tools
list_estimators

### Outputs
Candidate specifications and rationale.

### Pitfalls
TODO: Human author supplies domain pitfalls.

## inferential_analysis

### Purpose
Fit the declared inferential analysis on training data.

### Heuristics
TODO: Human author supplies domain heuristics.

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
TODO: Human author supplies domain pitfalls.

## preprocessing

### Purpose
Fit candidate preprocessing on training data and apply fitted transforms.

### Heuristics
TODO: Human author supplies domain heuristics.

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
TODO: Human author supplies domain pitfalls.

## feature_engineering

### Purpose
Fit candidate feature transforms on training data and apply them.

### Heuristics
TODO: Human author supplies domain heuristics.

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

### Outputs
Fitted feature transforms and candidate feature frames.

### Pitfalls
TODO: Human author supplies domain pitfalls.

## model_training

### Purpose
Train or tune the declared candidate estimator.

### Heuristics
TODO: Human author supplies domain heuristics.

### Tools
list_estimators
train_estimator
tune_estimator
get_model_params

### Outputs
Fitted candidate estimator and training summary.

### Pitfalls
TODO: Human author supplies domain pitfalls.

## model_evaluation

### Purpose
Evaluate candidates using training and validation evidence.

### Heuristics
TODO: Human author supplies domain heuristics.

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
TODO: Human author supplies domain pitfalls.

## model_interpretation

### Purpose
Interpret candidate predictions and diagnostics.

### Heuristics
TODO: Human author supplies domain heuristics.

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
TODO: Human author supplies domain pitfalls.

## champion_selection

### Purpose
Select the champion using validation evidence, then score it on test once.

### Heuristics
TODO: Human author supplies domain heuristics.

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
TODO: Human author supplies domain pitfalls.

## artifact_persistence

### Purpose
Export and snapshot the completed run.

### Heuristics
TODO: Human author supplies domain heuristics.

### Tools
export_pipeline_script
snapshot_run

### Outputs
Reproducible pipeline and durable run snapshot.

### Pitfalls
TODO: Human author supplies domain pitfalls.

