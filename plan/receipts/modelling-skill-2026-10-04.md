# Modelling policy and Flash defaults — 2026-10-04

## Disposition and candidate

Requested edits completed and reviewed; uncommitted, no push. User explicitly authorized
research-informed skill authorship and DeepSeek-V4.1-Flash, then stopped all tests. This is an
adjunct policy/configuration task, not B9 implementation or newly accepted full product evidence.
Base commit: `276cf36` (`feat(b8): add candidate fan-out and policy context`).

| Product input | SHA256 |
|---|---|
| `skills/modelling.md` | `a284990d694386084d923bfeff059eb9009dbdf54454f310937e1ecb19a7d003` |
| `tests/test_skills.py` | `b49b816097a41e24dd69c7166c040edc966de40275021d20d9ec89db491329eb` |
| `backend/statlab/llm/routing.py` | `ff77b391d705b16bc1846a40b48af07e81d7aff5be9cea46f74baff100131e2b` |

Other changed files: `plan/PLAN.md`, `plan/WHATS_NEXT.md`, `plan/MODELLING_SKILL.md`, this receipt.
The spec contains frozen behavior, research sources, ownership and actual review corrections.
Main edited plans only; implementer owned skill/test edits and the subsequent routing edit.

## Requirements reviewed

- Filled 26 Heuristics/Pitfalls sections across 13 nodes. Preserved preamble, node/headings,
  Purpose/Outputs and exact Tools sections; no new permissions, model literals or APIs in skill.
- Consolidated capability review against existing tools and D28/D32–D35: train-only fitting,
  validation-only selection, OOF target encoding, disclosed prepared-frame CV limitations,
  bounded supported candidates, adjusted inference tests, one champion test score and exposure
  disclosure. Existing registry/snapshot/Pydantic guarantees reused; no new defensive guards.
- Updated the existing scaffold assertion after its real focused failure (it required TODO text).
  No new tests, mutation probes or paid LLM calls.
- All three default roles now use `deepseek/deepseek-flash`. Official [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing/)
  explicitly map `deepseek-flash` to DeepSeek-V4.1-Flash and support thinking/non-thinking modes
  (verified 2026-10-04). Existing role assignments, reasoning flags, override precedence and
  Flash capability/pricing handling remain intact. Client thinking flags depend on the request's
  reasoning boolean, not the Pro model name.
- Safe inspection of `.env` and process environment found no `STATLAB_MODEL_<ROLE>` overrides;
  no secret contents were printed or changed. Future explicit skill/env overrides remain supported.
- Research used primary sklearn, statsmodels and ASA sources, mapped in
  [the bounded spec](../MODELLING_SKILL.md#research-evidence-primary-sources-consulted-2026-10-04).
  No dependencies changed; documented methods remain bounded by implemented capabilities.

## Focused handoff (before user stopped tests)

Initial `make test-fast TESTS=tests/test_skills.py`: pytest exit 1, make exit 2, solely the stale
TODO assertion. Log: `/private/tmp/modelling-skill-focused-20261004.log`.

Correction handoff, same skill/test hashes as above:

| Command | Exit | Result / log |
|---|---|---|
| `make test-fast TESTS=tests/test_skills.py` | 0 | 18 passed, no coverage; `/private/tmp/modelling-skill-focused-correction-20261004.log` |
| `.venv/bin/ruff check skills/modelling.md tests/test_skills.py` | 0 | All checks passed (Python scope); `/private/tmp/modelling-skill-ruff-correction-20261004.log` |
| `.venv/bin/ruff format --check tests/test_skills.py` | 0 | 1 file already formatted; `/private/tmp/modelling-skill-format-correction-20261004.log` |
| `.venv/bin/mypy tests/test_skills.py` | 0 | No issues in 1 source file; `/private/tmp/modelling-skill-mypy-correction-20261004.log` |

Worker's preserved-section/heading comparison and scoped diff whitespace check exited 0.
Main read focused logs and reviewed the content/capability corrections. The focused receipt
predates the one-line routing follow-up and does not establish its tested acceptance.

## Full acceptance attempt and explicit stop

One attempt, before the user stopped tests:

```sh
PYTEST_ADDOPTS='-o addopts="--strict-markers --strict-config"' make check
```

Exit **2**. Its explicit 900-second pytest subprocess timed out around 38%, after 977 progress
dots. No final pytest summary or coverage TOTAL was produced; the global mypy/Ruff/format targets
were not reached. Full log: `/private/tmp/modelling-skill-acceptance-20261004.log`.
This is **not a passed acceptance gate**. Runtime cause remains unresolved; investigation and
further test execution stopped on the user's instruction. A prior collect-only command exited 0
and executed no tests (`/private/tmp/modelling-skill-collection-20261004.log`).

After the stop: implementer made only the reviewed reasoning-default line change, ran no tests
or static gates; scoped `git diff --check` exited 0. Main's final diff whitespace review exited 0.
No additional tests, live calls or global gates ran. Unchanged B8 inputs retain their earlier
[accepted evidence](B8.md); that evidence does not prove these changed runtime inputs.

## Remaining limits

Full acceptance and live V4.1-Flash integration are unproved for this candidate. Existing immutable
run snapshots retain their original skill bytes; edits apply to future unsnapshotted runs. B9–B11
still own concrete domain decisions/full agent assembly. Nothing remains for the user to fill in
the modelling skill; existing credentials and services remain the runtime prerequisites.
