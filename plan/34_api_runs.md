---
status: implemented
dependencies: [22, 32, 33]
completion_date: 2026-10-05
implementation_notes: "B13 implemented/reviewed after33 terminal; final global statics exit0. Lifecycle/gates/forks/restart/task ownership preserved; runtime/coverage outstanding, receipts/B13.md."
---

# Chunk34 — run API and caller-owned execution

## Goal, non-goals and prerequisite evidence

Implement PLAN §11 run creation, state snapshot, messages, typed gate answers and controls,
including rewind201/new_run_id. D4/D5/D8–D18/D27/D32/D36 and WHATS_NEXT lifecycle/restart/
ownership requirements apply. Reuse33's reviewed terminal handoff above, accepted B6 client
contracts, B8 historical behavior and B9–B12 reviewed/static-only domain/control seams. Runtime
and coverage remain explicitly unverified. No pytest, collection, startup, probes, mutations,
replays, paid calls or live server. No B14/SSE/frontend, report/artifact routes, schema migration,
out-of-band chat, queues, thread preemption, Task.cancel or multi-process coordination.

Main has inspected affected functions and callers: engine load_state/ensure_audit_anchor/run,
lifecycle start/resume/pause/cancel/exclusive; all GateService transitions/reconcile; ForkService
publication/resolver/retry, project_frontier, publish_retained_report/champion_provenance;
RestartReconciler's scan/repair/interruption; DomainRuntime/context/decide/approve/call;
TabularExecutor, TABULAR_DOMAIN/TABULAR_TRAVERSAL, CandidateSearchPlanner and
candidate_retune_decider; ControlRouter/ControlContext/PromptContext; repositories, DB DDL,
layouts/storage and existing restart/control/graph tests. No source discovery/design delegated.

## Exact ownership and reuse

Only these thirteen files:

1. New backend/statlab/domains/binding.py — RunBinding and bind_tabular composition.
2. New backend/statlab/api/runs.py — HTTP models, RunRegistry, runs_router.
3. backend/statlab/api/projects.py — append APIResources.environment only.
4. backend/statlab/main.py — request admission, registry lifespan/boot/drain and run router.
5. backend/statlab/persistence/models.py — SessionRow, MessageRow/MessageRole only.
6. backend/statlab/persistence/repository.py — five session/message functions only.
7. backend/statlab/graph/reconciliation.py — all-project preparation/cap/task names.
8. backend/statlab/graph/rewind.py — retry target guard and optional handler request_id.
9. New tests/test_domain_binding.py.
10. New tests/test_api_runs.py.
11. tests/test_repository.py — new session/message cases only.
12. tests/test_restart_reconciliation.py — all-project/cap cases only.
13. tests/test_control_rewind.py — retry target mismatch case only.

No other files, dependencies, config, architecture/status/plan edits, commits or nested agents.
Worker is not alone; preserve33 and others' edits. Missing signatures/decisions or unexpected
failures stop affected work and return exact diagnostics to main before any workaround.

Reuse ProjectsService.project/paths/dataset/validate and require_complete (api/projects.py),
get_contract/get_run/list_projects/list_runs/list_gates/list_node_executions/create_run/
update_run_status and new_id/utc_now, transaction; memory.read_memory; ensure_run_layout;
Checkpointer/EventBus/RunTracker; entered shared McpClient and shared LlmClient; ModelRouter/
Role/check_provider_environment; snapshot_skill/load_run_skill; all domain/graph services named
above. No provider/model literal or domain node name added outside llm/domains (tests exempt).

## Interfaces, validation and errors

All new API/row/config models frozen extra-forbid, tuple collections, typed positive kwargs.
Negative tests use model_validate/HTTP JSON. No explicit dynamic top-type token, type ignores,
coverage suppression or validator bypass. Config and response data never contain credentials.

APIResources.environment: Mapping[str,str], default empty dict via dataclass field factory;
append after policy so existing six-argument callers remain valid. main supplies effective env.

SessionRow: id:str, project_id:str, title:str, created_at:str.
MessageRole = Literal[user,assistant]. MessageRow: id/session_id/run_id:str, role:MessageRole,
content:str, meta_json:str, ts:str. metadata()->dict[str,JsonValue] parses strict finite JSON
object, using models.py's existing constant-rejector and TypeAdapter; ValueError/TypeError
become PersistenceError fixed label. No SQL/schema change.

Repository signatures (conn positional, others keyword unless noted):
- create_session(conn, *, project_id:str, title:str)->SessionRow.
- get_session(conn, session_id:str)->SessionRow|None.
- create_message(conn, *, session_id:str, run_id:str, role:MessageRole, content:str,
  meta:dict[str,JsonValue]|None=None)->MessageRow.
- list_messages(conn, session_id:str, *, limit:int=20)->tuple[MessageRow,...].
- get_message(conn, message_id:str)->MessageRow|None.
create_session rejects blank/title length>200 as PersistenceError; create_message rejects
blank/content length>8000, finite JSON failure and absent/cross-project session/run. Each write
has its own transaction; get session/run ownership INSIDE create_message transaction. Serialize
meta or {} with allow_nan=False. New IDs s_/msg_ and utc_now. SELECT back actual row. Wrap
sqlite3.Error and invalid stored row validation as PersistenceError with fixed labels/causes;
never expose messages at HTTP. get_* absent returnsNone. list_messages limit1..20 else
PersistenceError; latest by rowid DESC LIMIT then reverse for chronological order (rowid
resolves timestamp ties). No mixed-project/session query. Reads/writes synchronous same loop.

RunCreate: mode:RunMode REQUIRED; dataset_ids:tuple[str,...] exactlyone via min/max_length1;
contract_id:str min_length1; both IDs reject whitespace-only. OneCSV is current real caller,
no implicit first item of a multi-source request. RunAccepted run_id:str. ForkAccepted
new_run_id:str. RunSnapshot run:RunRow, state:RunState, gates:tuple[GateRow,...],
execution_failed:bool=False. state.spine already supplies graph status, no second graph model.

RunConfig persisted in runs.config_json: dataset_id:str min_length1,
dataset_sha256:str pattern ^[0-9a-f]{64}$, session_id:str min_length1,
memory:tuple[MemoryRow,...] REQUIRED, fork:dict[str,JsonValue]|None=None. Serialize with
model_dump_json. Existing ForkService clones config and supplies fork object; accept it intact.
Validate config on each fresh bind; memory rows must belong to project and each value() finite.
Never reload project memory for an existing run; fork inherits captured tuple and session.

GateSubmission: revision:int default0 ge0, action:GateAction, edits:dict[str,JsonValue]
default_factory=dict, instruction/reason/target:str|None defaultsNone. No gate_id field. Build
GateAnswer with path gate_id and typed fields; its existing validators enforce iff fields and
nonblank values. Catch ValidationError ->422 fixed invalid request. Do not duplicate validator.

ControlRequest: action:Literal[pause,resume,cancel,rewind]; target:str|None=None;
request_id:str|None=None, length1..128 when nonnull. Nonblank strings. Both optional fields
required exactly for rewind and forbidden otherwise. No hidden/generated ID for direct retry.

Run routes: POST /api/projects/{project_id}/runs ->201 RunAccepted;
GET /api/runs/{run_id} ->200 RunSnapshot;
POST /api/runs/{run_id}/messages ControlText ->200 ControlResponse;
POST /api/runs/{run_id}/gates/{gate_id} GateSubmission ->200 ControlResponse;
POST /api/runs/{run_id}/control ControlRequest ->200 ControlResponse, rewind201 ForkAccepted.
runs_router(registry:Callable[[],RunRegistry])->APIRouter uses these exact paths. Response
model for control is ControlResponse|ForkAccepted and Response status201 for rewind.

Missing run/project/owned contract/dataset/gate ->404 with respective fixed detail 'run not
found', 'project not found', 'contract not found', 'dataset not found', 'gate not found'.
Wrong schema/input ->422 'invalid request'. Partial contracts retain require_complete's
422 'contract is missing required setup fields'; MCP validation false ->422 'contract does
not match dataset'. Stale gate, unavailable action, duplicate execution,
illegal lifecycle/rewind ->409 fixed 'run transition rejected'; catch EngineError only around
actual control service calls, not around binding/recovery corruption. Capacity409 detail object
{message:'run capacity exhausted',run_id:<durable affected id or null before creation>}. Closing
admission/new scheduling ->503 'application is closing'. Existing33 Tool/LLM/Config/statics/
SQLite/OS handlers remain fixed and cancellation propagates. Corrupt config/snapshot ->
PersistenceError500. No stringify of causes. Invalid startup provider remains ConfigError503.

## Ordered production recipe

### 1. Session/message persistence

Add models/functions exactly above using current repository transaction/read-back conventions.
Wrap only new functions' foreign boundaries. Do not refactor unrelated repositories. Existing
tables already have required FKs/columns; no migration. Read ownership before message INSERT
inside same transaction; create_session project FK supplies ownership. Metadata canonical
serialization uses sorted keys/separators; message content preserved verbatim. No memory write.

### 2. Shared restart and fork corrections

RestartReconciler.reconcile(project_id:str)->RecoveryBatch remains compatible and delegates to
new reconcile_projects(project_ids:tuple[str,...], *, max_concurrent_runs:int=0)->RecoveryBatch.
Reject negative cap EngineError before any preparation. Deduplicate project IDs preserving
order. Repeat scans of list_runs for all supplied projects, skipping seen run IDs, until a full
pass finds no unseen rows. Mark every encountered ID seen even terminal skip. This includes
children created during gate.reconcile in earlier scan. For each row retain current preparation
algorithm and order verbatim: terminal exception for answered/prepared/applied gates; resolve;
tracker identity; load checkpoint cursor; gate.reconciler; refresh SQL; paused/cancelled/
superseded status projection; boundary repair; interrupted attempts; dormant checkpoint/status/
gate event repair or runnable list. Gate hooks must already be installed by resolver.
No task creation until EVERY scan finishes. If positivecap and runnable count>cap, EngineError
before any task creation. Then create each existing lifecycle.start task with name
'statlab-run:'+binding.initial.run_id. Caller owns tasks. Preserve all current exception mapping,
audit/test claims/immutable artifacts. Existing reconcile(defaultcap0) tests unchanged.

