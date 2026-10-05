---
status: implemented
dependencies: [03, 05, 06]
completion_date: 2026-10-05
implementation_notes: "B13 implemented/reviewed; final global static acceptance exit0. Runtime/coverage outstanding under user test stop; receipts/B13.md."
---

# Chunk33 — projects and pre-run setup

## Goal, scope and evidence

Implement PLAN §11's project create/list/detail, one bounded CSV upload, contract upload,
dataset validation and contract proposal. D4/D5/D8/D12–D18/D27/D32/D36 apply. Setup must not
create a run, node execution, artifact or fictitious audit anchor. Backend imports no compute.
CSV parsing and validation run only through MCP. No schema/dependency change, SSE, frontend,
run API, paid calls or runtime checks. Chunk34 starts only after main reviews terminal handoff.

Reuse accepted storage/repository/B6 MCP/LLM/skills/routing seams. B8 behavioral evidence is
historical; B9–B12 are static-only. Added setup calls have no run to attach `llm_calls` or
`tool_calls` to: do not fabricate one. Run accounting remains unchanged. Setup usage is not
included in run totals; this explicit pre-run accounting limit must appear in the receipt.

## Ownership and integration seams

Only these product files are owned:

- New `backend/statlab/api/__init__.py` (module docstring), `backend/statlab/api/projects.py`
  (typed request/response models, APIResources, ProjectsService, router and bounded readers).
- New `backend/statlab/main.py` (create_app and lazy default app).
- `backend/statlab/mcp_client.py`: imports and new fixed `McpClient.setup_dataset` only.
  Ordinary call/dispatch/lineage/recording algorithms must remain untouched.
- `backend/statlab/llm/client.py`: shared typed completion extraction and optional audit sink.
- `mcp_server/statlab_tools/tools/intake.py`: CSV reader extraction and fixed setup tool.
- New `tests/test_api_projects.py`, `tests/test_tools_setup.py`; additions to
  `tests/test_mcp_client.py` and `tests/test_llm_client.py` only.

No other source/config changes. Main owns this spec and status. No nested agents or commits.
Worker is not alone: preserve others' work. An unavailable helper, missing decision or failure
stops affected work and returns exact gap, command exit and partial diff to main.

Existing helpers/callers read by main:
`persistence.repository` create/get/list_project, create/get/find_dataset_by_hash,
create/get/latest_contract, list_runs; `memory.read_memory`; `persistence.db.connect`,
`persistence.db.migrate`; `persistence.ids.new_id`, `ProjectPaths`/`RunPaths` in contracts layout;
`config.load_settings`; MCP `_schema_errors`, `TOOL_RESULT_ADAPTER`, ToolSpec;
LLM complete/_request/_record/_record_response and their only production caller
`domains.runtime.DomainRuntime.decide`; `llm.routing` load_routing_environment, ModelRouter,
check_provider_environment; `domains.routing.NODE_ROLES`; `domains.tabular.TABULAR_DOMAIN`;
`llm.skills.load_skill`. Intake load_csv is used by foundation/candidate_pipeline/design_inference
and existing load tests; its summary/artifacts/options must stay byte-for-byte compatible in shape.
Intake `_dataset_csv_path`, `_reclassify_read_error`, `_raw_header_row`, duplicate/single-column
checks, `_role_for_column`, `_check_columns_exist`, `_check_target`, `_check_roles`,
`_check_split_time_column` are the shared compute helpers. No new role/target heuristics.

## Exact interfaces and boundary rules

All new Pydantic models frozen, extra forbid; tuple collections. Production/positive tests use
typed kwargs; negative tests model_validate. No explicit dynamic top-type token, ignores or
coverage suppression. IDs returned in existing row models. Response paths/config can remain
existing row fields; no credentials in responses. Do not stringify exception causes.

`APIResources` is a slots dataclass with fields `settings: Settings`, `conn: sqlite3.Connection`,
`mcp: McpClient`, `llm: LlmClient`, `router: ModelRouter`, `policy: SkillPolicy`.
No async DB offload: connection, services, async handlers and eventual run tasks share one loop
thread. Frozen policy is startup validation evidence; per-run snapshot comes in34.

