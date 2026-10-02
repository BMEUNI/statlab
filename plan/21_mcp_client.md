---
status: untouched
dependencies: [02, 03, 05, 06, 07, 16, 19, 20]
completion_date: null
implementation_notes: ""
---

# Chunk 21 — Backend MCP client

**Goal.** Implement PLAN §4.2, §6.5, D12/D23/D32/D34e: one discovery cache and one
dispatch boundary owning injection, artifact resolution, lineage, the durable test-score
claim, and the persisted tool log. Re-drive M6 and M7b through this production client.
Batch B6 stage 1; stage 2 is chunk 25, not chunk 22.

## 1. Prerequisites & Context

### State

HEAD `5447651`; 2096 passed, 1 skipped; 5417 statements / 1406 branches, 100% coverage;
mypy clean on 103 files; ruff check/format clean. Baseline verified 2026-10-02.
`plan/HANDOFF.md` is an existing untracked user file: preserve it, do not stage it.

| Reuse | Location | Purpose |
|---|---|---|
| ToolCallError, ToolNotAllowedError, ArtifactLockedError, EngineError | backend/statlab/errors.py | Boundary errors |
| create/get_artifact, list_artifacts | backend/statlab/storage.py | Authoritative registry |
| get_run, claim_test_scoring, set_test_fingerprint | persistence/repository.py | Durable facts |
| TOOL_RESULT_ADAPTER, Failure | contracts/statlab_contracts/envelope.py | Wire parsing |
| LoggedCall | contracts/statlab_contracts/reporting.py | Pipeline provenance |
| Driver/check_lineage | tests/test_skeleton.py | Move guards into production; delete duplicated logic |
| mcp_session | tests/conftest.py | Same-task HTTP/session lifetime pattern |

### Probes (2026-10-02)

| # | Finding | Consequence |
|---|---|---|
| P1 | MCP 2.2 `call_tool` accepts `read_timeout_seconds: float | None` | Use 900 seconds per call, including heavy fits, instead of the short SDK default |
| P2 | MCP result's structured union is under `structured_content["result"]` | Parse through TOOL_RESULT_ADAPTER |
| P3 | `node_execution_id` is NOT NULL on both call tables | Add the minimal execution-row repository now; chunk 22 extends it |
| P4 | jsonschema 4.26.0 is already installed via LiteLLM | Reuse Draft202012Validator for the served schema; do not build a validator |
| P5 | Existing reporting selects a champion after the skeleton has scored test | Move selection into the spine before test replay; reuse the resulting champion table |
| P6 | Sandbox denies local socket binding and the tilde fixture | Required gates use the hard-timeout wrapper with approved escalation; do not weaken tests |

### Decisions

**D-a. Keep the score claim after every dispatched attempt**, including ToolFailure,
timeout, cancellation and malformed responses. The existing primitive promises never to clear
the claim. An ambiguous call may have computed metrics; recovery must fork instead of risking
two exposures. Validate parameters and refs before claiming, so pre-dispatch refusals consume
nothing. A failed scoring attempt is documented and tested, not silently reset.

**D-b. The domain owns the pure lineage policy.** Move the skeleton's policy into
`backend/statlab/domains/lineage.py`; mcp_client explicitly re-exports `check_lineage`.
The normative spine tuple lives there, avoiding node-name literals outside domains (D27).
Use ArtifactLockedError instead of test AssertionError; unknown nodes are EngineError.
Add `champion_selected` to the node-12 guard. No concrete nodes/engine are built here.

**D-c. A repair callback is optional.** `call` accepts a typed async callback receiving
Failure and returning corrected params. For `tool_param` only, run it once, then re-enter
every guard/injection/validation step. No retry/backoff loop, no LLM import: later nodes supply
the callback. Without one, propagate ToolCallError with field_errors intact.

| Not this chunk | Owner |
|---|---|
| Engine, checkpointing, events, prune-on-success | 22 |
| Skills and prompts, LLM repair authoring | 26 |
| Recovery/backoff and fork lifecycle | 31 |
| Model/metric repositories | Their first node caller |

## 2. Files to Create / Modify

| File | Action |
|---|---|
| backend/statlab/mcp_client.py | Create client, ToolSpec, CallContext and logged_calls |
| backend/statlab/domains/lineage.py | Create moved pure policy and spine tuple |
| contracts/statlab_contracts/envelope.py | Move ORCHESTRATOR_SUPPLIED here |
| mcp_server/statlab_tools/tool.py | Explicitly re-export the moved constant; keep callers working |
| backend/statlab/persistence/models.py | Add frozen NodeExecutionRow and ToolCallRow |
| backend/statlab/persistence/repository.py | Minimal create_node_execution and record_tool_call |
| tests/test_mcp_client.py | Create focused guard/boundary/repository coverage |
| tests/test_skeleton.py, tests/test_spine_reporting.py | Replace driver guards/injection/log with production client and DB log |
| tests/conftest.py | Only necessary shared fixture changes |
| pyproject.toml | Scoped missing-stub override for jsonschema if needed; no new dependency |
| plan/21_mcp_client.md, 00_index.md, WHATS_NEXT.md | Completion state and measured evidence |