ForkService._existing(request_id:str, *, target:str)->ForkResult|None: in existing published
request match, require fork['target']==target BEFORE checkpoint load/resolver/parent finish;
else EngineError('fork request target differs'). fork's sole _existing call supplies target.
Keep all projection/publication/ancestry/report algorithms untouched. This guards every retry.
ForkService.handle's three positional args remain, add keyword request_id:str|None=None and
pass it to self.fork(intent.target, request_id=request_id). Existing callers retain default;
API message callback supplies durable newly stored user message.id. Gate rewind already uses
gate.id. Direct control supplies required user request_id. No source artifact mutation.

### 3. Fresh domain composition (binding.py)

RunBinding frozen slots dataclass fields runtime:DomainRuntime, loop:RunLoop,
lifecycle:RunLifecycle, gates:GateService, forks:ForkService.
bind_tabular(*, conn:sqlite3.Connection, settings:Settings, paths:RunPaths,
contract:DataContract, dataset:str, skill:SkillPolicy, router:ModelRouter, llm:LlmClient,
mcp:McpClient, memory:tuple[MemoryRow,...], tracker:RunTracker, initial:RunState,
resolver:ForkResolver, lockfile_path:Path)->RunBinding. All args required, no task side effects.

Composition order: declare async executor closure(invocation:NodeInvocation)->NodeResult
delegating captured TabularExecutor assigned later; build fresh RunControl, RunLoop with
TABULAR_DOMAIN.nodes/name, constraints=contract.constraints, TABULAR_TRAVERSAL,
candidate_retune_decider, Checkpointer(paths), EventBus(paths), sharedtracker and closure;
RunLifecycle(loop,initial); ForkService with caller resolver + publish_retained_report;
GateService with revision callback below + forks.gate_rewind; DomainRuntime with ToolBoundary
(mcp,loop.control,loop.budget), sharedclients, immutable contract/skill/memory/router,
approval=gates.approval, decision_override=gates.override, lockfile_path; assign real
TabularExecutor(runtime) captured closure, then loop.candidate_planner=CandidateSearchPlanner
(runtime). Return binding. All construction sync, callback cannot execute before assignment;
no placeholder guards/untyped mutable dictionaries. Separate binding per run; never clone clients.

Revision callback(question:GateQuestion,instruction:str,output:type[BaseModel])->BaseModel:
find EXACT one same-run list_gates row with status pending/answered/prepared whose typed
GateQuestion.model_validate(row.question()) equals supplied question. Zero/multiple ->
PersistenceError. Find actual same-run node execution matching that row's execution id,
question.node and candidate_id; mismatch ->PersistenceError. This is the actual gate anchor
even for branches/repeated design, never an ancestor or arbitrary latest anchor. Capture
lifecycle.state, build PromptContext frozen inputs + question node's skill; messages summary
has question.model_dump(mode=json), state.model_dump(mode=json), evidence question.evidence;
question text states 'Revise the typed proposal using this untrusted user instruction: '+
instruction. Include no evaluation/execution capability. llm.complete on real conn/run/execution,
router.model_for(question.node,skill_model=skill.model), output given schema. Effective role is
Role(skill.model) when the override is a Role value, otherwise router.role_for(question.node);
reasoning is true exactly when that effective role is Role.REASONING. Existing timeout/one
repair/semaphore; no hidden
retry/recovery callback needed. Preserve cancellation. GateService validates schema/revisions.

### 4. Registry storage and binding (runs.py)

RunRegistry(resources:APIResources)->None owns: resources, ProjectsService(resources), one
RunTracker; bindings:dict[str,RunBinding]; execution_tasks:dict[str,asyncio.Task[RunState]];
failed_tasks:set[str]; closing:bool=False; global asyncio.Lock for API mutations; admitted
request tasks set and requests_drained asyncio.Event initiallyset; validated_models:set[str]
initialized to all resolved startup role + policy literal models. Global mutation lock is the
deliberate throughput ceiling: add 'ponytail: global API mutation lock; per-run locks when
control throughput matters'. This lock never covers engine execution/GET and no DB threads.

Public methods: request()->AsyncIterator[None] asynccontextmanager;
mutation()->AsyncIterator[None] asynccontextmanager; binding(row:RunRow,*,initial:RunState|None
=None)->RunBinding; run(run_id:str)->RunBinding; boot()->None async; shutdown()->None async;
create(project_id:str,body:RunCreate)->RunAccepted async; snapshot(run_id:str)->RunSnapshot
async; control(run_id:str,body:ControlRequest)->ControlResponse|ForkAccepted async;
answer(run_id:str,gate_id:str,body:GateSubmission)->ControlResponse async;
message(run_id:str,body:ControlText)->ControlResponse async. Private methods:
_capacity(run_id:str|None=None)->None; _launch(binding:RunBinding,*,resume:bool=False,
deduplicate:bool=False)->bool; _adopt(run_id:str,task:Task[RunState])->None;
_completed(run_id:str,task:Task[RunState])->None;
_settle_gate_task(binding:RunBinding)->None async;
_continue(binding:RunBinding,response:ControlResponse)->None async;
_resume(binding:RunBinding)->ControlResponse async;
_lifecycle(binding:RunBinding,message:str,classified:InterruptClass,context:ControlContext)
->ControlResponse async. Only these helpers plus local endpoint/callback closures; no framework.

request: reject closing503, obtain current_task (missing ->EngineError), record task identity,
clear drainedEvent; yield; finally remove and setEvent when no admitted requests. Do not await
entire HTTP actor tasks at shutdown (test/client actor can outlive one request); await this
logical admission Event. mutation holds globalLock only. Previously admitted requests waiting
on lock may finish during closing, but _launch must refuse new work503. No cancellation catch.

binding: return cached binding if present (for supplied fork/recovery initial set lifecycle.initial
to that exact state, preserving same bus/control/runtime). Fresh: verify project exists; validate
RunConfig, owned contract and dataset, session sameproject; memory ownership/value. Validate SQL
run id regex ^r_[0-9a-f]{16}$ before RunPaths. ProjectService.paths validates project dirs. Run
root must resolve beneath project.runs; all layout child dirs plus state/events/snapshot/uv.lock
paths must resolve beneath root. Contract stored path beneath project.contracts, file present
and bytes equal SQL contract_json encoding. Dataset row SHA==config SHA; owned path/file and
sha256 actual file bytes==config. CSV hashing bounded64kiB reads, no compute import. Runtime
contract require_complete; convert its internal422 to PersistenceError. Snapshots contract/
skill/uv.lock must be files and contract snapshot bytes equal SQL contract_json, no regeneration
on restart. load_run_skill with TABULAR_DOMAIN.nodes and entered resources.mcp.tools; precheck
skill snapshot exists BEFORE call. Resolve literal models from loaded old policy; check any
models outside validated_models with resources.environment, add only after successfulcheck.
No secret exposure. initial absent ->RunState.initial(run_id,rowmode SQLCURRENT, declared
domain.name/nodes); suppliedinitial exactly as provided with matching runid/spine/SQLmode else
PersistenceError. It may be pending while SQL is cancelled during trusted unpublished fork
binding; do not reject that construction status. Call bind_tabular, resolver closure looks up
child SQL row and calls this binding(childrow,initial=childstate), no scheduling. Cache only after
complete successful composition. Invalid stored models/config/paths wrap PersistenceError;
ConfigError provider fail closed stays ConfigError. run lookup404 then binding.

boot: collect all list_projects IDs; RestartReconciler resolver(row) calls binding(row) then
RecoveryBinding(loop,lifecycle.initial,lifecycle). Call reconcile_projects once with settings
max_concurrent_runs. No binding resolver launches work. Immediately adopt every returned task
using exact 'statlab-run:' name suffix; matching cachedbinding required. Adopt synchronously
before lifespan yield. No extra per-project reconcile loop or task race before preparation.

_capacity counts all notdone execution_tasks (includes scheduled but notstarted); zero cap
unbounded; positive count>=cap ->409. _launch checksclosing, existingnotdone: deduplicate returns
False, otherwise409. Checkcapacity, create_task lifecycle.resume if resume else start, taskname,
adopt; returnTrue. _adopt stores task then callback to _completed. _completed retrieves
exception or cancellation (check cancelled first), removes only if dict still points to this
task, records failed_tasks on unexpected exception/cancellation, clears failure on successful
completion. No raw logging, silent synthetic failed SQL or unowned task. Bindings retained for
one EventBuswriter. _settle_gate_task: only if list_pending_gates nonempty and owned task active,
await asyncio.shield(task); prevents gate.opened/loop-return race, HTTP cancellation leaves
engineownedtask untouched. Failure remains domain/HTTP boundary, no test-score cleanup.

### 5. Creation and HTTP transitions

create under mutation lock: ownedproject/contract/dataset404 checks; validate completecontract,
contractpath containment/file bytes and datasetfile hash using freshbind rules BEFORE MCP;
await ProjectsService.validate, false422. Check closing503 and capacity AFTER await BEFORE SQL.
Capture read_memory ONCE. create_session(title=project.name); create_run(status=cancelled,
RunConfig allrequiredvalues); no nested SQL transaction. Validate own new run id; root must
not lexists, create root exclusively(exist_ok=False), ensure_run_layout for childdirs; contract
snapshot write via exclusive xb with exactSQLbytes, snapshot_skill(resources.policy), uv.lock
source Path('uv.lock').resolve read bytes, exclusive snapshotwrite. Require existing nonempty
locksource or PersistenceError. Initial currentSQLmode, Checkpointer.save; binding(row,initial);
update_run_status pending only AFTER files/checkpoint/binding succeed; _launch; return201id.
Any IO/config/cancel failure preserves diagnostic cancelledrow/partialfiles, never schedules
unpublishedrun, no destructive cleanup. If closing begins during lastawait and publication
already happened, shutdown will drain/pausedpending; _launch503 truthful. No run cancellation
on failed capacitybeforeSQL. No nextnode/execution is manually fabricated.