`ProjectCreate`: `slug: str` pattern `^[a-z0-9][a-z0-9_-]{0,63}$`, `name: str` length1..200;
reject whitespace-only name; preserve nonblank name text. Slug supplied explicitly, no slugification.
`ProjectDetail`: `project: ProjectRow`, `runs: tuple[RunRow,...]`, `memory: tuple[MemoryRow,...]`.
`ContractValidationRequest`: `dataset_id: str`, `contract: DataContract`.
`ContractProposalRequest`: `dataset_id: str`, `contract: DataContract`. Target column is mandatory
via DataContract, even for proposals; all other unset fields can be inferred. No target guessing.
`ContractProposal`: `contract: DataContract`, `reasoning: str` length1..10000.
`SetupValidation`: `valid: bool`, `mismatches: tuple[dict[str,JsonValue],...]`,
`warnings: tuple[str,...]`; transform the trusted tool summary with model_validate (no bypass).

`ProjectsService(resources: APIResources)` owns:
`project(project_id: str) -> ProjectRow`, `paths(project: ProjectRow) -> ProjectPaths`,
`dataset(project_id: str, dataset_id: str) -> DatasetRow`,
`persist_contract(project_id: str, contract: DataContract) -> ContractRow`,
async `validate(project_id: str, dataset_id: str, contract: DataContract) -> SetupValidation`,
async `propose(project_id: str, request: ContractProposalRequest) -> ContractProposal`.
Small private helpers below may be methods of this service with the specified arguments; no
generic repository/interface abstraction. `require_complete(contract: DataContract) -> None`
rejects missing target.type, objective.task, objective.primary_metric or split.strategy with422.
Other defaults are DataContract defaults; candidates/inference goals may stay empty until nodes.

`projects_router(resources: Callable[[], APIResources]) -> APIRouter` installs async endpoints:

| Method/path | Body/result | Status |
|---|---|---|
| POST /api/projects | ProjectCreate → ProjectRow |201|
| GET /api/projects | none → tuple[ProjectRow,...] |200|
| GET /api/projects/{project_id} | none → ProjectDetail |200|
| POST /api/projects/{project_id}/datasets | one multipart `file` → DatasetRow |201, or200 existing hash|
| POST /api/projects/{project_id}/contracts | one multipart `file` JSON → ContractRow |201|
| POST /api/projects/{project_id}/contracts/validate | ContractValidationRequest → SetupValidation |200|
| POST /api/projects/{project_id}/contracts/propose | ContractProposalRequest → ContractProposal |200|

Upload dialect is a `DatasetOptions` query model (FastAPI Query annotation); defaults/types/
constraints exactly DatasetOptions including repeated na_values. No MIME trust; CSV filename
extension must case-insensitively be .csv, JSON .json. Display basename strips either slash style;
filenames never form disk paths. Empty/malformed content422. Contract upload accepts partial valid
DataContract, including target.column; proposal never persists automatically. User confirms by
uploading accepted JSON. Validation compares data and returns findings, not422 for mismatches.

Missing project/dataset or cross-project dataset404; duplicate slug409; invalid input422;
upload limit413; tool_param/data failures422, transient503, bug500. LLM output failure502,
provider transient503/bug502. Other StatlabError500 and OSError/sqlite3.Error500 fixed generic
detail. FastAPI request validation remains422 with sanitized errors (remove input/ctx from output
so an uploaded secret is never reflected by validation). Catch Exception only at foreign
boundaries, never BaseException; cancellation propagates. Domain errors stay domain errors
inside shared clients; HTTPException is only API presentation. Error handlers expose fixed
labels, never raw tool/provider exception messages or causes. Missing startup resources503.

## Ordered per-file recipe

### 1. Intake: reuse CSV checks, add setup tool

Broaden `_dataset_csv_path(paths: RunPaths | ProjectPaths, dataset: str) -> Path`; retain existing
containment guard and missing-file behavior. Add `_read_csv(path: Path, options: DatasetOptions)
-> pd.DataFrame`: move load_csv's missing-file/read/reclassification/raw-header/duplicate/single-
column/zero-row sequence verbatim, using fields from options. load_csv constructs typed
DatasetOptions from its existing kwargs and calls this reader; saving raw artifact, summary,
warning and envelope remain unchanged. Every current load_csv caller keeps same contract.

Add `setup_dataset` decorated phase=`setup`, allow_in=[`setup`], needs_run=False, heavy=False.
Signature: `project: Annotated[str, Field(pattern=the API slug pattern, description=...) ]`,
`dataset: Annotated[str, Field(pattern=SHA256_PATTERN, description=...) ]`,
`options: Annotated[DatasetOptions, Field(description=...)] = _OPTS`,
`contract: Annotated[DataContract | None, Field(description=...)] = None` → ToolSuccess.
Use `get_tool_settings().data_dir` and ProjectPaths. Reject project root resolving outside
data_dir and datasets directory resolving outside project root (ToolParamError) before existing
dataset resolver. No SQL/backend import, RunPaths, file write or artifacts.