## 3. Implementation Spec

### 3.1 Public seam

`McpClient(settings: Settings, conn: sqlite3.Connection, *, session: object | None = None)`
is an async context manager. The optional session is a test seam, not another transport.
Production owns the bearer HTTP client, streamable_http_client and ClientSession with an
AsyncExitStack; enter and exit in one task, including partial-enter cleanup. Initialize once,
call tools/list once, cache ToolSpecs; do not discover on each call. Cache the complete schema
(including $defs), properties, required, phase, allow_in, heavy, transform_only. Fail discovery
on malformed metadata/schema rather than inventing permission defaults.

Frozen CallContext carries `paths: RunPaths`, `node: str`, `node_execution_id: str`,
`allowed: frozenset[str]`, `contract: DataContract`, `dataset: str`,
`champion_selected: bool = False`. Until 26, derive allowed from served allow_in. Provide a
small accessor such as `allowed_tools(node)`; do not add a second tool registry.
Check the execution row belongs to that run and node, and the run/project slug match paths,
before dispatch. Do not let logs attribute a call to a different run's execution.

`async call(ctx, tool: str, params: dict[str, JsonValue], *,
trusted: Mapping[str, JsonValue] | None = None,
repair: Callable[[Failure], Awaitable[dict[str, JsonValue]]] | None = None) -> ToolSuccess`.
Caller dictionaries remain unchanged. Cancellation must propagate, not become a retryable error.

### 3.2 Ordered rules

V1. Tool must be both in ctx.allowed and permitted by meta.allow_in (exact wildcard `[*]`).
Unknown/denied tools raise ToolNotAllowedError before network dispatch.

V2. Find orchestrator-owned fields by description equal to ORCHESTRATOR_SUPPLIED. Discard the
agent value. Fill project/run only when declared, dataset for load_csv, split.seed for split_*
and constraints.random_seed otherwise. trusted fills other marked fields (calls/target/
prior_test_scorings/n_waves). Agent values and trusted cannot override project/run/dataset/seed.
An unfilled marked field raises EngineError; never accept an agent's proposed value as fallback.
Unmarked target parameters remain agent-selected (a score's target is not export's target).

V3. Validate the injected object against the complete served JSON Schema before dispatch.
Map paths to Failure.field_errors, error_class tool_param; allow one callback repair (D-c).
Use installed jsonschema, narrow its object boundary, and do not add explicit forbidden typing
tokens or ignores. Schema-invalid attempts do not claim test or call the network.

V4. Walk strings matching ARTIFACT_ID_PATTERN recursively through lists and dict values.
Each must exist, be unpruned, and belong to this run or a fork ancestor in the same project.
Follow forked_from_run_id, including multiple ancestors; foreign/unrelated refs are ArtifactError.
Require its file under this run's RunPaths.artifact(ref, kind), including a fork's hard link.
A registered ancestor without the child link must fail before dispatch. Do not re-register refs.

V5. A ref with derived_from_test or split=test taints its enclosing top-level argument.
Move all existing pure check_lineage clauses intact. In node 12, every tainted input additionally
requires champion_selected. Fit_* and train-param refusal, plus second-score refusal, are
universal. Before node 12 only verify_split; node 12 transform_only / first score / reporting
after score; later read-only consumption remains allowed. Selecting a champion after the durable
score claim raises EngineError even with untainted arguments.

V6. Atomically claim_test_scoring immediately before dispatch for score_* with tainted input.
A lost race raises ArtifactLockedError. Re-read durable state, never an in-memory score count.
Two clients/connections, restart, transformed copies and fork lineage must exercise this rule.
Keep the claim on failure (D-a).

V7. Dispatch with 900-second read timeout; validate actual CallToolResult and its envelope.
ToolFailure maps to ToolCallError with the original Failure. Transport/timeouts map to transient;
unexpected result/envelope maps to bug. Do not swallow domain errors or CancelledError.

V8. Register outputs with paths.artifact(meta.ref, meta.kind), split=meta.split,
derived_from_test=tainted or meta.split==test. Validate refs and file existence at this boundary.
Split-summary test_fingerprint goes through set_test_fingerprint. Keep all output refs in order.

