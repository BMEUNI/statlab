---
status: completed
dependencies: [08, 09, 10, 22, 24, 26]
completion_date: 2026-10-04
implementation_notes: Implemented and reviewed; B9 static acceptance exit 0 under user test stop. Runtime/coverage acceptance outstanding; see receipts/B9.md.
---

# Chunk 27a — shared foundation nodes 1–4

## Goal

Implement callable, typed tabular foundation nodes using the entered B6 MCP client and LLM
client, B8 policy/context/routing and existing NodeInvocation/NodeResult engine seam. Declare
the normative 13-node domain, with only nodes 1–4 executable in this stage. No API boot/full
agent assembly, gate persistence/resume handlers, candidate execution or reporting yet.

## Prerequisites and reuse

Base `3348f9d`: B8 accepted source plus completed policy/Flash default edits. Reuse accepted
B6 dispatch/lineage/repair evidence and B8 boundary/checkpoint/fan-out evidence for unchanged
inputs. The policy receipt records its earlier full gate timeout; do not rerun it here.

Read PLAN §4 normative spine, §4.1 contract precedence and D5/D8/D12/D16/D27/D32/D33/D34/D35.
Domain nodes/names belong only in `backend/statlab/domains/`; no compute imports in backend.
Search ENVIRONMENT only for Pydantic/LiteLLM/MCP facts; no new dependency or SDK probes.

Reuse `NodeSpec`, `NodeResult`, `NodeInvocation`, `RunState.shared_results`, `PromptContext`,
`SkillPolicy`, `ModelRouter`, `LlmClient.complete`, `CallContext`, `ToolBoundary` and existing
storage/repository helpers, particularly `set_test_fingerprint`/`get_run`. Do not copy client
validation, provider pricing, cancellation, budget or checkpoint logic into domain nodes.

## Exact owned files

- `backend/statlab/domains/base.py`: add the small frozen Domain aggregate only.
- `backend/statlab/domains/tabular.py`: declared spine/domain metadata.
- `backend/statlab/domains/decisions.py`: typed foundation decisions/problem output.
- `backend/statlab/domains/runtime.py`: reusable domain invocation/prompt/tool/approval helpers.
- `backend/statlab/domains/foundation.py`: executor for nodes 1–4 and state-output readers.
- `tests/test_nodes_foundation.py`: meaningful offline decision/executor regression tests, authored
  but not executed under the current user instruction.

Main owns plans/status/receipt; worker does not edit them. No other files without reporting a
concrete blocker to main first. Preserve all dirty work; no engine/MCP/tool/config/schema migration.

## Frozen API and behavior

### Domain and runtime seam

Add a frozen `Domain` aggregate carrying name, contract schema, skill path, tool phases and
ordered NodeSpec tuple. `TABULAR_DOMAIN`/`TABULAR_SPINE` declare PLAN's exact 13 nodes/kinds,
skippability and guided gate policy: shared nodes always, candidate nodes exception. Deferred
nodes have an explicitly unavailable decision schema/executor, never pretend to run successfully.
Stage 27b replaces node-5/6 decision declarations; later stages supply the remaining schemas.
The full declaration enables existing `load_run_skill` validation without changing that loader.

`DomainRuntime` receives caller-owned sqlite connection, RunPaths, dataset digest, immutable
DataContract, entered ToolBoundary, shared LlmClient, validated SkillPolicy, ModelRouter and
memory snapshot. Expose typed decision generation and tool calls bound to NodeInvocation.
Use immutable PromptContext per node, unchanged skill.allowed in CallContext, current execution
identity, contract and dataset. Tool params derived from contract must never be supplied by LLM.
Model names remain confined to llm/; reasoning boolean follows Role.REASONING, skill model
override passes through existing routing. No new retries/clients/events/checkpointers.

Provide a minimal caller-supplied approval callback for guided decisions, returning approved
typed decision or None. In autonomous mode proceed; in guided mode without approval return
`awaiting_user` with serialized decision/evidence and retained refs BEFORE discretionary actions.
Validate approved values against the actual decision schema and semantic contract constraints.
No gate SQL/event publication or answered-gate resume is claimed: chunks 31–32 own that wiring.
Fixed-contract evidence calls may precede intake approval; they make no discretionary data edits.
Do not erase artifacts needed while awaiting a decision. Never catch cancellation broadly.

`FoundationExecutor(runtime)` implements async NodeExecutor over nodes 1–4, rejects other
nodes with EngineError. Helpers read the latest successful shared boundary for required output
and raise a package error for missing/malformed upstream data, not IndexError/KeyError. Preserve
tool summaries, warnings and refs in NodeResult; do not persist fictitious report artifacts.
JSON problem/profile decisions live durably in checkpointed result summaries for now; manifest
and final exports remain 29. Return data-class failed results for actual contract/data refusal.

### Intake

Mechanically call load_csv with dataset options from contract (dataset/project/run injected by
existing dispatch); no alternative parsing retry. Obtain raw ref from the returned artifact,
then infer_column_roles, validate_contract, detect_id_columns and detect_leaky_columns.
Use returned facts in a typed IntakeDecision (finding/rationale/proposed fixes, proceed/escalate).
Contract `valid=False` forces data refusal regardless of LLM approval. ID/leak flags are warnings
and human-review evidence, never automatic column removal or proof of leakage. Preserve raw and
all summaries. Snapshot ownership remains caller's existing run setup; do not overwrite snapshots.
No list_estimators call here: its allow-list permits node 5, which owns registry validation.

