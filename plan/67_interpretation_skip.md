---
status: completed
dependencies: [66]
completion_date: 2026-10-09
implementation_notes: "skip_reason skips the whole Explain step; a skip that also asks for diagnostics is rejected and repaired; make check 100%"
---

# Chunk 67 — skipping coefficients no longer skips the whole Explain step

User report 2026-10-09: the Explain (model_interpretation) cell shows the skip icon.

Cause (read-only, user DB `statlab.db`): run `r_fc9f0c008f3445d5`, candidates c_02 (decision
tree) and c_07 (hist gradient boosting): `InterpretationDecision` carried `skip_reason`
"coefficient_importance is not applicable ... so interpretation uses validation permutation
importance, partial dependence and confusion-based error analysis instead" together with
`partial_dependence` (`["Weight","Age"]`, `["Weight","Age","Height"]`) and `error_by` lists.
`CandidateModelExecutor._interpretation` (`backend/statlab/domains/candidate_models.py`
~512-540) returns `skipped` whenever `skip_reason` is set, so no diagnostics ran. The model
meant "skip coefficients"; the field does not say it skips the whole step.

Decisions:
1. `InterpretationDecision.skip_reason` gets a schema description saying it skips the whole
   step and that leaving out coefficients means `coefficients` false.
2. A decision with `skip_reason` and any requested diagnostic (`coefficients` true, non-empty
   `partial_dependence` or `error_by`) is contradictory: the decide `check` rejects it (one
   repair, D-style like `_validate_evaluation`), and the same validation runs after decide.
3. The question text and the skill section say the same in one sentence each.
Non-goals: changing how a legitimate skip works, re-running old runs.

## Stage 67a (detailed spec)

Owned: `backend/statlab/domains/decisions.py`, `backend/statlab/domains/candidate_models.py`,
`skills/modelling.md`, `tests/test_nodes_candidate_models.py`. Not alone: preserve others'
edits.

1. `decisions.py` `InterpretationDecision`: `skip_reason: str | None = Field(default=None,
   min_length=1, description=("Set only to skip this whole step: then no permutation
   importance, partial dependence or error analysis is produced. Leave it null to run the
   diagnostics. To leave out coefficients for a non-linear model, keep coefficients false"
   " instead of skipping."))`. Other fields unchanged.
2. `candidate_models.py`:
   - After `_validate_evaluation`, add `def _validate_interpretation(decision:
     InterpretationDecision) -> None:` — if `decision.skip_reason is not None and
     (decision.coefficients or decision.partial_dependence or decision.error_by)`: raise
     `_RefusalError("skip_reason skips the whole interpretation; leave it empty to run the
     requested diagnostics (keep coefficients false to leave out coefficients only)")`.
     (`InterpretationDecision` is already imported in this module; if not, add it to the
     existing `statlab.domains.decisions` import.)
   - In `_interpretation`, the `self.runtime.decide(...)` call: append to the question string
     the sentence `" skip_reason skips the whole step; to leave out coefficients only, keep
     coefficients false."` (after `"model_summary.feature_names."`), and add the argument
     `check=_rejecting(_validate_interpretation)`. Directly after the call add
     `_validate_interpretation(decision)` (before `if decision.skip_reason is not None:`).
3. `skills/modelling.md` `## model_interpretation` `### Pitfalls`: append the bullet
   `- skip_reason skips the whole step (no importance, partial dependence or error analysis); to leave out coefficients for a non-linear model keep coefficients false.`
4. `tests/test_nodes_candidate_models.py`, in
   `test_interpretation_guided_skip_coefficients_and_unscaled_pdp`, directly after the
   `assert decision["skip_reason"] == skipped.skip_reason` line:
   - `h.llm.responses[InterpretationDecision] = InterpretationDecision(rationale="Bad skip",
     skip_reason="No coefficients", partial_dependence=("x",))`;
     `_failed(await _run(h, 10), "skip_reason skips the whole interpretation; leave it empty
     to run the requested diagnostics (keep coefficients false to leave out coefficients
     only)")`.
   - `check = h.llm.checks[InterpretationDecision]`; `assert check(InterpretationDecision(
     rationale="r", skip_reason="Not needed")) is None`; `assert
     check(InterpretationDecision(rationale="r", partial_dependence=("x",))) is None`; for
     each of `InterpretationDecision(rationale="r", skip_reason="s", coefficients=True)`,
     `InterpretationDecision(rationale="r", skip_reason="s", partial_dependence=("x",))`,
     `InterpretationDecision(rationale="r", skip_reason="s", error_by=("x",))`: `with
     pytest.raises(DecisionRejectedError, match="skips the whole interpretation"):
     check(rejected)`.

Checks: `make test-fast TESTS="tests/test_nodes_candidate_models.py tests/test_skills.py
tests/test_nodes_candidate_pipeline.py"`; Ruff check, Ruff format --check and mypy on
`backend/statlab/domains/decisions.py backend/statlab/domains/candidate_models.py
tests/test_nodes_candidate_models.py`; `.venv/bin/python -m integration.run_scenarios --llm
offline --only S01` (offline, no cost). Stop and report on any failure.

Main then: review, `make check`, commit. A live check needs the user's go-ahead.

## Acceptance receipt (2026-10-09)

Implementer: focused selectors (candidate models, skills, candidate pipeline) exit 0; Ruff
check/format and mypy on the three Python files exit 0; offline S01 exit 0 PASS
(`integration/out/20261009T141724Z-offline`). Main reviewed the diff (matches the recipe).
`make check` (`check67.log`, temporary) exit 0: coverage 100 % (TOTAL 12583 stmts, 0
missed), mypy 200 files, Ruff clean. The live model's behaviour shows on the user's next run.
