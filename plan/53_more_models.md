---
status: completed
dependencies: [52]
completion_date: 2026-10-09
implementation_notes: "fifteen estimators (7 classifiers, 8 regressors); design picks models that suit the data; make check 100%; npm 91/91"
---

# Chunk 53 — more models for classification and regression (D6/D25 addendum 53)

User request 2026-10-09: add more statistical models for regression and classification, and
multiclass if not too hard; in automatic mode the agent picks the models that suit the data,
in guided mode it recommends them.

Already true (no work): multiclass classification is supported end to end (D6; every
classifier, `mnlogit` inference, offline S12 passes). Experiment design already proposes 3-5
registry estimators from profiling evidence; guided mode approves that shortlist once (46c).

Decision — eight new registry estimators (sklearn 1.9.0, all multiclass-capable):

| name | task | family | build | interpretable | class_weight | default_grid |
|---|---|---|---|---|---|---|
| `gaussian_nb` | classification | `naive_bayes` | `GaussianNB()` | no | no | `var_smoothing: (1e-9, 1e-7, 1e-5)` |
| `lda` | classification | `discriminant` | `LinearDiscriminantAnalysis(solver="lsqr")` | no | no | `shrinkage: (None, "auto")` |
| `extra_trees_classifier` | classification | `extra_trees` | `ExtraTreesClassifier(n_estimators=200, n_jobs=1, random_state=seed, class_weight=...)` | no | yes | `max_depth: (None, 5, 10)`, `min_samples_leaf: (1, 5)` |
| `decision_tree_classifier` | classification | `decision_tree` | `DecisionTreeClassifier(random_state=seed, class_weight=...)` | no | yes | `max_depth: (3, 5, 10, None)`, `min_samples_leaf: (1, 5, 20)` |
| `lasso` | regression | `linear` | `Lasso(alpha=1.0, max_iter=5000, random_state=seed)` | yes | no | `alpha: (0.001, 0.01, 0.1, 1.0)` |
| `elastic_net` | regression | `linear` | `ElasticNet(alpha=1.0, l1_ratio=0.5, max_iter=5000, random_state=seed)` | yes | no | `alpha: (0.001, 0.01, 0.1, 1.0)`, `l1_ratio: (0.2, 0.5, 0.8)` |
| `extra_trees_regressor` | regression | `extra_trees` | `ExtraTreesRegressor(n_estimators=200, n_jobs=1, random_state=seed)` | no | no | `max_depth: (None, 5, 10)`, `min_samples_leaf: (1, 5)` |
| `decision_tree_regressor` | regression | `decision_tree` | `DecisionTreeRegressor(random_state=seed)` | no | no | `max_depth: (3, 5, 10, None)`, `min_samples_leaf: (1, 5, 20)` |