snapshot: run binding, await lifecycle.state (checkpointauthority), refreshget_run, return all
gates + failed_tasks membership. No control/scheduling/audit/newmemory side effects on GET.

control under mutationlock: run and actual lifecycle.audit_anchor before every inboundcontrol;
pause/cancel call real methods and await drain, return categorylifecycle text 'Run <status>.';
resume uses _resume; rewind calls forks.fork(target,request_id), _launch(child,deduplicate=True)
if child SQLpending/running; returnForkAccepted child201. EngineError from transitions ->409.
Rewinding completed source preserves original test provenance viaexistingpublisher/projection.
Retry sameIDtarget gets existingchild and never duplicate task/child; differenttarget409.

_resume: reject existing notdone409 before lifecycle task enters; SQLpaused schedules resume,
SQLpending schedules start (allows durable gate/fork continuation after capacity denial), other
statuses409 incl running without anownedhealthy task (needs restart), awaiting/terminal. Response
categorylifecycle text 'Run continuation scheduled.' immediately; lifecycle persists actual
transition. Do not await resumed modelling execution as HTTPrequest. Pausedgate remains dormant
through existing resume. _continue chooses new_run_id child else parent; schedule only current
SQLpending/running, deduplicateTrue; paused/awaiting/terminal dormant. Capacitydenial after
accepted gate/fork includes that durableid; caller may later explicitresume pending. No hiddenqueue.

answer: underlock, run, get_gate and ownedrun404, _settle_gate_task, audit_anchor real; construct
actual GateAnswer; call gates.answer; _continue; return typedresponse. All7actions retained by
existing service. Never replay captured post-action result. EngineError409 and validation422
fixed; persistencecorruption500. Gate rewinds usegateid internally, not directrequestid.

message: underlock, run + _settle_gate_task; parse immutableconfig to ownedsession; persist user
create_message before LLM, append message.user {message_id,session_id,text}; actualaudit_anchor;
capturestate. Latest20 sessionmessages -> ConversationTurn, remove oldest until totalcontent
<=16000. Captured currentnode=anchor.node_name when cursor<spine length elseNone. Build
ControlContext with real conn/sharedllm/router, immutable runtimecontract/skill/memory, state,
audit execution id, conversation, artifactsummaries=(), eta=None, recovery=None. Wire real
ControlRouter decision=gates.handle; change closure calls forks.handle(...request_id=user.id);
lifecycle closure calls _lifecycle; polishFalse. await handle. Persist assistantmessage with
contentresponse.text/meta=response.model_dump(mode=json,exclude={'text'}); append
message.assistant.complete {message_id,session_id,text,response:<metadata>}; _continue for
decision/change only (lifecycle resume already scheduled); return response. Failure leaves
honest userrow/event; no inventedassistant. _lifecycle verifies contextsameconn/runid;
resume _resume, pause/cancel actual lifecycle.handle. EngineError409 only from control services.
No main-created heuristics/classification shortcuts or tool/sample access for QA.

### 6. Lifespan and shutdown

main.create_app signature unchanged/lazy. Add active_registry:RunRegistry|None getter503. Global
FastAPI dependency admitted_request asyncyield delegates registry.request, declared
Depends(admitted_request,scope='request'); this tracks ALL API requests including project setup
calls using shared MCP/LLM. Installed FastAPI supports request-scopedyield. No app.state or
threadpool middleware. Construct dependency closure at factorytime without loadingresources.
Include runs_router(registry). Existing errorhandlers preserved. After33 startupclient/model/
policy checks and envupdate, setAPIResources environment, setregistry and awaitregistry.boot
inside try/finally spanning boot ANDyield. finally awaitregistry.shutdown while active getters,
MCP/LLM/DB still live; then clearactive_registry/resources. AsyncExitStack MCPexit afterward,
SQLiteclose outerfinally. Ensure gettersclear even shutdownraises. Boot failure also drains any
adoptedwork before cleanup; fullprep/cap failures return no tasks.

shutdown setsclosingTrue; awaitrequests_drained Event, no newrequest/launch. Then snapshot
bindings: for SQLpending, or SQLrunning with notdone healthy ownedtask, await lifecycle.pause
to drain admittedtools/candidates. Skip failed_tasks (including doneexception/cancelled task
before callback), and unpublishedcancelled/paused/awaiting/terminal rows. Refreshrun if pause
EngineError: benign only if nowterminal/awaiting/paused; otherwise collectfailure. Process ALL
bindings even onepausefails; gather all remaining ownedtasks(return_exceptions=True), retrieve
eachresult/failure through callbacks and clear completedrefs; raise firstpausefailure only
AFTER drains. No Task.cancel, backgroundclientteardown or testclaim erasure. Pendingtasks can
lose start race to pause; retrieve their exception honestly. Data/client resources staylive
until admittedrequests and engineownedtasks finish. Ordinary caller cancellation propagates.

## Written test recipe (not executed)

Tests use real repositories/Checkpointer/EventBus/services. Only tool/LLM/executor boundaries
are canned; no bypass of RunState/GateAnswer validators or fake completed score claims. Use
existing33 SetupSession/_model_response and existing graph World/new_world/node/complete,
immutable_inputs/service_for helpers where named. New typed fixturehelpers permitted only to
express the data/setup below. No new production helper solely to expose tests.

### Session/message tests — tests/test_repository.py

Use migrated_db/project/minimal_contract existingfixtures and create_contract/create_run.
Create sessiontitle 'Demo', assert get/absent and timestamps/row equality. Createuser/assistant
messages same session/run with meta {'category':'status'}, assert metadata roundtrip/listorder;
23messages tiedtimestamps via transaction setts same fixedstring -> latest20 order byrowid
matches last20 insertion IDs. limits0/21 PersistenceError. Secondproject session vs firstrun
create_message PersistenceError/noinsert. Missingrun/session same. Empty/blank/8001content,
blank/201title and meta {'bad':float('nan')} PersistenceError/noinsert. Handeditmeta_json
'{"bad":NaN}' then metadata PersistenceError. Close a separateconnect/migratedfixturecopy
connection, call new read/write functions ->PersistenceError, neverclosefixtureownedconn.

### All-project recovery — tests/test_restart_reconciliation.py

Use engine_world and create_project secondslug plus new_world(sharedconn, secondproject,
minimal_contract, tmp_path/'second',data_dir=engine_world.paths.data_dir). nodes=(node(),).
Both SQLrunning and actual runningattempts via create_node_execution; saveinitialcheckpoint.
Resolver maprunid toworld, loops sharedtracker, executor asserts BOTH oldattempts failed with
process-terminatederror BEFORE its firstentry, returnscomplete. reconcile_projects tupleboth
projectIDs ->2namedtasks; awaitboth, attempts retain old IDs/newattempt2. Existingauditretain
tests remain unchanged. Invalid secondstate_json 'corrupt' ->PersistenceError and executorentry
list empty (no task was scheduled). Positivecap1 with2runnable ->EngineError/noentries;
negativecap ->EngineError/no resolvercalls. Duplicate IDs tuple(first,first,second) ->2tasks.

Childscan test: firstworld.loop.gate_reconciler custom typed callback creates new_world only
once in firstproject and savespendingcheckpoint, returns originalstate. Resolver includes that
childworld and sharedtracker. Both originalworld + child returned namedtasks, executor asserts
all worldids resolved before entry. No new child in everycallback. This checks full preparation
without duplicating GateService's accepted fork algorithms. Awaitallownedtasks in test.

### Shared fork retry — tests/test_control_rewind.py

Extend existing service_for/immutable_inputs tests with two-node genuine complete parent using
actual lifecycle.start and complete executor. fork(firstnode,request_id='retry') returnschild;
retry firstnode sameidreturnsamechild; secondnode sameid raisesEngineError targetdiff; before/
after SQLruns/files/parentstate equal across rejectedretry, childcount1. No task scheduling in
resolver. Existing projection/wave/report/provenance tests remain unaltered/unexecuted.

### Domain composition — tests/test_domain_binding.py

Settings/data/sql fixtures: realconnect/migrate, project 'demo', complete DataContract targeta
binary positiveclass1/taskclassification/metricroc_auc/splitstratified; datasetbytes
'a,b\n0,1\n1,2\n', ownedrow/hash; entered McpClient SetupSession; ModelRouter(NODE_ROLES,{}),
load_skill(TABULAR_DOMAIN.skill_file,nodes,client.tools), shared LlmClient canned _model_response;
ensure_run_layout actualrun/createcontract; snapshotSkill/contractbytes/uv.lock suppliedfixture
bytes; real RunState.initial declaredname/nodes/SQLmode. bind two runs samecapturedMemoryRow
tuple/sharedtracker/client. Assert runtime/context allowed ISskill.node.allowed; distinct
control/loop/lifecycle/gates/forks/EventBus, sharedtracker/clients; constraints/traversal identity,
plannerinstance/retune identity; runtime approval/override bound services; revision/rewind/
publisher/hooks notNone. Construct real firstnode NodeInvocation with create_node_execution
for thatrun to inspect context; foreignexecution invocation ->EngineError.

Revision case: replace binding.loop.executor with typed asyncfunction using actualruntime.
decide(IntakeDecision,evidence=(),question='Fixture'), runtime.approve beforetool, return
awaiting_user when None elsecompleted. Canned firstLLM IntakeDecision(rationale='First').
lifecycle.start opensrealgate, write latermemoryfact, gateanswer instruct 'Revise' calls real
revisioncallback/canned IntakeDecision(rationale='Revised'); assert gatependingrevision1,
historylen1, proposalrevised, llm_calls real same-run gateexecution for bothcalls, prompts contain
capturedoldmemory and exclude newfact. Bad inherited/missinggateexecution detected by callback
via legitimate separategatequestion/no row rather than bypassingstatevalidators. Cancelled
cannedrevisionprovider ->CancelledError retainsansweredgate/no syntheticnewproposal.

