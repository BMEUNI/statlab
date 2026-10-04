# B8 stage26 focused handoff

Candidate: HEAD `1e9fe6a03367b4f7ff47792e515e9de70139590a` plus the owned working-tree file hashes below. Chunk24 dirty changes preserved; repository modifications are memory imports/functions only. No commits, global gates, coverage or mutations. No unrelated plan/status changes.

Changed files: `Makefile`, `backend/statlab/llm/routing.py`, `backend/statlab/llm/skills.py`, `backend/statlab/llm/prompts.py`, `backend/statlab/domains/routing.py`, `backend/statlab/memory.py`, `backend/statlab/persistence/models.py`, `backend/statlab/persistence/repository.py`, `skills/modelling.md`, `tests/test_llm_routing.py`, `tests/test_skills.py`, `tests/test_prompt_context.py`, `tests/test_project_memory.py`. `llm/__init__.py` unchanged.

Behavior proved: role/environment/skill precedence and five amended reasoning roles; isolated selected-provider credential checks; real 13-node scaffold against served registry; strict headings/tool permissions; exact immutable snapshots, concurrent publication and cleanup; append-only SQL facts and same-project run provenance; finite canonical JSON; stable initialized node-local prompt prefix; real accepted LLM client offline completion and SQL accounting.

## Commands and genuine results