Read entire CSV once via reader. Summary: `rows:int`, `cols:int`, `columns:list[str]`,
`dtypes:dict[str,str]`, `null_counts:dict[str,int]`, `n_unique:dict[str,int]`,
`roles:dict[str,str]`, `role_evidence:dict[str,str]`, `all_null:list[str]`.
Role/evidence use `_role_for_column` for every column; all-null omitted from roles.
Unique counts use dropna().nunique(), converted int. No rows or example values returned.
If contract supplied append `valid`, `mismatches`, `checked` using exactly the four existing
validation helpers and their skip-missing-column semantics; `checked` retains existing meaning.
Warnings exactly load_csv's >200000-row warning. No sampling, fit or target default guessing.

### 2. MCP fixed pre-run dispatch

Add async `McpClient.setup_dataset(*, project: str, dataset: str, options: DatasetOptions,
contract: DataContract | None = None) -> ToolSuccess`. Import DatasetOptions.
Only fixed name setup_dataset; callers cannot select arbitrary tools/allow-list. Verify entered
session, existing SQL project by slug, discovered spec.phase==setup and allow_in==(`setup`,),
heavyFalse and transform_onlyFalse. Missing/incorrect registry is EngineError. Build JSON
arguments project/dataset/options plus contract only when non-None, using mode=json. Apply
existing `_schema_errors` and raise ToolCallError(tool_param) before dispatch on violations.
Call entered session with read_timeout_seconds900. Reuse ordinary dispatch's exception class
mapping verbatim, without SQL recording. Preserve CancelledError unaltered. Parse actual
CallToolResult structured_content['result'] with existing adapter; malformed result/envelope
ToolCallError bug; ToolFailure → ToolCallError. Reject successful nonempty artifacts as bug.
Return ToolSuccess. Do not modify ordinary call/_call_once or context allow-list/lineage behavior.

### 3. Shared LLM setup completion

Keep public `complete` signature and same-run execution verification exactly. Extract everything
after verification into private async `_complete[T: BaseModel](conn: sqlite3.Connection | None,
*, run_id:str, node_execution_id:str, model:str, messages:Sequence[dict[str,str]], output:type[T],
reasoning:bool, example:dict[str,JsonValue]|None, recovery:CallRecovery|None) -> T`.
Existing complete delegates with its verified conn/arguments. Add public async
`complete_setup[T: BaseModel](*, model:str, messages:Sequence[dict[str,str]], output:type[T],
reasoning:bool=False, example:dict[str,JsonValue]|None=None) -> T`: delegate with connNone,
empty audit IDs and recoveryNone. No fake run or call recovery scheduler in setup.
Change only `_request`, `_record`, `_record_response` conn type to optional. `_record` returns
immediately on None; `_record_response` still validates usage and calculates cost exactly,
then skips SQL write on None. Same shared semaphore, 60-second timeout, no SDK retry, one repair,
exception/cancellation behavior and run accounting. All existing callers remain unchanged.

### 4. API project service and bounded multipart

Use public Starlette MultiPartParser and FormData/UploadFile, not eager FastAPI File dependencies.
Async context-manager helper `bounded_file(request: Request, *, limit:int) -> AsyncIterator[UploadFile]`:
require multipart Content-Type; optional Content-Length must parse nonnegative and <=limit+65536,
otherwise422/413. Supply parser a request.stream wrapper counting every raw chunk; over
limit+65536 raises413 before feeding it. Parser max_files1, max_fields0. Installed Starlette
parse() closes temporary files on **all BaseException**, including stream failure/cancellation;
reuse that guarantee. After parse require exactly one form part named file which is UploadFile.
Map MultiPartException/ValueError/ClientDisconnect to fixed422. Finally await form.close for
success and later failure/cancellation. Separate file reader enforces actual payload limit;
Starlette max_part_size alone does not bound file payloads. No body accumulation for CSV.
For JSON max1MiB; read chunks64KiB, allow exactlylimit, reject next byte. JSON parse with
DataContract.model_validate_json; validation failure422 fixed message.

ProjectsService.project resolves SQL ID or404. paths uses stored safe slug, validates pattern
(legacy invalid slug500), resolves root under settings.data_dir, each used datasets/contracts
under root, rejects symlink escape500 before writes. Existing content path must resolve inside
datasets and match expected `<sha>.csv` (no caller path trusted). Ensure root/datasets/contracts/
runs directories with parents/exist_ok; no overwrite of unrelated files.