`handles_missing=False` for all eight (imputation stays the contract's job). LDA uses `lsqr`
because the default `svd` solver warns on collinear one-hot columns. Lasso and elastic net are
linear with a 1-d `coef_` and scalar `intercept_`, exactly like `ridge`, so they are
interpretable and get coefficient importance. Extra trees fit their final model on threads
like random forests (D17-50). Skipped: SVM and k-NN (quadratic cost on 60k+ rows), Huber
(needs a new `epsilon` kind).

The design question gains one sentence asking the agent to pick estimators that suit the data
rather than every family.

## Stage 53a — registry, vocabulary, training

Owned: `contracts/statlab_contracts/vocabulary.py`, `mcp_server/statlab_tools/registry.py`,
`mcp_server/statlab_tools/tools/training.py`, `tests/test_registry.py`,
`tests/test_contract_vocabulary.py`, `tests/test_tools_training.py`. Preserve other edits.

1. `vocabulary.py`:
   - `TunableKind` gains `"unit_interval"` and `"shrinkage"` (after `"bool"`, before
     `"max_features"`).
   - `TUNABLE_PARAM_KINDS` gains `"var_smoothing": "positive_number"`,
     `"l1_ratio": "unit_interval"`, `"shrinkage": "shrinkage"`.
   - `_matches_kind` gains two cases before `case _:`:
     `case "unit_interval": return _unit(value)` and
     `case "shrinkage": return value is None or value == "auto" or _unit(value)`, with a new
     helper above `_matches_kind`:
     `def _unit(value: ParamValue) -> bool:` returns `not isinstance(value, bool) and
     isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1`.
   - `TUNABLE_PARAMS` gains:
     `gaussian_nb: {"var_smoothing"}`; `lda: {"shrinkage"}`;
     `extra_trees_classifier` and `extra_trees_regressor`: the same six keys as the random
     forests; `decision_tree_classifier` and `decision_tree_regressor`:
     `{"max_depth", "min_samples_split", "min_samples_leaf", "max_features", "max_leaf_nodes"}`;
     `lasso: {"alpha", "fit_intercept", "tol", "max_iter"}`;
     `elastic_net: {"alpha", "l1_ratio", "fit_intercept", "tol", "max_iter"}`.
2. `registry.py`:
   - `EstimatorEntry.family` Literal adds `"naive_bayes"`, `"discriminant"`, `"extra_trees"`,
     `"decision_tree"`. Docstring: `interpretable` is true only for the linear models with
     plain coefficients (`logistic_regression`, `linear_regression`, `ridge`, `lasso`,
     `elastic_net`).
   - In `_build_estimators`, import the new classes lazily alongside the existing imports
     (`ExtraTreesClassifier`, `ExtraTreesRegressor` from `sklearn.ensemble`; `Lasso`,
     `ElasticNet` from `sklearn.linear_model`; `GaussianNB` from `sklearn.naive_bayes`;
     `LinearDiscriminantAnalysis` from `sklearn.discriminant_analysis`;
     `DecisionTreeClassifier`, `DecisionTreeRegressor` from `sklearn.tree`). Add a builder per
     table row (same signature `(seed: int, class_weight: ClassWeight) -> object`; builders
     without `random_state`/`class_weight` ignore those arguments like `linear_regression`),
     and an `EstimatorEntry` per row with the table's task, family, grid,
     `handles_missing=False`, `supports_class_weight` and `interpretable`. Add
     `_grid_tree: Grid = {"max_depth": (3, 5, 10, None), "min_samples_leaf": (1, 5, 20)}` next
     to the other grids; extra trees reuse `_grid_rf`. Classifiers listed after
     `hist_gb_classifier`, regressors after `hist_gb_regressor`.
   - Module docstring: "six estimators, three per task" becomes "fifteen estimators (seven
     classifiers, eight regressors)".
3. `training.py`: `threaded = entry.family == "random_forest" and not calibrated` becomes
   `threaded = entry.family in ("random_forest", "extra_trees") and not calibrated`; the comment
   above says "forest trees" -> "forest and extra-trees trees".
4. `tests/test_registry.py`:
   - `test_exactly_the_six_d25_names_with_tasks` renamed
     `test_registry_names_tasks_and_families`; the expected name set, task map and family map
     include all fifteen per the table.
   - `test_every_forest_is_single_threaded` also covers `extra_trees_classifier` and
     `extra_trees_regressor`.
   - `test_handles_missing_and_class_weight_flags`: add asserts that every new name has
     `handles_missing is False`; `extra_trees_classifier` and `decision_tree_classifier` have
     `supports_class_weight is True`; `gaussian_nb` and `lda` have it `False`.
   - New `test_new_linear_models_are_interpretable_and_lda_uses_lsqr`:
     `lasso`/`elastic_net` `interpretable is True`; the other six new ones `False`;
     `_params(estimators()["lda"].build(0, "none"))["solver"] == "lsqr"`.
5. `tests/test_contract_vocabulary.py`: new test `test_unit_interval_and_shrinkage_kinds`:
   `tunable_param_errors({"l1_ratio": v}, frozenset({"l1_ratio"})) == []` for `0`, `0.5`, `1`;
   non-empty for `1.5`, `-0.1`, `True`, `"auto"`. `tunable_param_errors({"shrinkage": v},
   frozenset({"shrinkage"})) == []` for `None`, `"auto"`, `0.3`; non-empty for `"sqrt"`, `1.2`,
   `False`. (Import `tunable_param_errors` if the file does not already.)
6. `tests/test_tools_training.py`: new parametrized test
   `test_added_estimators_fit_and_predict(run_paths, name)` over the eight new names. For a
   classifier: for each frame in `(_separable_binary(), _multiclass_frame())`:
   `ref = _save(run_paths, frame)`; `res = train_estimator(run_paths, ref, target="y",
   name=name, seed=1, params=_FAST_RF if name.startswith("extra_trees") else {})`; `proba =
   _loaded(run_paths, predict_proba(run_paths, res.artifacts[0].ref, ref))`; assert
   `len(proba) == len(frame)`. For a regressor: `_regression_frame()`, `predict` instead of
   `predict_proba`, same length assert. Decide classifier vs regressor with
   `estimators()[name].task` (import `estimators` from `statlab_tools.registry` if needed).

