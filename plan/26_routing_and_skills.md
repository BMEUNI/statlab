---
status: completed
dependencies: [05, 21, 22, 25]
completion_date: 2026-10-04
implementation_notes: "B8 accepted on 1e9fe6a plus recorded dirty inputs: 45 initial focused cases, affected corrections green; 2428 full passes, one designed skip, 100% line/branch; full static exits 0. See receipts/B8.md. Committed with exact accepted product inputs."
---

# Chunk 26 — Routing, skills, prompt context and project memory

## Goal

Provide the policy/context seams concrete nodes will consume in B9. Reuse the entered B6
MCP registry and one shared B6 LLM client; no new agent framework or provider adapter.
Architecture authority: PLAN D12/D16/D18/D27/D36d, §7/§7.2/§7.3/§9.1–9.3.

## Prerequisites and reuse

- B7 committed `1e9fe6a`; B8 chunk 24 focused receipt: 120 passed, all focused exits 0.
  Canonical `RunState.branches`, domain-supplied traversal and existing `NodeInvocation`
  carry candidate identity; no new scheduler or concrete executor here.
- Accepted B6 `LlmClient.complete` owns output validation, one repair, timeout, cost audit
  and shared semaphore. `McpClient.tools` provides `ToolSpec.allow_in`; `CallContext.allowed`
  is the existing pre-dispatch authority. Reuse its evidence; do not edit these clients.
- Existing SQL `memory(id, project_id, key, value_json, source_run_id, ts)` is sufficient.
  Plain repository functions, frozen row models and existing transaction/ID helpers.
- Root AGENTS owns validation. Search ENVIRONMENT only for LiteLLM/Pydantic/settings
  dependencies. Installed SDK documentation/source is primary evidence for key validation.

## Exact owned files

- New `backend/statlab/llm/routing.py`, `skills.py`, `prompts.py`.
- New `backend/statlab/domains/routing.py` (node-to-role declarations only).
- New `backend/statlab/memory.py` (project facts read/remember actions only).
- `backend/statlab/persistence/models.py`, `repository.py`: memory row/repositories only;
  preserve chunk 24 candidate changes.
- `backend/statlab/llm/__init__.py`: public exports if needed.
- New `skills/modelling.md`.
- `Makefile`: append LLM configuration check to `check-env` only; test/check targets unchanged.
- New `tests/test_llm_routing.py`, `tests/test_skills.py`, `tests/test_prompt_context.py`,
  `tests/test_project_memory.py`. No unrelated tests, dependencies, settings or migrations.

## Frozen behavior/API

### Routing and startup configuration

- `Role(StrEnum)` values `default`, `reasoning`, `classify`. `ModelRouter` receives a
  domain-supplied mapping of node names to roles and environment values, with
  `model_for(node, *, skill_model=None)` and `role_for(node)`.
- Four-level precedence: explicit skill model literal; skill role selects role/environment;
  `STATLAB_MODEL_<ROLE>`; configured role default; DEFAULT fallback. Unknown nodes use DEFAULT.
  Empty model overrides fail configuration rather than silently reverting.
- Model defaults from PLAN §7.3: default/classify `deepseek/deepseek-flash`, reasoning
  `deepseek/deepseek-v4-pro`. These literals and provider credential names exist only in `llm/`.
- D16's amended reasoning nodes: problem_definition, experiment_design, inferential_analysis,
  model_interpretation, champion_selection. Generic router contains no node names. Domain map
  declares these five plus interrupt/status CLASSIFY handlers. Q&A caller uses current node role;
  no control handler implementation here.
- Load routing environment consistently from `.env` then process environment (process wins).
  Fail-fast `check_provider_environment(models)` / CLI helper validates only selected model
  providers, including explicit skill models when startup supplies them. No network requests,
  live model probes or import-time checks. Startup helper for Makefile checks the three role models.
  Credentials must never appear in messages, reprs, logs or chained exception text.
- Reuse LiteLLM's installed provider/environment guarantees. Reachable gap: its key-membership
  tests accept a present blank credential. Reject missing/blank required credentials. Prefer SDK
  metadata/checks over a copied provider catalogue. Installed SDK reads process globals; its
  environment validator can silently accept uncovered providers. Use a bounded isolated
  subprocess with merged environment,
  blank values omitted, SDK `get_llm_provider` plus `validate_environment`, local cost map and
  non-DEV `LITELLM_MODE` (prevents SDK reloading dropped `.env` values). Only safe missing-variable
  names cross back; no provider catalogue, network call or temporary global environment swap.
  Inside the isolated child, compare SDK validation with an empty credential environment: if it
  reports success without requirements, reject startup as validation unavailable (recognized
  providers such as databricks otherwise falsely pass). Credential-free/SDK-unverified provider
  startup support is deferred; preserve SDK alternative-credential rules for supported checks.

### Skill parsing and snapshot

- Frozen `NodeSkill` holds node name, original section text, required field texts,
  `allowed: frozenset[str]`, optional model override. Frozen `SkillPolicy` holds preamble,
  ordered node skills, original bytes/hash and node lookup.