Create route: validate body; precheck duplicate slug409; prepare safe directories; reject an
existing project_json (including dangling symlink)409 before create_project SQL. Publish canonical
row JSON via temporary sibling and exclusive os.link, always unlinking the owned temporary.
FileExistsError after SQL returns409 and preserves the committed diagnostic row and existing file.
No await
between SQL and write. If filesystem fails after row commit return500, preserve row diagnostic;
do not delete previously existing root or rows. DB slug constraint is final authority.
List/detail reuse repositories/memory ordered as returned.

CSV upload: verify project first; limit=min(settings.max_upload_mb,200)*1024*1024.
Within bounded_file, validate basename/suffix, NamedTemporaryFile in datasets(delete=False),
stream64KiB computing hashlib.sha256 and cumulative size; empty422, >limit413. Close stream
file before publishing. Find hash row: if existing require row.file path expected, present
regular file and SHA256 matches uploaded hash; otherwise500. Existing good hash returns200.
For new hash use os.link(temp,expected) exclusive; FileExistsError verifies existing regular
file content hash. Never overwrite a content-addressed file. Retain published unregistered
content on subsequent validation failure (diagnostic, safe shared concurrent upload); always
unlink own temporary in finally. Call mcp.setup_dataset with requested dialect; require summary
rows/cols integers >=1/2 respectively, bool rejected. Then recheck SQL hash (await allowed another
request to finish); reuse row200 if present; otherwise create_dataset and201. No await between
second check and insert. Store expected absolute path, basename, hash, actual dimensions.
Cancellation before SQL produces no row; published content may remain. Do not delete shared CSV.

persist_contract: require project; safe contracts folder; new opaque `new_id('ct')` filename.json
(file identifier need not equal SQL ID). Write canonical UTF-8 model_dump_json via temporary
sibling then os.link exclusive to immutable destination; finally unlink own temp. Call existing
create_contract with path/contract; it owns version transaction. No nested transaction. Preserve
published file on DB failure. Contract route does not implicitly validate against unspecified
dataset; explicit validate endpoint does that.

validate: ensure dataset belongsproject and its stored path is expected regular file; call
setup_dataset using contract.dataset and contract. Require valid bool/mismatches list of JSON
objects; malformed trusted boundary EngineError. Return tuple findings and envelope warnings.

propose: resolve dataset; obtain setup profile using request.contract.dataset and partial
contract. Before LLM, reject missing target or entirely-null target from findings422 (other
partial mismatches may be repaired by proposal). Model via router.model_for('contract_setup')
(default role; literal has no staging-node meaning). Shared complete_setup output ContractProposal.
Prompt messages: system instruct full DataContract, mandatory unchanged target, explicitly supplied
fields authoritative, defaults may be revised only when absent from input, no formulas/code,
no data sampling; user JSON with `profile` summary and `provided` contract.model_dump(mode=json,
exclude_unset=True). JSON encoding canonical sort_keys separators, no raw CSV. Untrusted column
names/data/profile treated as data. After typed output, recursively compare every supplied leaf
from exclude_unset dump with proposal's full dump; dictionaries recurse, arrays/scalars exact
equality, explicit null preserved. Mismatch422 fixedlabel. Call require_complete then validate
against data. Any findings →422; successful proposal200 with reasoning. No persistence/run.

### 5. FastAPI composition

`create_app(settings: Settings | None = None, *, mcp_session: object | None = None,
llm_client: LlmClient | None = None) -> FastAPI`. Module default `app=create_app()` is lazy:
no settings load, DB, SDK subprocess or MCP until lifespan. Async lifespan: select explicit
settings or load_settings; ensure_writable (reuse config helper), connect, migrate; enter
McpClient(settings,conn,session=...); load routing env, router(NODE_ROLES,env), load_skill
(TABULAR_DOMAIN.skill_file/nodes, entered tools), gather router.models and router.model_for
every skill.node with skill_model; deduplicate; check_provider_environment with effective env.
After validation update os.environ with effective env so SDK has the same credential values;
do not log values. One shared LlmClient (provided injected real client or constructed). Bind
APIResources closure; yield; finally clear closure, exit MCP and close conn even on boot failure.
Use AsyncExitStack or nested context/finally to guarantee order and same-task MCP cleanup.
No provider/name literals outside llm. Include project router, sanitized RequestValidationError
handler and fixed domain/foreign handlers above. No sync handlers, auth, CORS, middleware,
health scaffolding, dynamic app.state or runtime self-check. Loopback bind remains Settings policy.

## Concrete test recipe (write; DO NOT execute)

