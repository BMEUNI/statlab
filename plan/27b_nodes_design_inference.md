---
status: completed
dependencies: [13, 15, 27a]
completion_date: 2026-10-04
implementation_notes: Implemented and reviewed after 27a; B9 static acceptance exit 0 under user test stop. Runtime/coverage acceptance outstanding; see receipts/B9.md.
---

# Chunk 27b — experiment design and shared inference

## Goal

Implement shared nodes 5–6 using 27a's immutable runtime and B6/B8 seams. Validate a reasoned
shortlist before fan-out, bootstrap its initial branches through the existing generic planner,
and fit exactly one canonical inferential companion when node 2 fixed inference goals. No
candidate execution, champion/report exports, API assembly or persistent gate handlers.

## Prerequisites and accepted evidence

Base `3348f9d`; 27a terminal handoff adds DomainRuntime, FoundationExecutor, typed ProblemSpec,
shared_result and problem_from_state. Scoped six-file mypy/Ruff/format and diff checks exit 0;
tests authored but unexecuted. Reuse accepted B6 clients/dispatch/lineage and existing inference
tool known-answer/Holm/MNLogit evidence; B8 fan-out/checkpoint behavior remains evidence for
unchanged paths. Engine bootstrap is new and must be labelled runtime-unverified.

Read PLAN D5/D12/D18/D25–D29/D31–D35, §4.2, §4.3 and normative §4 spine. D31's inferential
matrix combines train+valid, never test; D34 train-only fits govern predictive candidates.
Existing accepted assemble_inference_matrix already does canonical imputation/dummy coding,
missing indicators and collinearity removal; do not rebuild it in backend or change MCP tools.
Search ENVIRONMENT for touched Pydantic/MCP facts only; no dependency upgrades/probes.

27a hazards: ambiguous target type needs explicit contract; automatic metric needs successful
profiling (skipping it requires explicit metric); B6 records fingerprint before verification;
approval callback deliberately lacks durable gate answers/resume. Preserve these contracts.

## Exact owned files

- `backend/statlab/domains/decisions.py`: add design and inference decision models only.
- `backend/statlab/domains/tabular.py`: replace node-5/6 schemas and declare tabular traversal.
- `backend/statlab/domains/design_inference.py`: new nodes, shared readers, initial planner.
- `backend/statlab/graph/engine.py`: generic initial-candidate bootstrap only.
- `mcp_server/statlab_tools/registry.py`: estimator family metadata only.
- `mcp_server/statlab_tools/tools/training.py`: expose family through list_estimators only.
- `skills/modelling.md`: correct node-6 Purpose/one heuristic to train+valid per D31.
- `tests/test_nodes_design_inference.py`: new offline tests, not executed.
- `tests/test_candidate_fanout.py`: meaningful initial-bootstrap/restart offline cases only.
- `tests/test_registry.py` and `tests/test_tools_training.py`: family metadata assertions only.

No foundation/runtime/client/schema/storage/config edits without a concrete blocker reported
to main. Main owns plans/review/receipt. Worker is not alone; preserve others' dirty work.

## Frozen behavior and API

### Registry and design

Expose registry-owned family (`linear`, `random_forest`, `hist_gradient_boosting`) on every
estimator entry and in list_estimators.summary.estimators. Ridge/logistic/linear regression
are linear. This is additive metadata; no estimator construction/fit/scorer behavior changes.
Do not duplicate a server registry or import it into backend. Existing interpreted baseline
metadata identifies the current inexpensive linear models without inventing timing scores.

Typed DesignDecision wraps existing CandidateSet, family justifications and a rationale.
DesignInferenceExecutor(runtime) dispatches only node 5/6 and refuses deferred nodes. Use
runtime.decide/approve/call, immutable skill allowed context, current execution identity and
problem_from_state; no new LLM/client/repair/event/checkpoint machinery in domain code.

Node 5 calls list_estimators(task=effective task), reads actual profile/problem and memory,
and obtains a typed shortlist, normally 3–5 but bounded by max_candidates, minimum one.
Preserve CandidateSet's validators for baseline uniqueness/hash dedupe/variation/label rules.
Additional reachable constraints, absent from CandidateSet: estimator is listed for task and
inside any supplied modelling.candidates pool; baseline is registry-interpretable/linear;
cap candidates; >2 per family requires nonblank explicit family justification; class weighting
requires registry support; calibration is classification-only. Contract explicitly supplied
class_weight/calibrate apply to every candidate (model_fields_set); absent fields may vary.
Candidate missing.drop_rows_above must equal contract's shared pre-split value (D34d), even
when absent. Explicit preprocessing/feature defaults constrain candidates where supplied;
validate named interactions/datetime/binning/per-column missing choices against observed
columns, target exclusion and contract feature pool. No post-split row edits or computation.
Parameter values remain server validation's responsibility; do not fit an estimator to probe
params or invent a schema from default_grid. Rationale's EDA relevance remains decision/gate
judgment; no fake string-matching evidence validator or test quota.

Guided mode returns awaiting_user before any candidate execution; validate edited decisions
again. Tool registry discovery is fixed evidence and may precede approval. Return approved
decision/CandidateSet and registry evidence in durable NodeResult.summary, with warnings.
Do not invent candidates.json artifacts; final exports remain chunk 29. If caller supplied
branches, approved global shortlist must match those specifications; never silently train
different precreated SQL candidates. New-wave design may validate the full cumulative set.

