---
status: untouched
dependencies: [01, 02, 05, 21]
completion_date: null
implementation_notes: ""
---

# Chunk 25 — Typed LiteLLM client

**Goal.** Implement PLAN §7.1, D16/D18: async structured output in three capability tiers,
exactly one repair, bounded in-flight requests, domain errors and per-attempt usage/cost
records. B6 stage 2 receives chunk 21's implementation report. Routing/skills remain chunk 26.

## 1. Prerequisites & Context

### State

Baseline `5447651`: 2096 passed, 1 skipped; 100% line/branch (5417 statements, 1406 branches),
mypy 103 files, ruff clean (historical baseline). Chunk 21's unfinished implementation from rewound
commit `02f1739` remains in the working tree; require main-reviewed focused stage evidence before
this stage starts, not new full gates.
Read its final report and public create_node_execution API; reuse it, do not redefine it.

| Reuse | Location | Purpose |
|---|---|---|
| Settings | backend/statlab/config.py | Provider-free concurrency setting |
| StatlabError, Failure, retry_budget | errors.py / contracts/envelope.py | Error taxonomy |
| transaction, new_id, utc_now | persistence | Existing SQL/ID discipline |
| create_node_execution | chunk 21 | Required llm_calls FK |
| llm_calls SQL | migrations/001_initial.sql | Already complete; no migration |

### Probes (2026-10-02; LiteLLM 1.100.0)