V9. Record every dispatched attempt, successful or failed, as a tool_calls row: arguments
exactly as sent, validated/synthesized envelope JSON, duration_ms and timestamp. Persist response
before any post-response registry error can lose its audit evidence. No row for local guard or
schema refusal. Record repair attempts independently. `logged_calls(conn, run_id)` reads rows in
insertion (SQLite rowid) order, constructs tuple[LoggedCall, ...], failed calls have empty outputs.
Malformed stored rows become PersistenceError. Do not log bearer secrets.

### 3.3 Repositories and integration

Add frozen row models matching SQL; create_node_execution has run_id, node_name, seq,
candidate_id=None, attempt=1, status=running, timestamps from existing ids helpers. Return the
read-back row. record_tool_call validates execution attribution; uses transaction and domain
wrapping. No DDL change or unused lifecycle API.

The skeleton's Driver may remain as a thin test convenience accepting `node, tool, **params`;
it creates/reuses one execution per node and delegates to McpClient. Delete _inject, _tainted,
the test-local policy, raw_call, schema reader and driver.log. Drive the client's owned HTTP
lifetime via a sync fixture/context-manager factory. Keep G1-G7, the real fixtures and metrics.
Use real project/contract/run rows, generated run ids and matching RunPaths. Select_champion now
runs before test replay; retain its table in SpineResult. Reporting reuses it. Trusted values
travel separately from agent params. Reproducibility uses logged_calls, not a test-built log.

## 4. Test Suite Spec (~65 new/relocated cases)

| IDs | Assertions |
|---|---|
| T1-T8 | Owned real HTTP round-trip/auth, one discovery; missing/invalid metadata; lifecycle cleanup, transport and malformed result errors |
| T9-T18 | Both allow-lists; wildcard; injection spoofing, absent schema fields, two different seeds; trusted marked/unmarked values; missing trusted value; schema bounds/$defs and one repair |
| T19-T28 | Nested refs, multiple fork ancestors/links, foreign project/run, unknown/pruned/missing file, normal strings not refs, context attribution |
| T29-T45 | Move every skeleton policy case with domain error assertions; independently trigger train, fit, prechampion, transform, reporting, champion-required and second-score clauses |
| T46-T53 | Durable concurrent claim/restart; failed envelope/timeout keep claim; transformed test propagation; split fingerprint; selecting after scoring refused |
| T54-T60 | Exact as-sent args, ordered output refs, failed/repair logs, stored malformed JSON, repository FK/identity rejection and no predispatch record |
| T61-T65 | Real M6 classification/regression plus reporting and M7b; DB artifacts match refs/files; logs reconstruct both standalone pipelines |

Counts are a planning estimate, not a quota: cover every reachable line/branch and each rule's
failure, with meaningful fixtures. Do not make assumptions true by test construction.

## 5. Definition of Done

- [ ] Full regression and coverage gates through timeout=900 wrapper, outputs read; 100% line
      and branch, one designed skip. Strict mypy, ruff check/format pass.
- [ ] No explicit forbidden typing token, no coverage suppression, no new type ignores;
      compute/provider boundaries remain intact. No node-name literals outside domains.
- [ ] M6 G1-G7 and M7b prove the client path over HTTP on both real fixtures.
- [ ] Run these mutations independently; each kills at least one targeted test; restore in
      finally and verify diff afterwards:
  1. remove ctx.allowed check while keeping meta.allow_in;
  2. keep agent seed instead of injected seed;
  3. walk dict keys instead of values;
  4. remove ancestor ownership check;
  5. drop derived_from_test propagation;
  6. replace atomic score claim with unconditional success;
  7. allow node-12 test input without champion_selected;
  8. remove each pure lineage clause separately (train, fit, second score, prechampion,
     transform/reporting gate and reporting-after-score predicate);
  9. select_champion allowed after scoring;
  10. record caller params rather than sent arguments.
- [ ] Read generated report.md, pipeline.py and manifest.json in full, probe a real fixture
      through split/impute/encode/scale/train/score, preserve exact reproduction.
- [ ] Update own frontmatter/index; WHATS_NEXT stays B6 until 25 completes. Do not commit.

### Explicitly out of scope

Chunk 22 onwards, retries with sleep/backoff, provider routing, a new tool catalogue, schema/DB
migrations, a generic domain engine, deletion of the user handoff.

### Stop and ask if

The wire schema cannot be validated by installed jsonschema; M6/M7b requires relaxing a lineage
guard; a feature must be dropped; a new dependency or cross-process import is necessary.

### Report back with

1. All gate outputs/counts/time and changed files.
2. Each mutation and failing test; any survivor.
3. Claim failure semantics and concurrent/restart evidence.
4. M6/M7b metric values and reproduction differences; artifact paths for independent review.
5. Exact public CallContext/client/repository signatures.
6. Any deviations and hazards for chunk 25/22. Do not commit or push.