Provide candidate_set_from_state(state) reading latest successful design output, validating
it through CandidateSet and raising EngineError for missing/malformed checkpoint output.
An async initial_candidate_planner(state, wave) returns approved specs only for wave 1 with
no existing branches; otherwise returns empty. Later-wave LLM proposals/retunes belong to 28.
Declare TABULAR_TRAVERSAL using existing overlap/design/training/evaluation names in domains/.

### Generic initial bootstrap

Reachable failure: current RunLoop._fanout refuses empty branches before executing the
shared prefix, so node 5 cannot supply an initial shortlist. Reuse CandidatePlanner; no new
callback/protocol and no domain/node literals in engine. With a nonempty candidate region,
empty branches and configured planner/design node, execute prefix through approved design,
then call planner(latest, 1), validate CandidateSet and max_candidates, assign deterministic
c_01... IDs/position=region start, ensure SQL candidates, checkpoint branches before launching
overlap/candidates. Guided/failed prefix stops without invoking planner. Keep existing refusal
when no planner, empty/invalid proposal or missing approved design boundary. Observe control
and budget boundaries; do not launch work after a stop. Preserve supplied-branch behavior.

Restart with saved prefix/no branches reads the durable design; planner does no new LLM call.
Restart with saved branches does not re-bootstrap. Use existing checkpointer, SQL ensure and
next-wave validation patterns; no gates/events beyond existing boundary ownership. Bootstrap
does not mark a run complete or execute the unavailable candidate nodes in this chunk.

### Shared inferential analysis

Read fixed goals from node-2 ProblemSpec. No goals -> skipped with reason, no LLM/tools/fit.
With goals, typed InferenceDecision selects supported registry model, predictors, explicit
categorical reference levels, rationale and optional residual plot. Contract inferential_model
wins; otherwise default/match continuous OLS, binary Logit, multiclass MNLogit, count Poisson
GLM. Gamma is available only when explicitly compatible continuous contract choice; tool
checks positive target. All predeclared goal predictors must be included; extra controls are
exploratory. Require unique existing eligible predictors excluding target/IDs/excluded features;
reference keys must be selected predictors. No formula evaluation or invented transformations.
Registry evidence from node 5 is authority for model tool/family/target-type compatibility.

Approve before canonical matrix/fits. Read verified train and valid from splitting boundary;
never read or pass test. Call assemble_inference_matrix once, retaining matrix and manifest.
Call exactly one registry-derived fit tool with fixed target/family; no refit/candidate-local
choice, tuning, scaling or predictive selection. Preserve tool refusal; never silently switch
model on separation/convergence issues. Call coefficient_table with original fixed goals,
inferential_fit_summary and compute_vif. test_residuals is OLS-only (normality/heteroskedasticity;
autocorrelation only when ordered context supports it); influence/optional plot supported for
OLS/Logit/GLM, omitted with explicit warning for MNLogit. Respect existing tool applicability.
Do not add unsupported robust covariance, negative-binomial or per-equation diagnostics.

Return model/decision, matrix/manifest/fit/coefficient refs, actual diagnostics and warnings
in NodeResult; retain all artifacts for reporting. Existing coefficient_table supplies joint
tests, raw/Holm-adjusted p-values, goal alpha and untestable labeling; preserve results without
recomputing statistics or making causal claims. Finite nonconverged tool output is reported
with warning, not interpreted as a successful scientific conclusion. Tool/LLM domain errors
keep their classification; cancellation propagates. No inference report artifact fabricated.

## Tests and checks

User stop on tests remains active: **no pytest, collection, coverage, mutations or paid calls**.
Author offline cases for legitimate CandidateSet-valid external violations (registry/family
cap/pool/max/contract row threshold/baseline/support), edited approval revalidation, goals fixed,
empty-goal skip, one fit/no test refs, target-family match, MNLogit diagnostics omission, tool
refusal/cancellation and missing/malformed shared outputs. Reuse existing Pydantic validation;
negative cases use model_validate, no model_construct or validator bypass.
Bootstrap cases: empty initial state plans after design only, deterministic IDs/SQL/checkpoint,
guided/failed prefix no planner, stop control, max/empty refusal, restart uses durable shortlist
and does not repeat design/LLM, supplied branches unchanged. Do not repeat accepted B6 known
answer/Holm/MNLogit mutation tests; those tools are unchanged.

Future focused selector when authorized:
`make test-fast TESTS='tests/test_nodes_design_inference.py tests/test_candidate_fanout.py tests/test_registry.py tests/test_tools_training.py'`.
Worker runs scoped mypy/Ruff/format and diff check with unique logs; report genuine exits,
exact selectors/file hashes and unexecuted runtime cases. No global gate or commits.

## DoD / stop

Terminal implemented/static-clean handoff, tests explicitly unexecuted. Give exact API names,
commands/exits/logs, hashes, candidate identity and next-stage hazards. Stop on architecture
or ownership blocker; no unrelated guards/dependencies. Main consolidates B9 review, runs
one static acceptance pass under user test waiver, records receipt and commits exact files.
Runtime/coverage acceptance and full-agent assembly remain unproved and visibly deferred.

## Review addendum

Consolidated B9 review preserves the existing RunState invariant: exhausted initial fit budget
raises EngineError before planning/launch rather than fabricating a failed state without a
pending node. Runtime recovery of this new edge remains unverified under the user test stop.
The foundation spine test must now assert node-5/6 concrete schemas and reserve unavailable
schema assertions for nodes 7–13; this is a sequential 27a test correction, not a new guard.