### API fixtures and executor — tests/test_api_runs.py

Use real create_app settings/HTTPX ASGITransport inside SAME-TASK app.router.lifespan_context,
SetupSession registry/profile; monkeypatch main.check_provider_environment and runs equivalent
checker only for testmodels. Real LlmClient canned typedJSON deque selected percase; record
calls. No network. HTTPcreateproject/uploadCSV/upload completecontract for fixture IDs. Record
realconn via wrappermain.connect callingoriginal so testcaninspectread/pause/checkpoints; never
close whileappowned. Monkeypatch domains.binding.TabularExecutor to typed FixtureExecutor
(runtime:DomainRuntime), __call__(NodeInvocation)->NodeResult. Constructor recordsruntime
maprunid; firstnode executes runtime.decide(IntakeDecision) then runtime.approve. None ->
awaiting_user; approved ->bodycounts increment, optionally runtime.call(invocation,'load_csv',
{}) when testflag saysblocktool, optional postaction runtime.approve(after_action=True), returns
completedsummarybodycount. Every laternode signals nextentered Event then waitsnextrelease
and control.check; returncompleted only if control permits (tests pause/cancel before release).
No candidateplanning reached. For capacity/unbounded tests fixture firstnode may be configured
to signalentered and waitrelease BEFORE decide so no unwantedLLM. Fixture modes exactflags
beforeblock/aftergate/toolblock; case specifies flag. Typedfunction boundary only, realservices.
Waitinghelpers wait Event or use at most100 asyncio.sleep(0) turns checking actualSQLgate/status;
on exhaustion pytest.fail. This is written runtimecheck, NOT executed now. Tests always drain
ownedtasks via realcontrols/releases before lifespanexit; nevercancelengineownedtask.

1. Creation/snapshot: guidedcase cannedFirstDecision;201runID, awaitpendinggate, GET200 has
   declared13spine and awaitingstate/gate. Verify exact contract/skill/lock snapshots, config
   oneid/hash/session/memory tuple, SQLmode, no initial candidate launch. Unknownrun404. Create
   anotherproject then crossdataset/crosscontract404; zero/twodatasetIDs422; partialcontract422;
   queuedsetupvalidFalse422 allbeforeSQLrun creation. Invalid field/mode422fixed noinputecho.
2. Capacity: maxruns1, beforeblock flag; first201 scheduledreserves slot beforeengineenter;
   second409 detailrun_idNone and SQLone. Resumeactive409. Pausefirst inrequesttask, release,
   awaitpause200, assertpaused/noTaskcancel. Second now201. Separatecap0 case two201bothslots
   even beforeengineentry; pause/releaseboth. No queuedwork beyond ownedtasks.
3. Gateactions (freshapp/run percase): approve_with_edits rationale='Edited' consumes actual
   prepareddecision/no extraLLM; approve/instruct/approve_all_remaining/abort/rewind each goes
   through typedendpoint. Instruct cannedRevised ->200, pendingrevision1, oldrevision409 then
   approve revision1; verify history. approveall updates SQL/currentcheckpoint/initialautonomous.
   Abortcancelled/dormant. rewindtargetfirstnode ->200ControlResponse.new_run_id, childpending/
   running hasnewbindings/capturedconfig, parent superseded and requestid==gateid. Skipfirst
   nonskippable ->409; skipreasonmissing422. Accepted B12 tests already specify successful
   skippable-node behavior; do not synthesize preceding tabular boundaries for another case.
   Unknown/crossrungate404; extra submission fields and negative revision422; edits
   {'rationale':7}422 from typed proposal validation; {'unknown_field':'x'}409 from the existing
   noneditable-field guard. Postactionflag: firstaction bodycount1 beforeaftergate, approveaftergate
   leavesbodycount1 (no replay); editsaftergate409. Alwayspausenextnode thenreleasenextblocked.
4. Directrewind: awaitguidedgate, POSTcontrol rewindtargetfirstnode/request_id='r1' ->201child;
   retry sameidtarget returns201samechild/no duplicateSQL/task/bus; retrysameid different
   declaredsecondnode409 even if frontierreached notneeded (requestmatchguard first); missing/
   blankrequest_id/target, unrelatedfields pause422; unknowntarget409. Childsamecontract/skill/
   memory/session, distinctcontrol; inheritedsame-runanchor created forchild incomingmessage,
   noancestor anchor. Childfirstgate stopswork, no modellingsequence required.
5. Continuationcapacity: max1 runA awaitinggate(noactive task), runB beforeblock occupiesone.
   ApproveA ->409 detailrun_idA AFTERgateaccepted/prepared and SQLpending; release/pauseB;
   resumeA ->200scheduled and bodycount1; repeatedresume409whileownedtaskactive. DirectforkA
   is a separate required freshapp case whenBactive ->409durablechildid; retrysameid afterfreecapacity
   ->201samechild, onlyonechild. No acceptedgate answer rollback or duplicatework.
6. Messages: guidedawaitingrun; canned InterruptClass status ->200realcanonicalfacts; noise
   ->200Acknowledged; qa then QaAnswer(text='Evidence unavailable.',references=()) ->200;
   decision then GateAnswer actualid approve ->actual200continuation; lifecycle pause/resume
   via InterruptClass ->actualmethods/scheduledownedtask; change then RewindIntent firstnode
   ->actualfork using storeduser.id. Separatecases avoid terminalstatusmix. Verify user/assistant
   SQLrows/metadata/eventtypes and everyLLMaudit execution same-run; no classification-only
   effect. Two separate provider-boundary failure cases: ValueError('offline failure')->502
   and litellm APIConnectionError(message='offline transient',llm_provider='fixture',model='x')
   ->503. In each, userrow/event retained, no assistantrow, one failed real same-run audit and
   fixed response detail 'LLM request failed'; no retry because ControlContext.recovery=None.
   Insert23 bounded sessionturns and
   assert providerquestioncapturedlast20/trim<=16000, noall-history load. Do not testrawsecrets.
7. Capturedmemory/restart: writeoldfact beforecreate; runA config/runtimememoryoldtuple; write
   newfact aftergate; messageprompt excludesnewfact; newrunB capturesboth. Exitawaitingapp,
   reenter newapp sameDB and assert awaiting stays dormant with its captured memory. Separate
   required case: actual approveall then pause blocked nextnode, so both SQL and checkpoint
   mode are autonomous before exit. Restart paused stays dormant, hooks installed, captured
   memory unchanged and lifecycle.initial.mode equals current SQL mode. Never handedit mode
   or claim load_state projects a mismatched checkpoint mode.
   Oldskillliteral outside startupvalidatedmodels: the actual source policy has no Model
   sections. In the test-owned run snapshot only, insert the optional subsection heading
   '### Model', blank line and literal 'openai/old-test' immediately before the next second-node
   heading '## problem_definition'. Assert this heading occurs exactly once before insertion;
   preserve all other bytes. This represents a valid historical intake override, not a
   production snapshot mutation. runs.check_provider_environment
   records selectedoldmodel/effectiveenv, raisedConfigError bootfail beforeexecutorentry.
   Missing skill snapshot/changedcontractbytes bootPersistenceError nofallbackregeneration.
8. Shutdown: autonomous run with toolblock flag: set SetupSession.block_next only AFTER run
   creation validation, before releasing a beforeblock latch into decide/tool execution. Queue
   its IntakeDecision and block actual runtime.call load_csv using SetupSession.entered/release.
   Runtime tool call uses the existing trusted contract/dataset context and ordinary ToolSuccess
   envelope from SetupSession; no compute executes. lifespanowner signals ready waitsfinishEvent;
   separateHTTPtaskrun, shutdownstarts byfinishEvent; confirmpausewaiting/client/connlive,
   noengineTaskcancel; release tool call, lifespanends, SQLpaused+connclosed. Toolblock case
   returns its completed firstnode immediately after the tool response, then the engine observes
   pause before another node; it must not wait on nextrelease after shutdown has requested pause.
   Secondcase blockproject
   SetupSession.block_next afterstartup, shutdownwhileuploadHTTPadmitted; check session/conn
   stilllive and newHTTP503, release setup, awaituploadthenlifespanclosed. Thirdcase inject
   RunLifecycle.start typed coroutine raisingPersistenceError for thatrun only via monkeypatch;
   wait callback, GETexecution_failedTrue, SQLpending/runningdiagnostic retained (notfakefailed),
   shutdownmustretrievefailure/closecleanly; nextboot actualreconciler repairsattempts ifrunning.

Writtencases use exact existingpositive contracts/canned schemas; missing setupfunction/signature
is a mainquestion, not permission to invent behavior. No runtimecounts/coverage claims.

## Permitted checks and acceptance stop

Worker runs only these three commands over the EXACT13 ownedpaths listed above:
Each command uses this exact ordered argument list: backend/statlab/domains/binding.py,
backend/statlab/api/runs.py, backend/statlab/api/projects.py, backend/statlab/main.py,
backend/statlab/persistence/models.py, backend/statlab/persistence/repository.py,
backend/statlab/graph/reconciliation.py, backend/statlab/graph/rewind.py,
tests/test_domain_binding.py, tests/test_api_runs.py, tests/test_repository.py,
tests/test_restart_reconciliation.py, tests/test_control_rewind.py (commas delimit prose,
not shell arguments). Prefixes are `.venv/bin/python -m ruff check`,
`.venv/bin/python -m ruff format --check`, and `.venv/bin/python -m mypy` respectively.
Mechanical Ruffformat on ownedfiles permitted. No autofix behavior changes. Logs unique
`/private/tmp/b13-chunk34-{ruff-check,ruff-format,mypy}.log`. Genuine exits/full summaries,
caseinventory, SHA256 eachfile, candidateHEAD/diff and deviations/hazards in terminalreceipt.
Unexpectedstatic errors return exactdiagnostics beforeunplannedcorrection. Worker neverglobal
checks/commits/plans. Stop only once prescribedsource/cases transcribed and focusedstaticspass,
or on namedunresolvedfailure. Mainreviewscomplete diff, resolvescorrections then aloneonefinal
stableglobalstaticacceptance. Runtime/coverage remain deferred; no omittedgap describedpassed.
Finalstatus/index/WHATS_NEXT/localcommit ownedbymain AFTERacceptance; B14 remainsuntouched.