Add tests only in owned test files. Async tests use pytest asyncio, httpx.AsyncClient with
ASGITransport and explicit same-task app.router.lifespan_context; no TestClient DB thread.
API fixture builds Settings with tmp data/db, SecretStr('test-token'), monkeypatch startup
credential check to capture selected models, uses fake session schema/metadata for **all skill
allow-listed tools**: its list_tools obtains real `statlab_tools.tool.server.list_tools()` as
`tests/test_skills.py:registry` does (import statlab_tools.tools registers tools). Wrap listing
in SimpleNamespace. Its call_tool returns canned SDK CallToolResult for setup only, recording
arguments; it must not dispatch real MCP/network or fabricate registry metadata.
Fake profile envelope default rows3 cols3 columns[a,b,c], null_counts0, n_unique[a:3,b:3,c:3],
roles numeric/numeric/categorical, validTrue mismatches[]; fixture can enqueue failure/profile.
Use actual SQL and files, injected real LlmClient with async canned ModelResponse.
No paid/network calls. API fixture needs no production skill patch; startup passes real policy.

`test_api_projects.py` cases:
- create201 slugdemo nameDemo, project_json row, directory layout; list/detail SQL/order/memory;
  duplicate409; missing404; slash/uppercase/blank-name rejected422 with input not echoed.
- CSV bytes `a,b,c\n1,2.5,x\n3,4.5,y\n4,6.5,z\n`:201 SHA/file exactbytes/dims/basename;
  repeat200 sameID one row; defaults forwarded exactly. Semicolon query forwarded unchanged.
- cap Settings.max_upload_mb1; bytes exactly1MiB accepted with canned profile; limit+1 →413
  no dataset row/own temp; raw multipart >limit+65536 →413; twofiles/textfield/wrongfield/empty/
  wrongsuffix malformedcontent422. Check form UploadFile closes on stream error/cancel via
  parser guarantee, use real request stream cancellation; no external resource.
- crossproject validation404; corrupt/missing existing SHA file500, neveroverwrite;
  setupfailure422/503/500 fixedtext no secret from cause; cancellation noSQLrow and tempremoved.
- JSON valid partial201 canonicalfile/version1 then2; malformed/oversized/wrongextension422/413;
  validate missing-column200 validFalse findings, correctdata200 true; no runs/node rows.
- proposal canned full binary contract targeta, taskclassification, metricroc_auc, stratified;
  unchanged explicit target and fields; output reasoning; no savedcontract/run; wrongtarget,
  changed explicit seed/null/list422; unresolved fields422; datamismatch422; allnulltarget422
  no LLM dispatch. Startup models includes role overrides and explicit skill literals, failure
  closes MCP/conn, effective env preserved; factory itself performs noIO.

`test_tools_setup.py`: call decorated setup tool with project dataset/options; real smallCSV;
assert no artifacts/runfolder, summaryroles/dims and allnull, duplicateheader/dataerrors,
unknowncodec/semicolon mismatch, supplied target validation findings, symlink escape rejected.
Reuse conftest tool settings fixture; direct envelope wrapper as existing intake tests.
Existing intake tests must remain unchanged.

`test_mcp_client.py`: new setup tests use existing FakeSession/served_tool/world; correct fixed
args and900timeout, no tool_calls/node rows, schema bad beforedispatch, missing/wrongsetupmetadata,
foreign transport classifications, malformed SDK/envelope, artifact-bearing success rejected,
ToolFailure and CancelledError preserved. Existing ordinary-call tests unchanged.
`test_llm_client.py`: setup uses existing response/fake helpers; typed output; invalid first then
valid exactly2dispatch; twiceinvalid LlmOutputError; usageinvalid LlmCallError; noSQLaudit,
shared semaphore with run and setup call (block fake provider Event, second does not enter at
max_concurrent_llm1); cancellation preserves; timeout60/num_retries0. Existing complete tests
remain unchanged. Do not bypass validators or manufacture unreachable branches.

## Permitted checks, stop and receipt

Runtime pytest/collection/probes/mutations/replays/server startup remain **stopped**. Do not run
make check. Execute only these focused statics after transcription:

1. `.venv/bin/python -m ruff check backend/statlab/api backend/statlab/main.py backend/statlab/mcp_client.py backend/statlab/llm/client.py mcp_server/statlab_tools/tools/intake.py tests/test_api_projects.py tests/test_tools_setup.py tests/test_mcp_client.py tests/test_llm_client.py`
2. Same paths with `.venv/bin/python -m ruff format --check` (format owned files mechanically if needed).
3. Same paths with `.venv/bin/python -m mypy`.

Redirect outputs to unique /private/tmp/b13-chunk33-* logs; read full exits/diagnostics. Mechanical
syntax/import/format corrections authorized; unexpected behavioral failure/design gap returns
to main. No global statics or tests by worker. Deferred runtime selectors: these four test files
plus existing tests/test_tools_intake_load.py, roles.py and validate.py. No new mutation required.