### Problem definition

No tools. Typed ProblemDecision/ProblemSpec records target type, derived task, metric choice,
secondary metrics, threshold, inference goals, constraints/risks and rationale. Existing contract
non-null/explicit values are authoritative. Reject conflicting target/task, task-incompatible
metrics, target/excluded/unknown inference predictors using existing contract vocab/validators
and intake column evidence. Goals are fixed here, before profiling; later findings are exploratory.
Do not invent facts when target/task ambiguity cannot be resolved from contract/intake evidence;
return data escalation. Use model_fields_set only where preserving explicitly supplied empty
goals/defaults matters; avoid redundant checks already guaranteed by DataContract/Pydantic.

**Metric sequencing decision:** when classification primary metric is unspecified, node 2
records the automatic D33 rule (`pr_auc` if minority share <0.1 else `roc_auc`), not an invented
class balance. Node 3 resolves that rule from summarize_target. An explicit contract metric wins;
an agent override needs a recorded nonblank rationale. Regression automatic default is `rmse`.
Expose one `problem_from_state` helper that consumes node-2 spec plus node-3 resolution so later
stages receive an effective metric consistently. Metric finalization never changes frozen goals.

### Profiling

Use raw from intake and fixed node-2 problem. Mandatory facts: profile_schema, report_missingness,
summarize_target. ProfileDecision selects bounded optional summaries/plots using typed column
choices, not arbitrary tool dictionaries; inspect served schema for precise argument names.
Limit plot count (at most four), keep optional numeric/categorical/correlation/outlier work relevant
to actual columns and the node skill allow-list. Unknown selected columns/target mismatches are
data/parameter refusal rather than fabricated values. No row drops or contract mutation here.
If casts are needed, derive from declared feature roles, keep the resulting shared frame ref for
node 4; casts are structural, not learned. Record narrative/findings from typed LLM reading of
actual results, with whole-input EDA disclosure. Resolve automatic classification metric from
observed summarize_target class shares; unavailable/contradictory class evidence escalates.
Do not rerun problem goals after seeing EDA. Preserve generated profile/plot refs.

### Splitting

Typed SplitDecision chooses only absent strategy and explains dependency concerns; contract
strategy, ratios, seed, duplicate policy, missing threshold and roles always win. Stratified
default for classification, random for regression. Temporal/grouped explicit request OR decision
indicating such dependence -> data refusal before any row edits/split; no random fallback.
Validate strategy with existing SplitRules. Derive shared deterministic structural changes from
contract: dtype casting, exact duplicate removal when enabled, high-missing row removal only at
contract threshold. Keep target and traceability IDs; exclude only explicitly excluded features
while preserving target, IDs and columns needed for contract split semantics. No learned transforms.
Record actual pre/post counts and dropped-null-target count returned by split tool.

Do not duplicate split tool minimum-size/class rounding logic: split_random/split_stratified
already refuse and write no split files below D33 floors. Use their data refusal and call
verify_split on the returned train/valid/test refs. `passed=False` stops the node; no retries with
new seed or size. This is the sole permitted structural test-read before champion selection.
B6 already persists the write-once fingerprint when splitting produces the test artifact, before
verification; preserve that lineage/audit record even when verification later refuses the node.
After successful verification confirm the returned fingerprint with set_test_fingerprint/get_run;
existing different fingerprint is a data refusal, existing identical value is idempotent. Return
shared train/valid/test refs and report only on verified success; locked test registration stays
B6 client's responsibility.

## Tests and validation

Explicit user instruction: **do not execute tests, coverage, mutation probes or live calls**.
Author meaningful offline tests for later execution: typed-schema rejection/contract authority;
first-four node flow with fake LLM/ToolBoundary; immutable goals and balanced/imbalanced automatic
metric resolution; explicit metric override; guided action hold/approval; temporal/grouped no calls;
split refusal and verify failure do not publish a completed split boundary; emulate B6 fingerprint
persistence on artifact production, including verification failure; fingerprint idempotence/conflict;
no test-read except verify_split; missing prior boundaries; normal cancellation propagation.
Use typed positive construction and model_validate for negative Pydantic cases. Do not test
unreachable states by bypassing validators. Focused selector when authorized later:
`make test-fast TESTS='tests/test_nodes_foundation.py tests/test_domains_base.py'`.

Worker may run scoped Ruff check/format and mypy on owned Python files and git diff --check;
no pytest/collection, full make check, SDK/environment probes or imports that call a provider.
Record exact genuine exits/logs and product hashes; tests are unexecuted, not green.

## DoD and stop conditions

Stop at implemented/static-clean terminal handoff (test execution waived). Report exact files,
API names, base commit, hashes, commands/exits/logs, unexecuted tests and stage-27b hazards.
Report architecture/permission mismatch before widening scope. No worker commits/plans; main
reviews dependencies then writes 27b detailed spec. Completion/receipt must disclose missing runtime
acceptance; no claim of 100% coverage or working full agent. User authorized main automatic local
commits on finished work; no push.

## Review decision

2026-10-04: initial spec incorrectly deferred fingerprint creation until verification. The existing
B6 client persists it on split-tool success (`mcp_client.py`), recording the actual produced test
artifact before further reads. Preserve this accepted security behavior; verification failure
prevents a completed split boundary but retains fingerprint and tool audit. No MCP correction.