## Resolved correction A — first terminal partial handoff

The initial writer stopped correctly: Ruff check/format exited0; scoped mypy exited1 with four
errors. Original test inventory remains incomplete. This bounded correction precedes the remaining
written tests; it does not complete34. Main reviewed every production addition/diff. Only the
following six files may change in this pass; preserve all other stage33/34 edits.

1. domains/binding.py: import CandidateSearchPlanner from domains.candidate_search; keep
   candidate_retune_decider from candidate_models. Import RunControl from graph.candidates with
   ToolBoundary; engine supplies NodeInvocation/RunLoop/RunTracker only. Composition unchanged.
2. api/runs.py: _lifecycle returns the actual ControlResponse awaited from lifecycle.handle;
   retain context identity check, resume scheduling and EngineError409 mapping. No second response
   or status attribute. answer constructs GateAnswer using explicit typed keyword fields gate_id,
   revision, action, edits, instruction, reason, target from GateSubmission; remove model_dump
   expansion. create's false validation detail is exactly 'contract does not match dataset'.
   Direct rewind obtains ForkResult, then calls _continue with a typed ControlResponse(category
   'change', text 'Run forked.', new_run_id=fork.new_run_id), then returns ForkAccepted. This
   reuses the current-SQL pending/running guard and capacity detail; awaiting/paused/terminal
   retry children stay dormant. No change to durable request ID or status201.
   shutdown: before deciding pause eligibility, if the owned task exists and is done, call
   _completed to retrieve its exception/cancellation and refresh task lookup; failed_tasks then
   excludes it from pause. Catch EngineError with the existing refreshed terminal/awaiting/
   paused benign rule. Additionally catch other Exception subclasses, retaining the first
   failure without terminating the binding loop. Continue processing ALL bindings and gather
   ALL remaining tasks, then raise first retained failure. Do not catch BaseException or
   cancellation; do not cancel tasks. Existing BaseException-typed accumulator may remain.
3. tests/test_domain_binding.py: ToolBoundary.client IS shared McpClient, not nonexistent mcp;
   context allowed IS policy node allowed tuple (identity). Remaining composition/revision
   inventory follows in correction B; do not manufacture coverage now.
4. tests/test_control_rewind.py: obtain the real parent RunRow via get_run and assert present;
   use its project_id for EVERY list_runs query. Perform same-target retry first and assert
   samechild. Then capture SQL project run tuple, parent executions/checkpoint/events and sorted
   relative directory/file names beneath the project runs root. Assert exactly one row has
   forked_from_run_id equal parent ID; use this existing typed RunRow field.
   Reject different-target retry and assert all captured values unchanged, with one child.
   This corrects the empty query and excludes harmless successful-retry publication events
   from the rejected-retry comparison. No resolver scheduling or source restoration.
5. tests/test_restart_reconciliation.py: in the two-project test retain the IDs of the two
   initially created attempts. At executor entry select those IDs only, asserting both failed,
   ended_at present and error contains process-terminated wording already used by existing
   interruption tests. The newly admitted attempt is running and must not be asserted failed.
   Keep attempt-number/name/capacity assertions. Additional corrupt/child cases follow in B.
6. tests/test_repository.py: extend the existing separate closed-connection test with
   list_messages(closed_connection, 'missing') raising PersistenceError. Existing four closed
   functions already cover their boundaries; preserve fixture-owned connection.

Use exactly these six paths, in the order above, with Ruff check, Ruff format --check and mypy
prefixes from Permitted checks. Mechanical format allowed on these paths. Unique logs
/private/tmp/b13-chunk34-a-{ruff-check,ruff-format,mypy}.log. No runtime/collection/probe checks.
Stop and report unexpected diagnostics without workaround. Terminal A receipt must give actual
commands/exits/full summaries, six path hashes, diff identity and remaining inventory. Main
reviews A before sending B to this same implementer. No plan/status/commit edits or agents.

## Resolved continuation B — persistence, recovery and composition tests

Only after main reviews A. Own exactly tests/test_repository.py,
tests/test_restart_reconciliation.py and tests/test_domain_binding.py; no production edits.
Original Written test recipe remains binding. Ordered transcription:

1. Repository: extend the existing round-trip test: assert nonempty session.created_at,
   user.ts/assistant.ts and user.content exactly ' hello '. Include empty string alongside
   blank and overlength title/content negatives. Add missing session plus valid run rejection.
   Capture actual SQL message/session counts before all invalid writes and assert counts equal
   afterward; corruption of metadata occurs only after these comparisons. Use transaction
   helper for intentional tied timestamp/metadata fixture edits. Existing five closed connection
   assertions satisfy that boundary; do not duplicate or close migrated_db.
2. Recovery: add corrupt-second-project test using the exact two-world setup from existing new
   test, pending checkpoints and shared tracker. Write 'corrupt' to second state_json; resolver
   records both IDs; executor records entries. reconcile_projects raises PersistenceError,
   entered remains empty, first run has no new attempt. No scheduling/cleanup workaround.
   Add child-discovery test: original pending world one-node; resolver dictionary initially
   original binding. Its typed gate_reconciler(state:RunState)->RunState callback creates ONE
   new_world with original project/minimal_contract/tmp_path/'child', same data_dir, saves
   pending checkpoint, constructs shared-tracker loop/lifecycle and inserts binding in dictionary.
   Subsequent callback calls return originalstate without more children. Resolver records IDs.
   Executor asserts both IDs have been resolved, then complete(invocation). Reconcile returns
   exactly two named tasks (original/newchild), gather both, entered IDs equal both IDs. All
   creation is ordinary repository fixture data; no invalid gate/checkpoint bypass.
3. Composition: extend existing two-binding fixture with remember(project_id,key='old',
   value='captured-old-fact') before binding; capture read_memory ONCE and pass SAME tuple to
   both bind calls. Assert both runtime.memory IS this tuple, distinct loop.control/EventBus,
   tools.client shared, controls identical to own loop.control, budget same own loop.budget.
   Correct the remaining context.allowed equality assertion to IS the original policy frozenset;
   correction A left this identity assertion unchanged and B explicitly completes it.
   CandidateSearchPlanner instance and its runtime IS binding.runtime; retune_decider IS
   candidate_retune_decider. Bound method equality for runtime.approval/gates.approval,
   decision_override/gates.override; hooks equal actual gates.reconcile/capture/failure;
   forks.publish_domain IS publish_retained_report, revise present, rewind equals gate_rewind.
   Preserve context identity/foreign execution test. No production executor execution here.
4. Revision: add positive and cancellation parameterized cases (cancel:bool), repeating the
   exact ordinary binding setup already described, with one run, captured old memory and an
   entered SetupSession McpClient. LlmClient injected acompletion(**kwargs:object)->ModelResponse
   records kwargs['messages'] and consumes deque containing First IntakeDecision JSON, then
   either Revised IntakeDecision JSON or asyncio.CancelledError. supports_response_schema
   canned True. Replace loop.executor with typed async executor that calls runtime.decide
   IntakeDecision evidence=(), question='Fixture', then runtime.approve; None returns
   NodeResult awaiting_user, else completed. lifecycle.start produces genuine first gate and
   returns awaiting. Never resume this fixture beyond first gate. Remember new memory fact
   'uncaptured-new-fact' AFTER start. gates.answer typed GateAnswer(action instruct,
   instruction 'Revise', gate_id actual, revision0). Positive: pending revision1/history1,
   proposal.rationale Revised; two llm_calls same-run/execution ID as real gate, prompts contain
   captured-old-fact and omit uncaptured-new-fact. Cancellation: await raises CancelledError,
   gate remains answered, original proposal First/revision0, no revised event; second audit
   must follow existing cancellation behavior (do not assert a fabricated failure row).
   Missing anchor: after positive/cancel assertions, call captured gates.revise directly with
   a separately typed GateQuestion carrying every actual question field explicitly but
   reasoning 'No matching durable question'. This valid distinct question has no matching SQL
   row; PersistenceError before provider call, recorded call count unchanged. No model_copy
   validator bypass, ancestor FK tampering or extra coverage-only guard.

Exact checks: Ruff check, Ruff format --check, mypy over the three owned paths above, in that
order; unique /private/tmp/b13-chunk34-b-{ruff-check,ruff-format,mypy}.log. Mechanical formatting
permitted. No runtime/collection/probe execution. Missing helper/decision or unexpected failure
stops before workaround. Terminal B receipt lists implemented case names/remaining API cases,
commands/genuine exits/full summaries/hashes/diff identity. Main sends C only after B review.

## Resolved continuation C — complete offline API test transcription

Only after B review. Own tests/test_api_runs.py ONLY. Replace the partial executor/test with the
real-service fixture and all eight numbered API groups above. No production edits. Sequence:
fixture; creation/capacity; gates/direct rewind/continuation capacity; messages; memory/restart;
shutdown. Each group may contain parameterized tests/fresh app cases exactly as prescribed;
test counts are not quotas. The following fixes fixture mechanics and all shared decisions.

Use typed dataclasses local to this test module:
- ExecutorFlags: before_block:bool=False, after_gate:bool=False, tool_block:bool=False.
- FixtureRecords: replies:deque[str|BaseException], requests:list[dict[str,object]],
  flags:deque[ExecutorFlags], executors:dict[str,FixtureExecutor]. All empty factory defaults.