Terminal handoff: owned changed files, base commit + diff/hash identity, exact commands/exits,
static results/full logs, tests written but unexecuted, deviations, known setup-cost limit and
chunk34 hazards. Main reviews complete diff before accepting or writing34. B13's final stable
global static acceptance belongs main after34; runtime/coverage gaps must remain explicit.

## Resolved correction A — exclusive project metadata

Pre-existing project directories are reusable, but pre-existing project_json is unrelated until
SQL owns that slug. Before SQL creation, lexists (including dangling symlinks) returns409.
Replace the former os.replace instruction with exclusive os.link publication and owned-temp
cleanup. A publication race returns409 without overwriting the existing file or removing the
committed row. Add tests for pre-existing metadata (bytes unchanged, no SQL row), dangling
symlink, and publication race (existing bytes unchanged, committed row retained). These tests
are written only; rerun the same focused statics after this correction.

## Resolved correction B — complete transcription and reviewed boundaries

Terminal handoff on a375054 is static-clean but incomplete: the prescribed tests remain mostly
unwritten. Finish every concrete test group above in the four owned test files. Do not execute
them. No runtime restriction is relaxed. Existing setup fixture must explicitly import
`statlab_tools.tools`, as tests/test_skills.py does, before obtaining the real served registry;
test-order-dependent registration is not acceptable. Extend SetupSession with queued ToolSuccess,
ToolFailure or exception responses and the injected LlmClient with canned SDK responses using
the existing test_llm_client response helper pattern. Positive contracts use typed constructors
from contract.py, including TargetSpec, Objective, SplitRules and FeatureRules. Negative HTTP
input uses JSON dictionaries; no validator bypasses. Keep existing cases and helpers.

Exact production corrections, in order:

1. api/projects.py: extract existing path checks into private
   `_project_paths(settings: Settings, slug: str) -> ProjectPaths`. Validate slug, root below
   data_dir, and datasets/contracts/runs below resolved root; fixed500 on violation. Return
   paths without creating directories. ProjectsService.paths delegates to it. Create uses it
   before metadata precheck or mkdir/SQL. A datasets symlink to another project's directory
   inside data_dir must return500 without SQL row, metadata or writes to that directory.
2. upload_dataset takes `response: Response` (FastAPI Response import). Before returning either
   existing hash row, set response.status_code200. New insert retains declared201. Existing
   hash bytes/path checks and second lookup after await remain unchanged. The existing repeat
   test already requires200; add a concurrent upload using an Event barrier in setup dispatch:
   first waits, second finishes201, first resumes and returns200 with the same ID and one row.
3. `_contains_supplied` retains dictionary recursion. For other JSON leaves, compare canonical
   JSON encodings with sort_keys=True, separators=(comma,colon), allow_nan=False. This preserves
   scalar JSON types and list order/null; Python bool/int equality is insufficient. Concrete
   reachable case: supplied TargetSpec(column=a,type=binary,positive_class=True), proposed
   positive_class=1; both are valid typed contracts but proposal returns422 before its second
   MCP validation call. Supplied seed7 changing to8, explicit features.include=None changing
   to tuple(b), and explicit exclude=(b,c) changing order all return422. Successful proposal
   may infer fields absent from exclude_unset, including defaults. No new public interface.
4. intake._read_csv: use path.stem in the missing-file ToolDataError so load_csv preserves its
   exact former `dataset <sha> not found` message. Other parsing/error/summary behavior stays.
5. main RequestValidationError handler returns fixed422 detail `invalid request`; do not emit
   loc/msg/type because dictionary keys and model-validator messages may contain supplied data.
   Add HTTP negative contract with target=a, features.include=(secret,), exclude=(secret,):
   DataContract rejects the overlap, and response must contain neither secret nor input/ctx.

Complete tests using the original recipe's concrete data and expected outcomes. Additional
fixture decisions: response queues default to the existing three-column profile plus validTrue,
mismatches=[] when a contract is supplied; exceptions propagate from fake call_tool. Canned
proposal is a fully typed binary contract target=a, classification/roc_auc, stratified; profile
is canned and no compute is dispatched. For proposal mismatch assertions, enqueue an invalid
validation summary only on the second call. For target-null/missing enqueue mismatches with
field target.column so no LLM call occurs. For MCP malformed result/artifact tests use actual
CallToolResult and existing ArtifactMeta/ArtifactRef fixtures, never weaken envelope validators.
Shared semaphore test uses existing real run execution from llm_context, max_concurrent_llm1,
provider entered/release Events, and setup as the second call; assert second provider entry
occurs only after releasing first. Cancellation uses a blocked fake dispatch/provider, cancel
only the test-created task, assert CancelledError and unchanged audit rows. Multipart stream
error/cancellation test uses Starlette Request with a receive callable yielding a valid opening
file part then raising ClientDisconnect/CancelledError; record created parser spool files via
monkeypatch of formparsers.SpooledTemporaryFile and assert closed. This exercises the installed
parser's existing BaseException cleanup, not a manufactured production path. Raw over-limit
stream has no Content-Length so streamed cap is exercised independently of header rejection.