- `load_skill(path, *, spine: tuple[NodeSpec,...], tools: Mapping[str,ToolSpec])` parses
  `## <node>` / `### <field>`; required Purpose/Heuristics/Tools/Outputs/Pitfalls, optional Model.
  Reject duplicate nodes/fields, missing/extra nodes, missing headings, malformed Model,
  unknown tool names and tools whose `allow_in` excludes node (wildcard permitted).
  Empty tool sections allowed. Domain-supplied spine is authority, never derived from this file.
- Tools are plain tool names, one per line; returned frozenset feeds existing CallContext
  unchanged. Each node receives only preamble plus its own section; no other node policy.
- Scaffold all 13 normative nodes with truthful Purpose/Outputs and real registry-validated
  Tools. Heuristics/Pitfalls remain explicit TODO placeholders for human authors. Do not list
  deferred temporal/grouped tools or invent domain heuristics. Tests use the actual served
  registry and a separately declared normative spine to validate the scaffold.
- `snapshot_skill(policy, run_dir)` creates `skill.snapshot.md` with exact source bytes;
  `load_run_skill(run_dir, source_path, *, spine, tools)` reuses an existing snapshot on restart
  and validates it, otherwise loads/validates source then snapshots. Return SHA256 for future
  manifest owner 29. A source edit cannot change an already started run's policy. Wrap file/
  decoding failures as safe ConfigError. Snapshot failure must not leave a partial policy file.

### Project memory and stable prompt assembly

- Frozen `MemoryRow`: existing SQL columns, with typed JSON decode helper. Append-only
  `create_memory(conn, *, project_id, key, value: JsonValue, source_run_id=None)` and
  `list_memory(conn, project_id) -> tuple[MemoryRow,...]`, stable chronological ordering.
  Existing duplicate keys preserve separate facts/provenance; no upsert, second store or schema.
- `remember` backend action delegates validated write. When source_run_id supplied, verify
  its run belongs to destination project before insert. FK only verifies existence: a real
  run in another project otherwise forges provenance. Nonblank key; JSON canonical/finite;
  invalid JSON/SQLite failures become PersistenceError without evaluating values/formulas.
- Read memory once at context initialization/run start, immutable snapshot for stable prefix.
  Later remember writes affect future contexts/runs. No auto extraction or session summarizer;
  concrete final node owns eventual automatic fact extraction.
- Frozen `PromptContext` initialized from own NodeSkill/preamble, validated DataContract and
  ordered project-memory snapshot. `messages(*, state_summary: JsonValue,
  recent_tool_results: tuple[JsonValue,...], question: str)` returns typed B6 messages.
  Exact order: preamble, own node section, canonical contract, memory in system prefix;
  state summary, recent results, question in user tail. Stable prefix byte-identical across
  changed tail, no neighboring node text. No secrets or raw frame data added by builder.
- Exercise actual accepted B6 LlmClient with assembled messages and offline mocked completion;
  typed output and SQL audit still pass. No paid live calls, new retries or cache adapter.

## Focused proof

`make test-fast TESTS='tests/test_llm_routing.py tests/test_skills.py tests/test_prompt_context.py
tests/test_project_memory.py tests/test_llm_client.py::test_provider_names_are_local_to_llm_package'`

- Routing: all precedence levels, role override/environment, unknown node/default, five D16
  reasoning entries, control roles/current-node Q&A, blank overrides, selected-only provider
  key checks, missing/blank credential plus .env/process precedence, safe failure text.
- Skills: actual scaffold registry/spine validation, required/duplicate/unknown/disallowed
  cases, wildcard, node isolation, exact snapshots/hash and changed-source restart reuse,
  genuine unreadable/invalid file errors. Use public validators, no bypassed invalid models.
- Memory: typed round trip/append ordering, project isolation, nullable provenance, reject
  cross-project provenance and invalid finite JSON; startup snapshot remains stable after writes.
- Prompts: exact prefix/tail order/isolation, changed tail preserves prefix, B6 offline completion
  and existing audit. Reuse B6 repair/semaphore evidence rather than retesting unchanged behavior.
- Scoped mypy/Ruff/format over owned Python files. Inspect Makefile diff/command semantics;
  no full tests/coverage or global static gate in worker. No blanket mutation quota.

## Definition of done / stop

Focused green terminal handoff with changed files, candidate HEAD/file hashes, exact commands,
genuine exits/summaries/full-log locations, SDK guarantees used and next-stage hazards.
Stop before global acceptance, commits, push, plan/status changes or concrete nodes/controllers.
Escalate an architecture/API blocker with evidence; preserve all others' dirty edits.

## Consolidated review addendum

2026-10-04: retain bare declared role overrides as blank until process precedence is
applied, so configuration cannot silently fall back. Prompt canonicalization wraps
nonfinite JSON serialization as safe `EngineError`; Pydantic `JsonValue` permits these
floats, so its type validation does not provide the finite guarantee. Public regressions
cover state/tool payloads with NaN and both infinities. Affected 25-case group and scoped
static checks exited 0; exact evidence is in the stage-26 focused receipt.

Coverage review removed the unreachable normal cleanup path with no temporary file:
creation now precedes unconditional cleanup. Existing publication/failure/race tests
remain unchanged, 18 focused cases and scoped statics exited 0.