- `make test-fast TESTS='tests/test_llm_routing.py tests/test_skills.py tests/test_prompt_context.py tests/test_project_memory.py tests/test_llm_client.py::test_provider_names_are_local_to_llm_package'`: exit 0, 45 passed (45 progress successes, `[100%]`; project's double quiet mode omits duration/summary). Full output `/private/tmp/statlab-B8-stage26-focused-4.log`. Explicit make wrapper: Python subprocess timeout 900 seconds, `--no-cov`.
- Final refinements only changed two tests: generic valueless `.env` entry handling and fixed equal timestamps to prove rowid ordering. `make test-fast TESTS='tests/test_llm_routing.py::test_env_precedence tests/test_project_memory.py::test_append_order_isolation_and_snapshot'`: exit 0, 2 passed. `/private/tmp/statlab-B8-stage26-tail-focused.log`. Unchanged 43-case evidence reused.
- `.venv/bin/python -m mypy backend/statlab/llm/routing.py backend/statlab/llm/skills.py backend/statlab/llm/prompts.py backend/statlab/domains/routing.py backend/statlab/memory.py backend/statlab/persistence/models.py backend/statlab/persistence/repository.py tests/test_llm_routing.py tests/test_skills.py tests/test_prompt_context.py tests/test_project_memory.py`: exit 0, success on 11 files. `/private/tmp/statlab-B8-stage26-mypy-4.log`. After the two test refinements, scoped mypy of `tests/test_llm_routing.py tests/test_project_memory.py`: exit 0, success on 2 files, `/private/tmp/statlab-B8-stage26-tail-mypy.log`.
- `.venv/bin/python -m ruff check backend/statlab/llm/routing.py backend/statlab/llm/skills.py backend/statlab/llm/prompts.py backend/statlab/domains/routing.py backend/statlab/memory.py backend/statlab/persistence/models.py backend/statlab/persistence/repository.py tests/test_llm_routing.py tests/test_skills.py tests/test_prompt_context.py tests/test_project_memory.py`: exit 0, all checks passed. `/private/tmp/statlab-B8-stage26-ruff-final.log`.
- `.venv/bin/python -m ruff format --check backend/statlab/llm/routing.py backend/statlab/llm/skills.py backend/statlab/llm/prompts.py backend/statlab/domains/routing.py backend/statlab/memory.py backend/statlab/persistence/models.py backend/statlab/persistence/repository.py tests/test_llm_routing.py tests/test_skills.py tests/test_prompt_context.py tests/test_project_memory.py`: exit 0, 11 already formatted. `/private/tmp/statlab-B8-stage26-format-final.log`.
- `git diff --check`: exit 0; `/private/tmp/statlab-B8-stage26-diff-check.log` (empty successful output); repeated final exit 0 after test-only edits.
- `make -n check-env`: exit 0. `/private/tmp/statlab-B8-stage26-check-env-command.log`. Existing backend/MCP commands unchanged; appended `.venv/bin/python -m statlab.llm.routing`. Test/check targets unchanged.

Earlier correction evidence: focused-1 make exit 2 (async fixture decorator issue; no product failures), mypy-1 exit 1 (JSON value annotations and test annotation); focused-2 make exit 2 (test's directory assertion counted fixture SQL files). Skills-only focused-3 exit 0. Logs retained at `/private/tmp/statlab-B8-stage26-focused-1.log`, `/private/tmp/statlab-B8-stage26-focused-2.log`, `/private/tmp/statlab-B8-stage26-skills-3.log`, `/private/tmp/statlab-B8-stage26-mypy-1.log`. Initial combined Ruff/format command showed remaining long-line findings (Ruff intermediate exit was not separately captured); format resolved them, final Ruff exit 0.

## SDK guarantees and approved boundary decision

Installed LiteLLM 1.100.0 `utils.validate_environment` uses environment-key membership; it accepts blanks, includes credential alternatives, and defaults to success with no missing keys for providers its validator does not cover. `get_llm_provider` rejects an unknown provider but recognized `databricks` demonstrates the uncovered-provider gap.

Main approved: one bounded isolated child for all selected models; process environment wins over `.env`, drop blank values; local model cost map and `LITELLM_MODE=PRODUCTION`; provider resolution, empty-environment SDK requirement check, then actual SDK environment check. Empty environment mutation occurs only in child and is restored before actual check. Missing names are the only returned details; captured foreign stdout/stderr and exception values are never surfaced. Child timeout 30 seconds. No provider catalogue, copied requirements, parent-environment mutation, paid calls or live probes.

SDK `__init__.py:15–27` auto-loads `.env` in default DEV mode. Non-DEV child prevents dropped/replaced credentials being reloaded. Existing B6 parent client retains its accepted import behavior unchanged.

## Next-stage hazards / remaining work

- Recognized providers for which SDK establishes no required environment (including genuinely credential-free providers) fail closed with `startup environment validation unavailable for selected provider`. Supporting those providers requires an explicit future decision, not a silent pass.
- API startup should preserve effective runtime `.env` values: B6 import may load `.env` in DEV, while setting non-DEV in parent prevents that SDK convenience. Validator only configures its isolated child.
- B9/domain assembly must pass its declared spine and entered registry to `load_run_skill`, pass `skill.allowed` unchanged to existing `CallContext`, and build one `PromptContext` from `read_memory` at initialization. Q&A calls router with current node; explicit startup skill model literals must be included in selected model validation. No concrete nodes/controllers added here.
- Full batch acceptance and review remain main-owned. No required stage26 mutation/probe beyond tests.

## Owned candidate SHA256

- `Makefile` `dcdaa199f1e8d48715d7817f2ad19f0820011a7a94bba5503fb2015c81d9da68`
- `backend/statlab/llm/routing.py` `f705b0976b94329b73832d0f10fcff86b2662825fd217800abc5a2f080f517ca`
- `backend/statlab/llm/skills.py` `c9dcdc01dc92db49209d8b05393b1d371ba1d132d1671b4dda19bfe46c3669cd`
- `backend/statlab/llm/prompts.py` `e30b214f1f2fa74c1ef44e1d8b8690a0bec14196b2001d8150d061ad27880715`
- `backend/statlab/domains/routing.py` `7146691428dcd2eaca2bd2bc233ab6cddec8436c861b91444e64d6b6f38c138b`
- `backend/statlab/memory.py` `62fb915a08ba2dd2276f8cf84ff02eec97349accb8f0b3aebf534a410211b1f4`
- `backend/statlab/persistence/models.py` `aa33844ce3da802a289de4425518e13306d56c299f6dca6beb7b4c44b6f0cc28`
- `backend/statlab/persistence/repository.py` `8f12ec870ff01dde7721dae6a327a591ab579ddef25b3ae47bfbcd8927e86648`
- `skills/modelling.md` `af5cd8686fb807e0a678acc8f09d11c099e1f281048fbb098195fdded3b1450d`
- `tests/test_llm_routing.py` `344d9fccd0bcdb035775a7959fd0b0b6a904b72262dbdea8f9b8173ab28f926c`
- `tests/test_skills.py` `0c07ac500f2b35681501eb727f33e9875d9e7d9bc8a1e8ae083ca15878034d14`
- `tests/test_prompt_context.py` `cb2f041778c5d7b4ba4e30c289907a83a0837166607862d11480e2e17fe01dbe`
- `tests/test_project_memory.py` `74fb79207c79e283ca8c0cd757e530451ffd8208229cf3947327fba00c57d4ab`

## Consolidated review corrections

Correction candidate: unchanged HEAD `1e9fe6a03367b4f7ff47792e515e9de70139590a` plus the four hashes below. Only routing/prompt sources and their tests changed; other workers' edits preserved. Prior hashes above identify the initial stage handoff, not this correction candidate.

- Bare declared `STATLAB_MODEL_<ROLE>` entries from `dotenv_values` now become blank overrides before process values are merged. All three roles fail through public `ModelRouter` when no process override exists; valid process values still win. Generic valueless entries retain their prior behavior.
- Nonfinite values are legitimate Pydantic `JsonValue` inputs. Canonical prompt serialization now converts `ValueError` into safe `EngineError` from None. Public prompt builder tests cover NaN and both infinities in state summaries and recent tool results; formatted exceptions contain neither private payload details nor the foreign ValueError.

Commands/results:

- `make test-fast TESTS='tests/test_llm_routing.py tests/test_prompt_context.py'`: exit 0, 25 progress successes, `[100%]`; double quiet mode omits duration/summary. Explicit subprocess timeout 900 seconds and `--no-cov`. Full output `/private/tmp/statlab-B8-stage26-review-focused.log`.
- `.venv/bin/python -m mypy backend/statlab/llm/routing.py backend/statlab/llm/prompts.py tests/test_llm_routing.py tests/test_prompt_context.py`: exit 0, success on 4 files. `/private/tmp/statlab-B8-stage26-review-mypy.log`.
- `.venv/bin/python -m ruff check backend/statlab/llm/routing.py backend/statlab/llm/prompts.py tests/test_llm_routing.py tests/test_prompt_context.py`: exit 0, all checks passed. `/private/tmp/statlab-B8-stage26-review-ruff.log`.
- `.venv/bin/python -m ruff format --check backend/statlab/llm/routing.py backend/statlab/llm/prompts.py tests/test_llm_routing.py tests/test_prompt_context.py`: exit 0, 4 already formatted. `/private/tmp/statlab-B8-stage26-review-format.log`.
- `git diff --check`: exit 0, empty output. `/private/tmp/statlab-B8-stage26-review-diff.log`.

Correction SHA256:

- `backend/statlab/llm/routing.py` `327001f020f43840554642d9b08d5bd1f8658ce3f8d36d5eac1cb328c7cb9dc2`
- `backend/statlab/llm/prompts.py` `338d2dc380a72f04d998a0fac618b6653612554f981395a02cbeab5f268e789e`
- `tests/test_llm_routing.py` `d970e585345dddd867d345bbeb5df6c1e3c2a31827b152ffad726bb18cd406a7`
- `tests/test_prompt_context.py` `2676b298b66a07b93da3be826dcad45d3240ac7b6fc6481af08802d9862311bc`

SDK boundary decision and next-stage hazards above remain unchanged. No remaining focused failure; main owns full batch acceptance. No global gate, coverage, mutation, commit or status rewrite performed.

## Final publication lifecycle cleanup

Candidate remains HEAD `1e9fe6a03367b4f7ff47792e515e9de70139590a` plus shared dirty work. This correction changed only `backend/statlab/llm/skills.py` and this factual receipt. Source SHA256: `17769e8f0e803b8dd20f15f110686ddb7cba5e4dabf61e6f040b521ad5f38b88`.

Main identified unreachable cleanup arc `skills.py:127->131`: successful NamedTemporaryFile creation always supplies a valid temporary path. Creation now precedes the cleanup try, and every created temporary is unconditionally unlinked in finally. Context management remains explicit; the handle closes before the atomic hard-link publication. Exact bytes, exclusive publication, existing snapshot immutability, same/different concurrent publication and safe I/O failures retain their prior behavior. Existing real failure/race tests reused; `tests/test_skills.py` unchanged. No bypass test, guard or suppression added.

- `make test-fast TESTS=tests/test_skills.py`: exit 0, 18 progress successes, `[100%]`; 900-second subprocess timeout, `--no-cov`. `/private/tmp/statlab-B8-stage26-lifecycle-focused-2.log`.
- `.venv/bin/python -m mypy backend/statlab/llm/skills.py`: exit 0, success on 1 file. `/private/tmp/statlab-B8-stage26-lifecycle-mypy-2.log`.
- `.venv/bin/python -m ruff check backend/statlab/llm/skills.py`: exit 0, all checks passed. `/private/tmp/statlab-B8-stage26-lifecycle-ruff-2.log`.
- `.venv/bin/python -m ruff format --check backend/statlab/llm/skills.py`: exit 0, 1 already formatted. `/private/tmp/statlab-B8-stage26-lifecycle-format-2.log`.
- `git diff --check`: exit 0, empty output. `/private/tmp/statlab-B8-stage26-lifecycle-diff.log`.

Initial lifecycle draft: focused tests exit 0 (18 successes), mypy/format exit 0, Ruff exit 1 (`SIM115` context management); corrected before final evidence. Initial logs `/private/tmp/statlab-B8-stage26-lifecycle-{focused,mypy,ruff,format}.log`.

No focused failures remain. Other edits preserved. No global gate, coverage, commit or plan/status rewrite. Main owns final coverage acceptance; it was not rerun here.
