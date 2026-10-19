---
status: completed
dependencies: [82]
completion_date: 2026-10-19
implementation_notes: "profiling drops a correlation request without two varying numeric columns, and plots of entirely empty columns"
---

# Chunk 83 — a one-column correlation request never fails profiling

User report 2026-10-19: run `r_9b8dd729df2dcd69` (project `p_31590ba8257df79d`, loan default
data, 255,347 rows, guided) stopped at Data profiling. The profiling gate was approved; the
re-executed step then failed and opened a failure gate (`abort`/`rewind`/`skip_node`), and the
user aborted.

Cause (main, read-only, from `gates.question_json`): the `ProfileDecision` chose
`correlations: ["Default"]` (the binary target alone). Chunk 80's `_usable_profile` keeps it (a
numeric column), and `compute_correlations` refuses: "at least two non-constant numeric
columns are needed for correlation, found 1" (`profiling.py` ~427). The same tool also refuses
when constant columns leave fewer than two. A plot of an entirely null column is the other
remaining LLM-choosable refusal (`plot_*`: "column ... is entirely null; nothing to plot").

Decision: `_usable_profile` also drops (with chunk 80's warning, counted) (a) a correlations
list with fewer than two numeric columns that vary (`n_unique > 1` in `profile_schema`'s
evidence), and (b) a plot whose column is entirely null (`n_null == rows`). Both facts are
already in the schema evidence. Non-goals: changing the tools (their refusals stay honest);
the stuck run (cancelled; the user re-runs).

## Stage 83a (detailed spec)

Owned: `backend/statlab/domains/foundation.py`, `tests/test_nodes_foundation.py`. Not alone:
preserve others' edits.

1. `foundation.py` `_usable_profile` (~462):
   - After `columns, numeric = self._profile_columns(schema)`, add, in order:
     `counts = {name: _object(columns[name], "profile column").get("n_unique") for name in
     numeric}`;
     `varying = {name for name, n in counts.items() if isinstance(n, int) and n > 1}`;
     `rows = schema.get("rows")`.
   - In the loop tuple, the correlations entry's compatible set becomes `varying` instead of
     `numeric`.
   - Inside the loop, right after `usable = tuple(...)`, insert:
     ```
     if name == "correlations" and len(usable) < 2:
         # compute_correlations needs two varying numeric columns (chunk 83).
         usable = ()
     ```
   - Plots: the drop condition becomes `if plot.column not in columns or (plot.kind ==
     "target_relation" and plot.column == target) or _object(columns[plot.column], "profile
     column").get("n_null") == rows:` (keep the existing first two parts unchanged and in that
     order; the new part last).
   - Docstring: append one sentence: "A correlation request needs two varying numeric columns
     and a plot a column that is not entirely empty (chunk 83)."
2. `tests/test_nodes_foundation.py`:
   - `OfflineTools.__init__`: add `self.x_nulls = 0` and `self.extra_columns: dict[str,
     JsonValue] = {}` (next to `self.x_dtype`).
   - `profile_schema` fake summary becomes `{"rows": 240, "columns": {"x": {"dtype":
     self.x_dtype, "n_unique": 10, "n_null": self.x_nulls}, "id": {"dtype": "str", "n_unique":
     240, "n_null": 0}, "excluded": {"dtype": "str", "n_unique": 3, "n_null": 0}, "y":
     {"dtype": "bool", "n_unique": 2, "n_null": 0}, **self.extra_columns}}`.
   - New tests after `test_profile_keeps_usable_choices` (fixture `foundation: Harness`,
     `@pytest.mark.asyncio`, same pattern: set response, `await foundation.advance(2)`,
     `result = await foundation.step()`):
     a. `test_profile_drops_correlation_without_two_varying_columns`:
        `foundation.tools.extra_columns = {"w": {"dtype": "float64", "n_unique": 1, "n_null":
        0}}`; decision `ProfileDecision(rationale="one varying", correlations=("x", "w"))`;
        assert `result.status == "completed"`, `"ignored 2 profiling choice(s) that were not
        usable columns" in result.warnings`, no `compute_correlations` call.
     b. `test_profile_correlates_two_varying_columns`: `extra_columns = {"z": {"dtype":
        "int64", "n_unique": 5, "n_null": 0}}`; decision `ProfileDecision(rationale="two
        varying", correlations=("x", "z"))`; assert completed, no warning starting with
        `"ignored"`, and exactly one `compute_correlations` call whose `columns == ["x", "z"]`.
     c. `test_profile_drops_plot_of_empty_column`: `foundation.tools.x_nulls = 240`; decision
        `ProfileDecision(rationale="empty plot", plots=(ProfilePlot(kind="distribution",
        column="x"),))`; assert completed, `"ignored 1 profiling choice(s) that were not usable
        columns" in result.warnings`, no `plot_distribution` call.
   If an existing test fails because of the new fake fields, stop and report it (do not edit
   it).

Checks: `make test-fast TESTS="tests/test_nodes_foundation.py tests/test_nodes_design_inference.py tests/test_nodes_candidate_pipeline.py"`;
`.venv/bin/python -m ruff format` then `--check`, `ruff check`, and `mypy` on the two owned
files. Stop and report on any failure or gap.

## Stage 83a acceptance receipt (2026-10-19)

Implementer: focused foundation/design/pipeline node tests exit 0; Ruff format/check and mypy
on the two owned files exit 0; no deviations. Main reviewed the diff (matches the recipe).
`make check` (`check83.log`, temporary) exit 0: coverage 100% (TOTAL 13937 stmts, 0 missed,
3942 branches), mypy 213 files, Ruff clean, 222 files formatted.