- FixtureExecutor constructor(runtime:DomainRuntime, flags:ExecutorFlags); fields runtime,
  flags, first_entered/first_release/next_entered/next_release asyncio.Event, body_count:int=0.
  __call__(invocation:NodeInvocation)->NodeResult follows original executor recipe. Compare
  invocation.spec.name with TABULAR_DOMAIN.nodes[0].name, never a one-use boolean: approved
  pre-action continuation re-enters this node. before_block latches first_entered then awaits
  first_release before runtime.decide; control.check AFTER latch. actual decide/approve as
  specified. Only approved path increments body_count. tool_block calls actual load_csv.
  after_gate calls approve(after_action=True) after bodycount/tool; None returns awaiting with
  summary body_count; completed path returns same summary. Every later node sets next_entered,
  waits next_release and control.check before returning completed. Tests always pause/cancel
  before setting next_release, so no fabricated later boundaries/candidate planning occur.
- RunAPI frozen dataclass: app:FastAPI, client:httpx.AsyncClient, settings:Settings,
  session:SetupSession, registry:RunRegistry, conn:sqlite3.Connection, records:FixtureRecords,
  project_id/dataset_id/contract_id:str.

_open_run_api(root:Path, monkeypatch:pytest.MonkeyPatch, *, records:FixtureRecords|None=None,
max_runs:int=0, setup:bool=True, existing_run_id:str|None=None)->AsyncIterator[RunAPI]
is asynccontextmanager. Settings use
root/data and root/db.sqlite, fixture SecretStr, max_concurrent_runs=max_runs,
max_concurrent_llm=1. Shared records supplied or fresh; supports_response_schema True.
Injected acompletion(**kwargs:object)->ModelResponse stores a shallow dict kwargs, pops exactly
one reply: raise BaseException entry, else _model_response(strJSON). Exhaustion AssertionError
is a fixture failure, never a fallback. Monkeypatch main/runs provider checkers to no-op by
default; individual old-model case replaces runs checker AFTER first app fixture exits.
Capture actual registry via typed constructor wrapper(resources:APIResources)->RunRegistry
calling saved real class and appending to list. Capture connect likewise to actual conn list.
Monkeypatch binding.TabularExecutor to typed factory(runtime:DomainRuntime)->FixtureExecutor:
pop flags or default ExecutorFlags, construct, store by runtime.paths.run_id, return. This
factory replaces the original suggested class patch solely to pass explicitly selected flags;
no production helper or runtime behavior substitute. Same-task lifespan+HTTPX as original.
setupTrue HTTPcreates project demo then CSV upload and complete contract upload using33 helpers,
assert201 and store IDs; no LLM setup calls. setupFalse opens existing root without writes;
require existing_run_id supplied by the restart case, get_run actual row and owned get_project,
RunConfig.model_validate_json for dataset ID and that run's contract_id; get_dataset/get_contract
assert ownership. No mutation or new SQL listing API. setupFalse is only used after that run exists.
Yield RunAPI with actual registry/conn; conn and client stay owned by lifespan/context.

Helper _create_run(api:RunAPI, *, mode:RunMode='guided', flags:ExecutorFlags|None=None)
->httpx.Response appends flags/default before HTTPcreate; body uses owned IDs.
On non201 pre-binding rejection, remove this still-unconsumed queued flags object after asserting
its identity; do not leave rejected request fixture options for a later successful bind.
Caller queues IntakeDecision JSON BEFORE create whenever not blocked-before-decision. _gate(api,run_id)
->GateRow waits <=100 sleep(0) turns for one pending SQL gate AND absence of active execution
task, then returns it; otherwise pytest.fail. This explicitly closes gate-open/task-return race.
_pause_release(api,run_id, *, first:bool=False)->None starts HTTPpause task, waits <=100 turns
until executor.runtime.tools.control.status paused, sets first_release when first else
next_release, awaits200. Repeated pausing of already paused/awaiting is unnecessary; release
any fixture latches before exit. No Task.cancel or background requests left unawaited.

Concrete clarifications for the eight original groups:
- Groups1–2: creation failure rowcounts exclude setup/session tables; compare SQL list_runs
  before/after each rejected run. Partial contract is typed DataContract() uploaded normally.
  False validation queues _validation_profile(valid=False,mismatches=[]) just before create.
  Cross resources created via actual second project/upload routes, IDs never synthetic.
  Snapshot gate waits _gate; assert actual awaiting boundary and matching gate execution.
  Lock bytes equal repository uv.lock; contract bytes SQL canonical; skill bytes policy.original_bytes.
  cap1/cap0 before_block cases queue decisions but pause before latch release, so control.check
  prevents calls and leaves replies unconsumed honestly. Compare real registry tasks/tracker.
- Groups3–5: canned First/Revised/Child IntakeDecision strings are model_dump_json of typed
  instances. Queue Child before rewind; child inherits same session/config/snapshots, new graph
  controls/events. Dormant same-request retry after child reaches its gate must leave task dict
  empty for child and state unchanged. Compare exactly one forked_from_run_id child row. Unknown
  target uses a fresh request ID, not existing request mismatch. After capacity-denied fork,
  extract durableid from detail.run_id, free B through _pause_release(first=True), queue Child,
  retry exact original request to samechild. Accepted-answer case releases B before resume A,
  then waits A.next_entered and pauses/releases A; body_count exactly1. All after-gate approvals
  assert body_count stays1, no additional first-node tool/audit; nextnode blocked for cleanup.
- Group6: queue classification/schema responses in exact dispatch order: status/noise each
  only InterruptClass; qa InterruptClass then QaAnswer; decision InterruptClass then actual
  typed GateAnswer; change InterruptClass then RewindIntent then Child decision. Verify genuine
  effects, shared client and SQL/event/audit ownership. Lifecycle message pause/resume uses
  paused awaiting gate, then real resume returns awaiting dormant; no extra decision required.
  Classifier itself omits history by existing contract: bounded-history assertion inspects the
  SECOND qa provider request's user content starts with canonical state-summary JSON; parse the
  portion before the first double newline and inspect its conversation, not classifier.
  Create23 ordinary messages with content unique prefix plus 1000 'x' chars, then incoming qa
  text. Expect <=20 turns and <=16000 text chars after oldest trim; last is incoming message,
  oldest inserted prefixes absent, retained text preserved. Positive memory prompt assertion
  also inspects qa request (classification has no memory). Failure response/audit recipe stays
  original; both cases one failed call, no assistant and no retries.
- Group7: setupFalse reuses root/DB on new app; records cleared/executors fresh. Dormant
  awaiting/paused restart has NO tasks/executor entry. Compare captured config memory IDs/values
  and new bindings runtime.memory to original serialized tuple; later project facts affect
  only newly created run. Positive mode case approve_all then nextentered pause/release yields
  actual SQL/checkpoint autonomous. Old-model/missing snapshot/corrupt contract each fresh root,
  exit awaiting first, alter test-owned fixture file as original recipe, new app boot fails
  named domain error before factory/executor admission; no regeneration or synthetic state.
- Group8: owner coroutine enters _open_run_api, stores RunAPI in one-element typed list,
  signals ready and waits finish Event. Test waits ready then performs separate HTTPtasks;
  setting finish initiates lifespan shutdown. Tool case first before_block+tool_block flags;
  queue decision, create, wait first_entered, set SetupSession.block_next then first_release,
  wait session.entered; set finish, wait registry.closing/control.paused, assert conn SELECT1
  works and resources.mcp._session IS session until session.release. Await owner, then conn
  execute raises sqlite3.ProgrammingError and mcp._session None; reopen DB to assert paused.
  Setup case block dataset upload request, finish, registry.closing; separate new request503,
  conn/session live until release, await upload and owner. Failed-start patch is async typed
  RunLifecycle.start(self)->RunState raising PersistenceError('fixture start failed') only
  unconditionally in this sole-run fixture; install after boot and before create, no product diagnosis.
  Wait failed_tasks recorded; snapshot execution_failed true, diagnostic SQLstatus retained,
  shutdown clean/client cleared. Preserve original method for fresh restart outside patch.

Run Ruff check, Ruff format --check, mypy over tests/test_api_runs.py only; unique
/private/tmp/b13-chunk34-c-{ruff-check,ruff-format,mypy}.log. Mechanical formatting permitted.
No runtime/collection/probe checks, no claims behavioral cases pass. Terminal receipt includes
every numbered group's case names and genuine statics; missing decision/failure stops affected
work before workaround. Main reviews complete tests and owns final stable global static gates.

## Runtime recovery C1 — interrupted transcription

The C implementer turn errored on account usage limit before a terminal receipt. Main read-only
inspection found tests/test_api_runs.py absent and all three C log paths absent. This is an
orchestration/resource failure, not a product failure; no C check or behavior is accepted.
Preserve all other dirty paths and previously reviewed A/B work. Same stage implementer resumes
only tests/test_api_runs.py: recreate it from the original eight-group Written test recipe and
the complete resolved C fixture/control-flow decisions above. No source restoration by main,
new design, reduced inventory or model escalation. Run only the three C file-scoped statics
after transcription; report genuine commands/exits and terminal inventory. If the usage limit
recurs, stop without modifying other files and report the exact runtime failure. B13 remains
pending; no final global gate or finished-work commit until this handoff is reviewed.

## Resolved correction C2 — typed fixtures and existing case correctness

C1 terminal stopped with nine scoped mypy diagnostics; Ruff check/format final exit0.
Main read the entire partial test file and relevant model, lifecycle, gate and prompt callers.
Only tests/test_api_runs.py is owned. Complete this smaller correction before remaining case
groups are assigned. Preserve all production and previously accepted test work.