Startup test monkeypatches main.load_routing_environment to an explicit environment mapping;
capture check_provider_environment models and environment without logging credentials. Use
existing role env keys from llm/routing.py, current skill literals, and assert selected models
include both. Factory lazy test monkeypatches main.load_settings/connect to raise if called;
create_app succeeds without lifespan. Boot-failure test credential check raises ConfigError;
record main.connect's returned real conn and client.__aexit__ (existing McpClient cleanup method)
to assert conn closed and MCP cleanup invoked in same lifespan task. No new SDK assumptions.

Run the same three focused static commands and report complete written case inventory, genuine
exits and new logs. Stop on unavailable fixtures/signatures or unexpected failures. No other
source ownership, probes, runtime checks, global statics, commits or plan edits are authorized.

## Resolved correction C — typed fixtures and remaining cases

Correction B stopped on scoped mypy exit1, not a product/runtime failure. Preserve its edits.
Repair the two owned test files first:

- test_mcp_client metadata parametrization uses separate typed arguments: allow_in list[str]
  and phase str. Two pairs are ([setup], intake) and ([intake], setup). Call served_tool with
  the named allow_in and phase arguments; remove object-dict expansion.
- test_api_projects parser spool wrapper has exact keyword signature max_size:int=0,
  mode:str="w+b" and returns tempfile.SpooledTemporaryFile[bytes]. The installed parser passes
  only max_size (verified formparsers.py). Construct the stdlib spool with max_size and fixed
  binary w+b mode; assert the incoming mode is w+b. Do not access the non-exported module
  attribute to obtain the original constructor. Monkeypatch its attribute by string only.
  Receive events are a list[starlette.types.Message], then an iterator over that list;
  receive returns Message. Do not use tuple/object args or type ignores.
- For the options assertions, extract the options leaf from the JsonValue argument dictionary,
  assert isinstance(options,dict), then index delimiter/na_values. This is real schema narrowing.

Finish the remaining original cases with these bounded recipes; preserve existing cases:

1. API concurrent dedup: create demo and upload using SetupSession.block_next=True in a task;
   await entered, send identical second upload (201), release, await first (200); assert same
   ID and exactly one datasets row. SQL inspection may use a second read-only sqlite connection
   to settings.db_path while lifespan is active. No inspection connection writes.
2. API upload bounds: settings max_upload_mb=1, canned profile, payload exactly1MiB accepted;
   limit+1 rejected413 and dataset row count unchanged. Compare datasets directory names before
   and after rejection (no leaked temporary). Empty file, .txt, wrong part name, two file parts,
   and a text field each422. Direct bounded_file Request with no Content-Length, one http.request
   body of 1024+65537 bytes then final empty body exercises streamed413 at limit1024. Parser may
   reject arbitrary body first; use a valid opening multipart file header and padding to exceed
   cap in that same chunk. Existing cleanup test covers disconnect/cancel.
3. API ownership/storage/failures: second project cannot validate demo's dataset (404). Mutate
   uploaded CSV bytes then repeat identical upload (500, mutated bytes retained); unlink CSV
   then repeat (500). Separate uploads with unique bytes and queued ToolFailure data/transient/
   bug return422/503/500; response contains no supplied secret and row count unchanged. Block
   fresh upload at setup dispatch, cancel that test request task, assert CancelledError and no
   row; own temporary removed, already published content-addressed orphan may remain by design.
   Pre-create datasets symlink from a new project's root to demo.datasets; create returns500,
   no new SQL project or metadata, demo files unchanged.
4. API contracts: upload typed partial DataContract(target=TargetSpec(column=a)) twice, versions
   increment and first file bytes remain unchanged. Empty/bad JSON/.txt each422; 1MiB+1 JSON
   payload413. Queue validFalse with one target.column mismatch for validate ->200, findings
   retained. Assert runs/node_executions remain empty across setup requests.