| # | Finding | Consequence |
|---|---|---|
| P1 | Import fetches remote cost map unless LITELLM_LOCAL_MODEL_COST_MAP=True first | Set default before import and at conftest top; prove with blocked sockets in fresh subprocess |
| P2 | acompletion(mock_response=JSON) returns real ModelResponse/usage offline | DI fake calls for tests; one actual offline mock round-trip |
| P3 | completion_cost(mock response) needs explicit model=provider-prefixed name | Always supply requested model |
| P4 | Local map lacks deepseek-flash and prices legacy Flash incorrectly (0.44/1.32 per million) | Local peak-rate overrides for current/legacy Flash names |
| P5 | supports_response_schema is true for deepseek/*; local-server params can falsely promise modes | Prefix override before LiteLLM detection; conservative prompt override for ollama/* |
| P6 | Official DeepSeek docs confirm JSON-only Chat Completions, json word + example; empty answers possible | DeepSeek is tier json, empty is validation failure |

Sources checked at build time: [pricing](https://api-docs.deepseek.com/quick_start/pricing/),
[JSON output](https://api-docs.deepseek.com/guides/json_mode/),
[thinking](https://api-docs.deepseek.com/guides/thinking_mode/).
Canonical name deepseek-flash; legacy deepseek-v4-flash(-vision-exp) still routes to Flash;
deepseek-v4-pro remains available. Peak/off-peak prices are in the amended PLAN §7.1.

### Decisions

**D-a. Repair every tier once**, including native schema: model validators can reject
schema-conformant output. PLAN §7.1 is amended. Preserve the stable prefix; append invalid
answer and field-level validation feedback. A second invalid answer is LlmOutputError,
error_class=tool_param. No JSON fence stripping, substring extraction or coercion heuristic.

**D-b. Peak-rate Flash costs are informational upper estimates.** Use noncached prompt
tokens at $0.30/M, cached at $0.006/M and output at $1.20/M, including legacy names.
Mark the ceiling with a ponytail comment; exact time-based billing can follow if needed.
Other prices use completion_cost(response, model=model). Unknown cost maps log zero with
an explanatory warning, never abort a valid response solely for unavailable pricing.

**D-c. Thinking is explicit at the provider boundary.** `complete(..., reasoning=False)`
disables DeepSeek thinking by default; reasoning=True enables it with effort=high. Chunk 26
maps roles to this flag. Do not read, log or persist reasoning_content. Other providers receive
no DeepSeek-specific parameters.

**D-d. One semaphore per client, configured**, default 4, positive int setting named
max_concurrent_llm / STATLAB_MAX_CONCURRENT_LLM. Each attempt acquires/releases it; cancellation
releases permits and propagates. Backoff belongs to 31; do not add it here.

| Not this chunk | Owner |
|---|---|
| Role routing / keys / skill loader / prompt assembly | 26 |
| Recovery and exponential backoff | 31 |
| SSE usage totals, token streaming | 35 |
| Live paid provider calls | Optional user smoke test, never CI |

## 2. Files to Create / Modify

| File | Action |
|---|---|
| backend/statlab/llm/__init__.py | Create package |
| backend/statlab/llm/client.py | Create LlmClient and local provider capability/cost overrides |
| backend/statlab/errors.py | Add LlmCallError and LlmOutputError using shared Failure taxonomy |
| backend/statlab/config.py | Add provider-free positive concurrency setting |
| backend/statlab/persistence/models.py | Frozen LlmCallRow matching existing SQL |
| backend/statlab/persistence/repository.py | record_llm_call with execution attribution |
| tests/test_llm_client.py | Create focused async/offline tests |
| tests/conftest.py, tests/test_config.py | Offline import env and setting tests |
| plan/25_llm_client.md, 00_index.md, WHATS_NEXT.md | Measured completion state |

## 3. Implementation Spec

### 3.1 Public API

`LlmClient(settings: Settings, *, acompletion: Callable[..., Awaitable[object]] | None = None)`.
At module top use os.environ.setdefault before importing litellm; suppress_debug_info=True.
Default uses real litellm.acompletion. Do not use synchronous completion on the event loop.

`async complete[T: BaseModel](conn: sqlite3.Connection, *, run_id: str,
node_execution_id: str, model: str, messages: Sequence[dict[str, str]],
output: type[T], reasoning: bool = False) -> T`.
Provide an explicit `example: dict[str, JsonValue] | None = None` argument if the model's
json_schema_extra examples are absent. In json tier an actual JSON example is required; use
provided example, model examples, or a generic syntax example clearly labelled as syntax-only.
Do not add a recursive fake-value/schema-example generator. Caller messages are copied.
No default model string or Role class here; routing is 26.

### 3.2 Rules

V1. Capability tier is schema/json/prompt. Prefix overrides inside llm/ take precedence;
deepseek/ => json, ollama/ => prompt. Otherwise query supports_response_schema first,
then supported params for response_format, else prompt. Unknown model/capability exception
falls back to prompt with a warning. Keep this conservative choice documented/tested.

V2. Schema tier passes output model as response_format. JSON tier passes json_object and a
system schema instruction containing lowercase json and an example. Prompt tier omits
response_format and includes schema. Keep caller's invariant/system prefix ahead of variable
messages: append the schema to an existing initial system message or insert one at the front.
Never mutate the original messages; never send a model-specific parameter to another provider.

V3. Nonstreaming completion, explicit request timeout, semaphore. Require ModelResponse and
nonempty choices with text content; None/empty content is output validation failure. Parse
only message.content with output.model_validate_json; ignore reasoning_content.

V4. Log every returned attempt before parsing it, including invalid JSON and repaired answers:
signature=output.__name__, requested model, prompt/completion/cached tokens, cost_usd,
duration_ms. Cached tokens come from usage.prompt_tokens_details.cached_tokens else zero;
absent usage yields zero. Validate nonnegative counts and cached<=prompt at the boundary.
Do not subtract cached input twice. Foreign cost result is narrowed to finite nonnegative float.

V5. On invalid content/model validation, append the prior assistant content (or empty string)
and sanitized validation errors (no input/ctx object repr), then make exactly one further
completion. A second invalid response is LlmOutputError with Failure(error_class=tool_param,
field_errors nonempty). Also repair schema-tier custom-validator errors.

V6. RateLimitError, Timeout, APIConnectionError, ServiceUnavailableError => LlmCallError
transient. Other provider/SDK or malformed-response errors => bug; wrap causes, no raw library
exception escapes. No retries for transport failures here. Record a failed attempted request
with zero usage/cost if no response exists. asyncio.CancelledError propagates and releases the
permit; log the started cancelled request with zero usage where possible. No secret keys,
full prompts, response text or reasoning in llm_calls (the table contains metadata only).

V7. Repository uses transaction + new_id/utc_now, returns read-back row. Reject execution ids
belonging to another run before dispatch and in repository; FK/SQLite errors -> PersistenceError.
No node lifecycle API, no model/metric repositories, no schema changes.

V8. D18 source scan backend/statlab: no module outside llm/ mentions provider/model tokens
deepseek, openai, anthropic, claude, gpt-, ollama (case-insensitive). Existing provider-free
docstrings stay valid. Contracts remain stdlib+pydantic. No explicit forbidden typing tokens,
no new type ignores. Untyped third-party payloads held as object and narrowed.

## 4. Test Suite Spec (~45 cases)

| IDs | Assertions |
|---|---|
| T1-T8 | All three tiers; override beats lying detection; unknown capabilities; schema/example prompt, copied prefix, no extra kwargs to other providers |
| T9-T18 | Valid typed object; malformed/empty/None content repairs; custom-validator schema failure repairs; second failure tool_param/field_errors; exactly two logs |
| T19-T27 | Cached/missing usage; explicit-model cost; Flash current and legacy pricing, hand-computed cached bill; absent cost warning; duration/signature; failed request records |
| T28-T35 | All transient classes and bug, malformed response, no automatic backoff, cancellation and permit release; barrier proves requests overlap up to injected limit |
| T36-T40 | Real acompletion offline mock, import in fresh process with blocked socket and env initially absent, D18 provider scan, execution attribution/FK errors |
| T41-T45 | Positive setting defaults/env/bounds, full-suite prior regressions and 100% branch coverage |

## 5. Definition of Done

- [ ] Implementer hands off focused LLM tests and affected regressions/targeted static checks.
      Main reviews the complete B6 candidate, then owns one final gate under AGENTS.md: full pytest
      WITH coverage via timeout=900, strict mypy, ruff check/format; 100% line/branch and zero new
      ignores/coverage suppressions. No separate plain full suite or duplicate worker final run.
- [ ] No paid/live provider requests; import test blocks socket connects in a fresh process.
- [ ] Each attempted completion has a row; repair count exactly one, cached costs independently
      calculated, concurrency proved by a barrier rather than elapsed-time guess.
- [ ] Implementer runs each mutation once for final review (one replace, count==1, finally restore,
      timeout=300); rerun only proofs whose property changed:
  1. consult LiteLLM before override (DeepSeek wrongly schema);
  2. remove import's local-model-map env default;
  3. remove repair or allow a third completion;
  4. discard cached tokens in the log/cost;
  5. price all prompt tokens as misses;
  6. omit explicit model from completion_cost;
  7. omit the semaphore;
  8. map rate limit to bug;
  9. omit the first invalid attempt's log.
- [ ] Main updates frontmatter/index after final acceptance; WHATS_NEXT measured evidence + one
      Next B7 heading, batch B6 summary and tradeoffs. Docs preserve product gate evidence.
      Implementer does not update plans, commit, or push; main commits only when authorized.

### Explicitly out of scope

Routing, role enum, API key startup checks, prompt heuristics, recovery sleeps, streaming,
new providers/dependencies, dollar budget enforcement, chunks 22/26 onwards.

### Stop and ask if

Offline acompletion cannot work with the installed pin; structured output would need Responses
API; a live call/new dependency or dropping planned behavior becomes necessary.

### Report back with

1. Changed files, focused commands/exit codes/summaries, full-log paths, and candidate inputs.
2. Mutation-to-failing-test table, including survivors.
3. Public API and errors; tier override/thinking choices.
4. Independently computed cached-token bill vs logged bill.
5. Offline import/mock and semaphore proof; no network calls.
6. What chunk 26 must know; deviations/concerns. Do not commit or push.