1. Imports/capture: use already imported persistence.db.connect as capture_connect's saved
   callable, never access main_module.connect as a statically exported symbol. Keep setattr
   targeting main_module. Import APIConnectionError from litellm.exceptions, as existing
   test_llm_client does; never access litellm.APIConnectionError. Every optional repository
   result is assigned to a local and asserted non-None before dereference (contract snapshot,
   parent status, continuation row, new memory run, reopened shutdown row, failed-start row).
   No casts/ignores or production export changes.
2. Partial contract correction: DataContract requires target. Construct the legitimate positive
   partial DataContract(target=TargetSpec(column='a')); all other defaults remain unresolved.
   Upload one complete contract to the OTHER project for cross-contract404; upload this partial
   contract to the OWN project for incomplete422. Foreign dataset/complete foreign contract
   both404, owned partial422; each leaves owned project run count unchanged. This supersedes
   DataContract() in original/C prose. No negative constructor or validator bypass.
3. _gate filters all SQL gates to status pending, requires exactly one pending gate and no
   unfinished owned execution task, then returns that pending gate. Historical prepared/applied
   gates remain present. Preserve <=100 sleep(0) bound and pytest.fail exhaustion.
   _pause_release is ONLY called for an active executor blocking first/next latch. A dormant
   awaiting run rejects pause by existing RunLifecycle._stop and needs no cleanup transition.
   Remove calls after merely reaching gates, messages or dormant children. Lifespan leaves
   awaiting dormant. Keep calls for blocked capacity/approved continuation/tool cases.
4. Existing edit/post-action case: queue ONLY First; prepared decision override needs no new
   LLM. After edit200 obtain post gate via _gate, assert body_count1 and GateQuestion.phase
   after; reject edits409, then approve200, await next_entered, body_count still1, exactly one
   provider request. Pause/release next latch. Missing reason422 is checked on first pre-gate
   in input test, not on an already applied gate. Existing instruct input case: after instruct
   question is pending revision1/history1/proposal Revised, not answered. Stale revision0 ->409;
   approve revision1 ->200; await next latch/body_count1, then pause/release. Two provider calls.
   Continuation-capacity accepted gate is prepared, not answered; SQL is pending/running and
   409 detail.run_id equals first ID. Preserve actual resume/body_count/dedup assertions.
5. Memory case queue First, InterruptClass(category='qa'), QaAnswer(text='Evidence unavailable.',
   references=()), then Second decision in this exact order. Use old fact value
   'captured-old-fact', new value 'uncaptured-new-fact'; inspect recorded QA messages only,
   old present/new absent. New run reaches its genuine gate and captures both facts. Leave
   both awaiting at first lifespan exit. Fresh setupFalse existing first run: both bindings
   dormant, no executor/task, current awaiting state and original captured tuple retained.
6. Add _mcp_session(api:RunAPI)->object|None returning resources.mcp._session. All before/after
   shutdown session assertions call this helper, so fresh reads are not narrowed across await.
   Tool owner case preserves pause/drain/live conn and afterclose checks, non-None reopened row.
   Setup owner case after owner finishes also asserts session None and conn execute raises
   sqlite3.ProgrammingError. Failed-start case save original RunLifecycle.start before patch,
   assert failed_tasks observed, row pending/running and failed snapshot, then after lifespan
   assert closed session/conn, restore exact original method via monkeypatch.setattr. Reopen
   same root setupFalse/existingrun using canned First; actual pending start reaches real gate,
   execution_failed false, leave dormant. No false failed SQL transition.

Existing creation/capacity/directfork/message tests otherwise preserved. Remaining absent
group3–7 cases are pending, not waived; main assigns them after this terminal review. Ruff check,
Ruff format --check, mypy file-only; logs /private/tmp/b13-chunk34-c2-{ruff-check,ruff-format,mypy}.log.
Mechanical formatting permitted. No runtime checks. Unexpected diagnostics stop before workaround.
Terminal receipt lists changed cases, genuine commands/exits/full summaries, file SHA and gaps.

## Resolved correction C2a — decode the existing typed gate model

Only tests/test_api_runs.py. Import existing graph.gates.GateQuestion. In instruct revision
test replace the local pending.question() dictionary with GateQuestion.model_validate of
that dictionary; use its revision, history and proposal attributes for the same assertions.
The proposal remains dict[str,JsonValue]; equality of proposal['rationale'] and 'Revised' is
typed and sufficient. No nested-union indexing, cast, new helper, ignore or weakened assertion.
Everything else remains C2. File-only Ruff check/format-check/mypy, unique c2a-prefixed logs;
mechanical format permitted, runtime stopped. Stop unexpected diagnostics; terminal actual
commands/exits/summaries/SHA. Main reviews before remaining case assignment.

## Resolved continuation C3 — remaining gate/control/fork API cases

C2a terminal reviewed: all file statics0; typed existing assertions retained. Own ONLY
tests/test_api_runs.py. Preserve existing cases and fixture; add/extend exactly below.

1. test_gate_action_outcomes parameter action:GateAction values approve,
   approve_all_remaining, abort, rewind; fresh tmp_path/action app, canned First only.
   Create guided and _gate, record parent binding/config. For rewind append Child before answer
   and body target declared first node. For others body only action. HTTP answer200/category
   decision. Approve: next_entered, body_count1, pause/release. Approve-all: same plus SQLmode,
   lifecycle.initial.mode and actual checkpoint/state.mode all autonomous, one provider call;
   pause/release. Abort: actual row and lifecycle state cancelled, no unfinished task/body0,
   gate applied. Rewind:200/new_run_id, _gate child; parent superseded, child config.fork
   request_id equals gate.id and published True; inherited session/dataset/hash/memory/contract,
   separate control/events, leave child awaiting. No other LLM for approve/abort.
2. test_gate_ownership_and_nonskippable creates two genuine guided runs/canned First, Second;
   wait each gate. First endpoint using second gate404; nonexistent gate404; first skip_node
   reason 'fixture reason'409 (first declared node is nonskippable). Compare both gates still
   pending and body0; do not fabricate a skippable frontier. Negative reason omission already422.
3. Extend direct rewind portion of existing test_gate_actions_and_direct_rewind. Before fork
   submit invalid controls: rewind missing request_id, missing target, request_id blank, target
   blank; pause with unrelated target/request_id; each422 fixed invalid request, run count
   unchanged. Unknown target 'unknown-node' with FRESH request 'unknown-request'409, unchanged.
   Existing fork/retry/mismatch preserved. After child gate: capture child lifecycle state,
   event tuple and real execution rows; successful retry leaves all unchanged, no unfinished
   child task, exactly one child row forked_from parent. Compare parent/child three immutable
   snapshot byte sets. Configs share dataset/hash/session/memory/contract while fork metadata
   differs; controls and EventBus distinct. Append InterruptClass status, send child message
   'status'200. Join llm_calls to node_executions for child: every execution run_id child,
   no parent anchor. This validates incoming same-run anchors via real services.
4. test_capacity_denied_direct_fork_is_durable: fresh max1 app, canned First only. A reaches
   gate; B before_block occupies slot (no B reply needed; pause prevents decide). Rewind A
   first node request 'capacity-fork'409 detail.run_id nonempty child ID; child row pending,
   config.fork published True/request_id correct, parent superseded, exactly one child and
   child not admitted to active task. Pause/release B first latch. Append Child, retry same
   parent request201/same child. Wait child gate, exactly one child, body0; leave dormant.

Import/reuse GateAction, InterruptClass, list_node_executions and existing typed RunConfig /
GateQuestion / repository functions as named. Every optional row gets local non-None assertion.
Event tuples come from awaited binding.loop.events.replay; no new disk parser or test substitute.
Use first node name from TABULAR_DOMAIN; no node literals outside tests. No production edits,
runtime/collection/probes, agents/commits. Three file-scoped static prefixes, c3-prefixed unique
logs; mechanical format only. Stop unexpected diagnostics/missing decisions before workaround.
Terminal inventory each four groups, actual exits/summaries/SHA, remaining C4 message/restart
gaps. Main reviews C3 before C4. Original eight-group inventory remains required.

## Resolved continuation C4 — messages and historical restart

C3 terminal reviewed: three file statics exit0; main read all new cases. Two added bootstrap
status assertions are incorrect: graph/rewind.py project_frontier explicitly returns pending;
lifecycle.initial is bootstrap state, while lifecycle.state reads the durable checkpoint.
Correct these assertions below. No production defect or production edit is required.

Own ONLY tests/test_api_runs.py. Preserve all existing tests and helpers. Reuse actual
ControlRouter, GateService, ForkService, RunLifecycle, EventBus.replay, repository message
functions and typed control/contracts models. No fake dispatch, state overrides, invented
audit fields, source mutations, runtime execution, probes, agents or commits. Imports required:
collections.abc Mapping/Sequence, pydantic JsonValue/TypeAdapter, errors ConfigError,
graph.control ConversationTurn/QaAnswer, repository create_message/list_messages,
contracts.gates GateAnswer/RewindIntent. Existing imports remain if used.

### Ordered file recipe and exact interfaces

1. In test_gate_action_outcomes assert answered JSON category is decision. Replace rewind
   branch lifecycle.initial.status assertion with awaited lifecycle.state().status awaiting_user.
   In test_gate_actions_and_direct_rewind use already captured child_state.status awaiting_user
   instead of initial.status; after retry also assert execution_tasks.get(child) is None or done.
   Preserve existing state/events/execution equality checks. No bootstrap-state mutation.
2. Extend FixtureRecords with provider_checks: list[tuple[tuple[str,...],dict[str,str]]], empty
   default_factory list. Add _open_run_api keyword reject_old_model:bool=False. Its local
   check_bound_models signature is (models:Sequence[str], *, environment:Mapping[str,str]|None
   =None, env_file:Path=Path('.env'))->None. When reject_old_model False return without work;
   otherwise append model tuple and a copy of environment (empty mapping if None), then raise
   ConfigError('fixture old model unavailable') iff 'openai/old-test' is among models.
   Replace ONLY runs_module checker monkeypatch with this function. Main checker remains no-op.
   env_file is signature compatibility only; no filesystem/provider/network operation.
