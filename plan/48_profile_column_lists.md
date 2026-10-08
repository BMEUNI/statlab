---
status: completed
dependencies: [47]
completion_date: 2026-10-08
implementation_notes: "profiling question lists numeric and non-numeric columns by the validator rule; make check 100%"
---

# Chunk 48 — data profiling tells the model which columns count as numeric

User report 2026-10-08: the first autonomous online run after chunk 47, `r_74c99d8739ae9d06`
(project `regression-test`, 100,000 x 44 student table, target `exam_score`), failed at
`data_profiling` with `tool_param` "LLM output remained invalid after one repair":
`summarize_numeric entries must be exact column names; rejected ['internet_access',
'private_tuition'] (unknown, repeated or incompatible)`.

Cause: both columns are stored as 0/1 `int64`, and the contract gives them the role `boolean`.
`FoundationNodes._validate_profile` (`backend/statlab/domains/foundation.py` ~404) treats a
column as numeric by dtype, and a contract role overrides it (any role other than `numeric`
removes the column). The model sees only the `profile_schema` dtypes, plus the prose rule
"(dtype or contract role)", so it picked them for `summarize_numeric` twice.

Decision: compute the numeric and non-numeric column sets once, with the validator's own rule,
and state both lists in the profiling question. The validator stays exactly as strict; the
model just gets the answer up front. Non-goals: no change to the role rule, the refusal, the
repair loop, or other nodes.

## Stage 48a

Owned: `backend/statlab/domains/foundation.py`, `tests/test_nodes_foundation.py`.

1. New method on the same class as `_validate_profile`, placed directly above it:
   `def _profile_columns(self, schema: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], set[str]]`.
   Body = the current first part of `_validate_profile`, moved unchanged: `columns =
   _object(schema.get("columns"), "profile columns")`, the `numeric` set comprehension, and the
   contract-roles loop. Returns `(columns, numeric)`.
2. `_validate_profile`: replace those moved lines with
   `columns, numeric = self._profile_columns(schema)`. Everything after is unchanged.
3. `_profile`: just before `decision = await self.runtime.decide(`, add
   `columns, numeric = self._profile_columns(schema.summary)`. Append to the `question`
   string (after "...never a sentence, pair or comment.") the f-string parts
   `f" Numeric columns: {', '.join(sorted(numeric)) or 'none'}."` and
   `f" Non-numeric columns: {', '.join(sorted(set(columns) - numeric)) or 'none'}."`
   The existing literal parts stay word for word.
4. Test: in `test_profile_check_feeds_llm_repair`, after the two existing
   `messages[-1]["content"]` assertions, add
   `assert "Numeric columns: x." in messages[-1]["content"]` and
   `assert "Non-numeric columns: excluded, id, y." in messages[-1]["content"]`
   (default harness: `x` dtype float64, no contract roles; `id`/`excluded` str, `y` bool).
5. Test: in `test_profile_selection_uses_declared_structural_casts` (contract role
   `x: numeric`, `x_dtype = "str"`), after the existing assertion add
   `messages = next(m for o, _, m in env.llm.calls if o is ProfileDecision)` and
   `assert "Numeric columns: x." in messages[-1]["content"]`.
6. New test `test_profile_question_lists_role_overridden_columns(migrated_db, project, tmp_path)`,
   same setup style as test 5: contract
   `DataContract(target=TargetSpec(column="y", type="binary"), features=FeatureRules(roles={"x": "boolean"}))`,
   `env = harness(migrated_db, project, tmp_path, contract=contract)`, default `x_dtype`
   (float64), `env.llm.responses[ProfileDecision] = ProfileDecision(rationale="no numeric")`,
   `await env.advance(3)`. Then the same `messages` lookup and assert
   `"Numeric columns: none." in messages[-1]["content"]` and
   `"Non-numeric columns: excluded, id, x, y." in messages[-1]["content"]`.
   If `advance(3)` fails for a reason unrelated to the question text, stop and report.

Checks: `make test-fast TESTS="tests/test_nodes_foundation.py"`; Ruff check, Ruff
format --check and mypy on the two owned files.

Main then: review, `make check`, commit. Online re-run is the user's.

## Acceptance receipt (2026-10-08)

Implementer: `make test-fast TESTS="tests/test_nodes_foundation.py"` exit 0 (71 collected);
Ruff check/format and mypy on both owned files exit 0. Main reviewed the diff (body moved
unchanged; validator untouched). Main `make check` exit 0: coverage 100 %, mypy 195 files,
Ruff and format clean (log `check48.log`, temporary). Online confirmation is the user's re-run.
