---
status: completed
dependencies: [78]
completion_date: 2026-10-10
implementation_notes: "an over-long decision user_summary is shortened instead of failing the step"
---

# Chunk 79 — a long chat summary never fails a step

User report 2026-10-10: run `r_d5082326a4150e9d` (project `teszt`, temporal split) stopped at
"Data quality — It failed: LLM output remained invalid after one repair".

Cause (main, read-only): the failure gate's evidence has `field_errors: {"user_summary":
"String should have at most 600 characters"}`; both `QualityDecision` calls (original and
repair, `deepseek/deepseek-flash`, 1653 and 1603 completion tokens) wrote a chat summary over
`Decision.user_summary`'s `max_length=600` (`backend/statlab/domains/decisions.py` ~31). The
summary is cosmetic chat text; failing a whole step for it is wrong.

Decision: `Decision` gains a `mode="before"` validator on `user_summary` that shortens a string
longer than 600 characters: keep the text up to and including the last `". "`-terminated
sentence end within the first 601 characters (so the kept text ends with `.` and is at most 600
characters); with no such sentence end, keep the first 599 characters with trailing whitespace
removed, plus `…`. Shorter strings, `None` and non-strings pass through unchanged (so `""` and
wrong types still fail as before). The JSON schema keeps `maxLength: 600`, so the model still
sees the limit. Applies to every decision type (all inherit `Decision`). Non-goals: changing
other length limits; editing the stuck run.

## Stage 79a (detailed spec)

Owned: `backend/statlab/domains/decisions.py`, `tests/test_domain_decisions.py`. Not alone:
preserve others' edits.

1. `decisions.py` class `Decision`, after the `user_summary` field:
   `@field_validator("user_summary", mode="before")` `@classmethod` `def _shorten_summary(cls,
   value: object) -> object:` docstring `"""Shorten an over-long chat summary at a sentence end
   instead of failing the step (chunk 79)."""`. Body: if `not isinstance(value, str) or
   len(value) <= 600`: return `value`; `end = value[:601].rfind(". ")`; if `end >= 0`: return
   `value[: end + 1]`; return `value[:599].rstrip() + "…"`. (`field_validator` is already
   imported.)
2. `tests/test_domain_decisions.py`:
   - `test_user_summary_bounds` parametrize becomes `["", 7]` (both still raise
     `ValidationError`).
   - New `test_long_user_summary_is_shortened()`:
     - `exact = "x" * 600`: `Decision(rationale="r", user_summary=exact).user_summary ==
       exact`.
     - `Decision.model_validate({"rationale": "r", "user_summary": "First sentence. " + "y" *
       700}).user_summary == "First sentence."`
     - `unbroken = Decision.model_validate({"rationale": "r", "user_summary": "z" *
       700}).user_summary`; assert `unbroken == "z" * 599 + "…"` and `len(unbroken) == 600`.
     - `edge = "a" * 598 + ". " + "b" * 50` (the sentence end at index 598):
       `Decision.model_validate({"rationale": "r", "user_summary": edge}).user_summary == "a" *
       598 + "."`.
     - `IntakeDecision.model_json_schema()["properties"]["user_summary"]["anyOf"][0]
       ["maxLength"] == 600`.

Checks: `make test-fast TESTS="tests/test_domain_decisions.py tests/test_domain_binding.py
tests/test_nodes_persistence.py tests/test_nodes_candidate_pipeline.py"`; Ruff check, Ruff
format --check and mypy on the two owned files. Stop and report on any failure or gap.

Main then: review, `make check`, commit.

## Stage 79a acceptance receipt (2026-10-10)

Implementer: focused selectors (domain decisions, domain binding, nodes persistence, nodes
candidate pipeline) exit 0, 47 passed; Ruff check/format and mypy on the two files exit 0.
Accepted deviation: the docstring wrapped over two lines (E501). Main reviewed the diff
(matches the recipe). `make check` (`check79.log`, temporary) exit 0: coverage 100 % (TOTAL
13768 stmts, 0 missed), mypy 212 files, Ruff clean; no other test expected a >600 summary to
fail.
