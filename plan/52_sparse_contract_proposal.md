---
status: completed
dependencies: [51]
completion_date: 2026-10-09
implementation_notes: "proposed contracts keep only supplied and non-default fields; make check 100%"
---

# Chunk 52 — a proposed contract locks only what was really chosen

User report 2026-10-09: run `r_35af8e6e3302a56b` (project `regression-test`) failed at
`experiment_design`: "LLM output remained invalid after one repair",
`candidate conflicts with explicit missing.per_column`.

Cause: `ProjectsService.propose` (`backend/statlab/api/projects.py` ~300) asks the contract LLM to
"Return a complete DataContract", validates the answer and returns it as is. Every field the
model wrote (all of them, defaults included) lands in `model_fields_set`, and
`persist_contract` saves `exclude_unset=True`, so the stored contract marks every default as an
explicit user choice. All three `regression-test` contracts on disk are fully materialized.
Experiment design treats explicit fields as binding (`design_inference.py` ~150-170), so every
candidate must copy the contract's preprocessing; here the explicit empty
`missing.per_column: {}` forbade any per-column imputation, and the model failed twice. The
first paid run's design rationale ("all candidates keep that contracted policy rather than
varying it") shows the same lock.

Decision: after the proposal passes its existing check, keep only (a) every field the caller
supplied, and (b) every field whose value differs from the default contract for the same
target column. Everything else is dropped from the set, so it stays a default. The effective
contract values are unchanged. Non-goals: no change to the validator, to uploads, to
`update_contract`, or migration of existing contract files (a new setup creates a sparse one).

## Stage 52a

Owned: `backend/statlab/api/projects.py`, `tests/test_api_projects.py`. Preserve other edits.

1. New module-level function below `_contains_supplied`:
   `def _sparse(proposed: dict[str, JsonValue], default: dict[str, JsonValue], supplied: JsonValue) -> dict[str, JsonValue]:`
   Docstring: "Keep supplied fields and fields that differ from the default contract, so
   defaults are not stored as explicit choices." Algorithm, for each `key, value` in
   `proposed.items()` (in order):
   - `kept = isinstance(supplied, dict) and key in supplied`
   - `base = default.get(key)`
   - if `isinstance(value, dict) and value and isinstance(base, dict)`: `inner = _sparse(value,
     base, supplied.get(key) if isinstance(supplied, dict) else None)`; when `inner or kept`,
     set `out[key] = inner if inner else value`.
   - elif `kept or value != base`: `out[key] = value`.
   Return `out` (a new dict).
2. In `propose`, after `proposal = await self.resources.llm.complete_setup(...)` and before
   `validation = await self.validate(...)`:
   `default = DataContract(target=TargetSpec(column=request.contract.target.column)).model_dump(mode="json")`
   `contract = DataContract.model_validate(_sparse(proposal.contract.model_dump(mode="json"), default, provided))`
   `proposal = ContractProposal(contract=contract, reasoning=proposal.reasoning)`.
   Then the existing validation and return use this `proposal`. Import `TargetSpec` from
   `statlab_contracts.contract` if not already imported.
3. Test `test_proposal_keeps_only_supplied_and_non_default_fields(tmp_path, monkeypatch)`,
   modelled on `test_proposal_partial_unresolved_mismatch_and_missing_target_paths`:
   - `full = _complete_contract().model_dump(mode="json")`;
     `full["constraints"]["max_candidates"] = 6`;
     `answer = DataContract.model_validate(full)` (every field set, like the real model).
   - `partial = DataContract(target=TargetSpec(column="a"))`; open the API with
     `answer=answer`, upload the same 2-row CSV, post the proposal with
     `partial.model_dump(mode="json", exclude_unset=True)`.
   - Assert status 200 and, on `body = response.json()["contract"]`:
     `"preprocessing" not in body`, `"modelling" not in body`,
     `body["constraints"] == {"max_candidates": 6}`,
     `body["target"] == {"column": "a", "type": "binary", "positive_class": True}`,
     `body["objective"] == {"task": "classification", "primary_metric": "roc_auc"}`,
     `body["split"]["seed"] == 7`, and
     `DataContract.model_validate(body).model_dump(mode="json") == answer.model_dump(mode="json")`.
   If any of these field names or defaults differ from the real schema (for example
   `max_candidates` default not 8, or `modelling` absent), stop and report the actual values.

Checks: `make test-fast TESTS="tests/test_api_projects.py tests/test_api_assistant.py"`
(if `tests/test_api_assistant.py` does not exist, run only the first and say so); Ruff check,
Ruff format --check and mypy on the two owned files.

Main then: review, `make check`, commit; tell the user to set up a fresh contract.

## Acceptance receipt (2026-10-09)

Implementer: `make test-fast TESTS="tests/test_api_projects.py tests/test_api_assistant.py"`
exit 0 (53 passed); Ruff check/format and mypy on both files exit 0. Main reviewed the diff and
ran `make check` exit 0: coverage 100 %, mypy 195 files, Ruff and format clean (log
`check52.log`, temporary). Read-only probe on the user's full contract
`ct_8acec0113c271d09.json`: `_sparse` keeps only `target`, `objective`, `split`, `features`
(no `preprocessing`), and the effective contract is identical. Existing contract files stay
locked; a fresh setup creates a sparse one.