3. Add _prompt_user(request:dict[str,object])->str: get messages, assert list and nonempty,
   final item dict, role user, content str, return content. Ordinary isinstance narrowing;
   no casts, ignores or generic untyped helpers. This is a recorded real provider-request reader.
4. Extend test_messages_status_noise_qa_and_provider_failure. Success branch: resolve config
   session_id from non-None real RunRow; list_messages gives six chronological rows with roles
   user,assistant repeated, user contents matching submitted texts; assistant contents matching
   responses, metadata categories status/noise/qa. Replay actual events, filter message types;
   exactly three message.user and three message.assistant.complete, matching corresponding SQL
   row IDs and session ID, every event.run_id same run. SQL join llm_calls to node_executions
   scoped to run gives five rows (initial decision + status/noise/qa classifiers + QaAnswer),
   every execution owner same run. Preserve request-count5.
   Each existing failure branch: user 'question' retained as sole SQL message, no assistant;
   replay one user and zero assistant-complete events. Three provider requests, no implicit retry.
   Joined usage rows have same-run owners; exactly one signature QaAnswer row, prompt_tokens0,
   completion_tokens0,cost_usd0. Do not assert nonexistent failed flag/status. Preserve fixed
   503 transient/APIConnectionError and 502 ValueError response/detail. Duration need not be0.
5. Add test_message_decision_and_change, parametrized category Literal['decision','change'].
   Fresh root/category, canned First, guided create and real gate. For decision append classifier
   decision and GateAnswer(gate_id=actual.id,action='approve').model_dump_json. Message 'approve'
   returns200/category decision; real executor.next_entered, body_count1, actual gate applied.
   SQL pair user/assistant and metadata category decision; joined audits own run, signatures
   IntakeDecision/InterruptClass/GateAnswer in request order (three provider requests). Pause and
   release next latch via existing helper.
   For change append classifier change, RewindIntent(target=TABULAR_DOMAIN.nodes[0].name), Child
   decision in that order. Message 'rewind to intake'200/category change/new_run_id; real child
   gate, parent superseded, exactly one child. Parent config and child config inherit session,
   dataset/hash,memory; child contract_id same parent. Child fork published True, request_id
   equals actual persisted USER message ID. Parent assistant metadata category change/new_run_id
   child. Parent joined audits signatures IntakeDecision/InterruptClass/RewindIntent, child
   IntakeDecision; all execution owners equal their respective run IDs. Leave child dormant.
6. Add test_message_lifecycle_actual_pause_resume. This supersedes original C's impossible
   pause of awaiting gate: RunLifecycle._stop rejects awaiting_user pause. Canned First followed
   by classifier lifecycle/pause. Create guided, wait gate, message 'pause'409 fixed run transition
   rejected, SQL/checkpoint still awaiting_user, one user/no assistant. Then HTTP approve real
   gate, wait next_entered/body1. Append lifecycle/pause classifier; create HTTP message task,
   wait up to100 sleep(0) iterations for actual control.status paused, assert it, set executor
   next_release, await HTTP task200/category lifecycle. SQL and lifecycle.state paused, first
   body1. Clear executor.next_entered and next_release before resume. Append lifecycle/resume
   classifier; HTTP message 'resume'200/category lifecycle, wait fresh next_entered, actual state
   running, body_count still1. Pause/release next latch through direct helper to clean up.
   Three user rows, two assistant rows; successful metadata lifecycle and actual response text;
   failed first pause has no assistant event. Four provider requests total including First;
   every classifier audit same run. No Task.cancel or fabricated paused awaiting state.
7. Add test_message_bounded_history. Canned First, qa classifier, QaAnswer(text='Evidence
   unavailable.',references=()). Create guided and gate; real config/session. Persist23 actual
   messages using create_message with that session/run, role user for even index/assistant odd,
   content 'turn-XX-' plus exactly1000 x characters (XX two-digit index00..22). Submit incoming
   'bounded question' HTTP200. Classifier user prompt exactly incoming, no history or memory.
   Parse final QA provider _prompt_user prefix before first double newline with existing
   TypeAdapter(dict[str,JsonValue]).validate_json; conversation value list, parse each item with
   ConversationTurn.model_validate, typed tuple. Expect exactly16 turns: old indices08..22 plus
   incoming; first turn08 exact content, last incoming/user, old turn22 retained, roles alternating
   according to original index. Sum text lengths <=16000, len<=20. Assert retained old content
   exactly equals corresponding original strings; turn00..07 absent. Initial latest20 contains
   old04..22+incoming, then four oldest are trimmed by the character limit. No classifier history.
   Existing captured-memory test remains unchanged and proves old fact/new fact boundaries.
8. Add test_autonomous_paused_restart. Persist old captured fact before guided First creation;
   await real gate, approve_all_remaining HTTP200, next_entered/body1, SQL and checkpoint mode
   autonomous. Pause/release next, capture non-None RunRow config.memory, exit lifespan. Reopen
   same root/setupFalse/existing_run_id with empty fresh FixtureRecords; SQL/current checkpoint
   paused and mode autonomous, bootstrap mode autonomous, captured runtime.memory equals saved
   tuple, no execution task or executor. Keep genuine bound services; no scheduling or response.
9. Add test_historical_restart_rejects_invalid_snapshot, parametrized failure Literal['old_model',
   'missing_skill','changed_contract']; fresh root/failure. Install typed local fixture_environment
   signature (env_file:Path=Path('.env'), *,environment:Mapping[str,str]|None=None)->dict[str,str],
   always returning {'B13_CONFIG_MARKER':'captured-env'}. Patch main.load_routing_environment;
   register monkeypatch.setenv of that marker before lifespan to restore its process mutation.
   No real credentials in fixture assertions or receipts. First guided run/real gate and exit.
   Its test-owned RunPaths only: old_model assert bytes heading '## problem_definition' occurs
   once, insert '### Model\n\nopenai/old-test\n\n' immediately before it; preserve all other bytes.
   missing_skill unlink its skill snapshot; changed_contract append one ASCII space to existing
   canonical contract snapshot bytes (valid JSON but different immutable bytes).
   New records empty, reopen setupFalse/existing id with reject_old_model true only old case,
   inside pytest.raises ConfigError match fixture old model unavailable, or PersistenceError
   match run immutable snapshot is missing / run contract snapshot differs from SQL respectively.
   Body must not yield (pytest.fail if reached). All cases records.executors empty and requests
   empty; old case provider_checks includes old literal and exact fixture environment, modified
   skill bytes unchanged; missing snapshot remains absent, changed bytes remain modified. No
   repository fallback, snapshot rewrite or background scheduling before validation. MCP still
   closes through real failed-lifespan cleanup; no assertion on unavailable yielded API object.

### Acceptance and checks

This finishes original message/restart groups6–7; prior real shutdown/tool/setup/failure tests
finish group8. Cases are implementation only while runtime tests are stopped; no claim that
these assertions passed behaviorally. Run ONLY file-scoped Ruff check, Ruff format --check and
mypy tests/test_api_runs.py, unique /private/tmp/b13-chunk34-c4-{ruff-check,ruff-format,mypy}.log.
Mechanical formatting/import sorting of this owned file allowed. Unexpected diagnostics, missing
helpers or decisions stop before workaround and return exact command/exit/partial diff to main.
Terminal receipt: entire eight-group inventory, changed file/SHA/base HEAD, actual commands/exits
and full summaries, log paths, deviations, remaining hazards; no worker plan/status edit or commit.

## Resolved correction C4a — reviewed fixture sequencing and row assertions

C4 terminal stopped at mypy1: decision/change local deque inferred str-only, incompatible
with invariant deque[str|BaseException]. Ruff/format final0. Main read every new case/helper;
production remains unchanged. Own ONLY tests/test_api_runs.py, preserve all other content.

1. In test_message_decision_and_change eliminate the prequeued placeholder gate response and
   positional replies[2] replacement: initial decision is consumed before the actual gate, so
   that index is invalid. At function start declare replies:deque[str|BaseException], initialize
   with ONLY _decision(), pass to FixtureRecords as before. Message text is approve for decision
   and rewind to intake otherwise. AFTER _gate returns, extend this same deque with actual
   classifier decision followed by GateAnswer(gate_id=gate.id,action=approve) JSON; change branch
   extends with classifier change, typed RewindIntent first-node target, then Child decision JSON.
   No placeholder or index assignment. Remaining assertions/provider counts unchanged.
2. Lifecycle message test selects THREE SQL fields but unpacks TWO in the ownership assertion.
   Use row[0]/row[1] per existing neighboring audit assertions, preserving both same-run checks.
   Add ORDER BY llm_calls.rowid to its audit query because the following signature tuple asserts
   insertion order. Immediately after paused HTTP result and lifecycle.state paused, read a
   fresh RunRow and assert non-None/status paused, before clearing latches/resuming. No other
   lifecycle behavior changes.
3. Historical restart test currently reopens INSIDE its first live lifespan. Keep first create,
   gate, non-None row and local paths/skill_path setup inside the first context, then exit it.
   Move the entire existing mutation/error-selection/restart/assertion block (starting failure
   old_model branch through final bytes assertion) AFTER first context at function indentation.
   This includes snapshot writes/unlink, restart_records and pytest.raises. No new helper or
   mutation logic; original byte guards and no-fallback assertions remain. It must perform a
   real closed-first-app/fresh-second-app restart, never nested live lifespans.

These are transcription corrections, not new design. Existing relocated 502 failure branch
belongs to the prescribed provider failure test and is retained. Only exact file-scoped three
static commands, c4a-prefixed logs; mechanical format/import sorting permitted. Runtime stopped.
Stop unexpected diagnostics before workaround. Terminal commands/exits/full summaries/SHA and
eight-group inventory required; main re-reviews changed behavior before final global statics.