Checks:
`make test-fast TESTS="tests/test_registry.py tests/test_contract_vocabulary.py tests/test_tools_training.py tests/test_tools_interpretation.py tests/test_tools_champion.py"`;
Ruff check, Ruff format --check and mypy on the six owned files.
Stop and report on any sklearn warning-as-error, unknown param, or a test elsewhere that pins
the estimator list.

## Stage 53b — design prompt, coefficients, labels (after 53a)

Owned: `backend/statlab/domains/design_inference.py`,
`backend/statlab/domains/candidate_models.py`, `frontend/src/run/model.ts`,
`tests/test_tools_interpretation.py`, `tests/test_nodes_design_inference.py`.

1. `design_inference.py` design `question`: after "...propose normally 3-5 reasoned candidates
   within max_candidates. " insert
   "Pick the estimators that suit this data (rows, linearity, collinearity, missingness, class
   balance, number of classes) from the registry; you need not use every family. "
2. `candidate_models.py` ~558: the coefficient-capable tuple becomes
   `("linear_regression", "ridge", "lasso", "elastic_net", "logistic_regression")`.
3. `model.ts` `ESTIMATOR_LABELS` adds: `gaussian_nb: 'Naive Bayes'`,
   `lda: 'Linear discriminant analysis'`, `extra_trees_classifier: 'Extra trees'`,
   `decision_tree_classifier: 'Decision tree'`, `lasso: 'Lasso regression'`,
   `elastic_net: 'Elastic net'`, `extra_trees_regressor: 'Extra trees'`,
   `decision_tree_regressor: 'Decision tree'`.
4. `tests/test_tools_interpretation.py`: new parametrized test
   `test_coef_lasso_and_elastic_net(run_paths, name)` over `("lasso", "elastic_net")`:
   `frame = _regression_frame(noise=0.01)`; `ref = _save(run_paths, frame)`;
   `model = _fit(run_paths, frame, name)`; `res = coefficient_importance(run_paths, model, ref)`;
   assert the coefficients table has one row per feature (`{"x1", "x2"}` as the set of
   `feature` values).
5. `tests/test_nodes_design_inference.py`: in the test that inspects the design question text
   (search for `"Include an interpretable linear baseline"`), also assert
   `"you need not use every family"` is in it. If no test inspects the question, stop and
   report.

Checks: `make test-fast TESTS="tests/test_tools_interpretation.py tests/test_nodes_design_inference.py tests/test_nodes_candidate_models.py"`;
Ruff check, Ruff format --check and mypy on the four Python files; from `frontend/`:
`npm test`, `npm run typecheck`, `npm run build`.

Main then: PLAN D6 addendum, review, `make check`, commit.

## Addendum 53a-A (pinned list in test_tools_training)

`tests/test_tools_training.py::test_list_estimators_all_and_filtered` pins the old registry.
Update it (owned by 53a): `len(allo) == 15`; the classification set is the seven classifiers
(`logistic_regression`, `random_forest_classifier`, `hist_gb_classifier`, `gaussian_nb`, `lda`,
`extra_trees_classifier`, `decision_tree_classifier`); the family map lists all fifteen names
with the families from the decision table (existing seven unchanged). The `supports_proba`
asserts stay. Then rerun the 53a focused selector and the three static checks; let Ruff format
wrap long lines.

## Addendum 53b-A (no question-text test)

No test inspects the design question text, and the change is one static string (no new
branch). Step 5 is dropped; `tests/test_nodes_design_inference.py` stays untouched. Run the
53b checks as written (the selector still includes that file as a regression run).

## Acceptance receipt (2026-10-09)

53a (after Addendum 53a-A): focused pytest (registry, vocabulary, training, interpretation,
champion) exit 0; Ruff check/format and mypy on the six files exit 0; the eight new estimators
fit and predict on binary, multiclass and regression frames with no sklearn warning.
53b (after Addendum 53b-A): focused pytest exit 0 (102); Ruff/format/mypy exit 0; npm 91/91,
typecheck 0, build 0. Main reviewed both diffs and ran `make check` exit 0 on 53a+53b: coverage
100 %, mypy 195 files, Ruff and format clean (log `check53.log`, temporary). Live behaviour of
the wider shortlist is the user's next online run.