5. API proposals: independent lifespan/canned answer per parametrized case; supplied complete
   contract with split seed7 -> proposed8; features includeNone -> proposed tuple(b); exclude
   tuple(b,c) -> proposed tuple(c,b); supplied targeta -> proposedb. Typed positive constructors
   form both contracts. Each422 after one profile call and before second validate call. For
   unresolved output use DataContract(target=TargetSpec(column=a)), default objective/split;
   422 before second MCP call. For dataset mismatch queue default profile then validFalse with
   finding objective/task ->422 after two MCP calls. For missing/all-null target queue a profile
   with target.column mismatch ->422, no canned LLM answer consumed. Successful partial supplied
   targeta may infer complete fields absent from exclude_unset;200, no saved contract/run.
6. Startup: lazy create_app with load_settings/connect monkeypatched to raise succeeds without
   lifespan. Explicit environment has distinct role overrides and a marker credential; capture
   startup models/environment, assert three overrides plus explicit policy literals selected,
   marker reaches provider check and os.environ (monkeypatch env restores after case). Do not
   print credentials. Credential check raises ConfigError; record returned real conn and
   McpClient.__aexit__, assert context fails, exit called, subsequent conn.execute raises
   sqlite3.ProgrammingError. SetupSession owns no separate context manager.
7. MCP boundaries: FakeSession wrong result object triggers ToolCallError bug; malformed envelope
   dictionary triggers bug; success with ArtifactMeta using the existing valid artifact fixture
   is rejected. Parametrize existing foreign exceptions used in ordinary-call tests (timeout,
   connection, ValueError), assert corresponding transient/transient/bug category and no audit
   rows. Missing setup tool rejects EngineError before call. Keep wrong-metadata cases.
8. LLM invalid usage: cached3 with prompt2 from _response ->LlmCallError and unchanged _rows.
   Shared semaphore: client limit1; start real complete using llm_context execution; provider
   first entry blocks on release. Start complete_setup on same client. Give scheduler one turn
   using asyncio.sleep(0), assert only first entry; release, gather, both typed outputs ok,
   two provider entries and exactly one run audit row. Each call timeout60/num_retries0.

Tool setup cases already transcribed satisfy their named recipe; no expansion there. These are
written, unexecuted tests. Run only the same focused Ruff/format/mypy commands, with correction-C
log names. On mypy-only failures stop and report exact diagnostics; never drop cases to pass.
Terminal receipt must inventory every group above as written or identify a precise missing
decision. No generic incomplete handoff while actionable prescribed edits remain.

## Resolved correction D — review of written checks

Main reviewed correction C and its genuine scoped static exits. Two test-only corrections:

1. In test_upload_limits_and_multipart_shapes, replace the final HTTP upload of the roughly
   66KiB body: the real endpoint limit is 1MiB, so that input cannot return413. Use a direct
   starlette Request with POST path /, multipart boundary x, no Content-Length, typed receive
   Message returning that existing body then an empty final http.request. Enter bounded_file
   with limit1024 under pytest.raises(HTTPException); assert caught status_code413. The body
   already exceeds1024+65536 and contains the valid opening file header. No endpoint setting
   change and no production change.
2. In test_startup_selects_role_models_and_preserves_effective_environment, before lifespan
   calls os.environ.update, register every environment key with monkeypatch.setenv using its
   existing value when present and the selected value otherwise. This records restoration of
   existing/absent keys at teardown. Keep the active-lifespan environment assertions intact.

Ownership: tests/test_api_projects.py only. Run focused Ruff check, format --check and mypy for
that file only; log /private/tmp/b13-chunk33-d-{ruff-check,ruff-format,mypy}.log. Mechanical
formatting permitted only on the owned file. No runtime execution or broader checks. Return
terminal genuine exits, changed-file hash, complete/no-deviation receipt. Existing production
review and other scoped statics remain reusable.

## Reviewed terminal handoff — prerequisite for34

Main reviewed the complete stage diff and corrections A–D against this spec. Correction C's
ten-file Ruff check, format check and mypy each exited0; logs are
`/private/tmp/b13-chunk33-c-{ruff-check,ruff-format,mypy}.log`. Correction D changed only
tests/test_api_projects.py, hash `0dd1f2174f9de2ceaa4caafcfddcc553bf1dc7ee10d30972dd904480e091b6a6`;
its three file-scoped statics exited0 in the equivalent d-prefixed logs. Main inspected the
changed streamed-limit and environment-restoration recipes plus full static summaries.
Candidate remains a dirty descendant of `a375054394fafcbab5b1865e11251a9742fa897b`.
No runtime/coverage/probe/replay evidence exists for33. Setup usage intentionally has no
run-linked accounting. This handoff permits34 planning/delegation; final B13 acceptance/status
will follow stable batch global statics and main review, not this prerequisite handoff.
