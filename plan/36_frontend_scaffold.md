---
status: implemented
dependencies: [35]
completion_date: 2026-10-05
implementation_notes: "B14 reviewed/static-only accepted; scoped/global Python statics, TS typecheck/build and Make dryrun exit0. Dependency directive warnings retained; runtime/coverage stopped; receipt plan/receipts/B14.md."
---

# Chunk36 — browser scaffold and local process ownership

## Goal, non-goals and prerequisites

Deliver the D9/D14/D20/D21 browser foundation: strict TypeScript, React, Vite, typed GET
client, native reconnecting EventSource with total sequence checks, small English shell,
and working Makefile api/ui/dev commands. Chunk35 terminal C3/C4 handoff reviewed by main:
eight-file Ruff check/format/scoped mypy all exit0. Reuse [B14 receipt](./receipts/B14.md),
B13 registry/bindings and the supported backend early-signal launcher. PLAN D16/D18/D27/D32/
D36 and §§11.1,11.2,12 remain binding. No setup wizard, upload/mapping, chat, gate controls,
lifecycle controls, graph, artifacts, comparison, charts, report, routing library, auth,
new backend routes, SQLite changes, SDK/dependency changes to Python, Docker, or B15 work.
All runtime tests/collection/mutations/probes/live services/browser remain stopped. Node tests
are written, never executed. Compilation/build is permitted; only writer generates artifacts.

## Ownership and inspected seams

Writer owns ONLY these NEW files:
- frontend/package.json, frontend/package-lock.json, frontend/tsconfig.json,
  frontend/vite.config.ts, frontend/index.html, frontend/README.md;
- frontend/src/main.tsx, frontend/src/App.tsx, frontend/src/style.css;
- frontend/src/api/types.ts, frontend/src/api/client.ts, frontend/src/api/events.ts,
  frontend/src/api/useRunEvents.ts;
- frontend/tests/client.test.ts, frontend/tests/events.test.ts;
- scripts/dev.py, tests/test_dev.py.
And existing Makefile api/ui/dev/check-env recipes/help text only. Preserve install, mcp,
test/test-fast/check/typecheck/lint/format/db-reset execution semantics. No other ownership.
Do not edit chunk35 source. No worker commits/status/plan edits, nested agents or design choices.
Worker is not alone; preserve all others' edits. Missing decisions or failures return to main.

Main read Makefile (all three launch targets are placeholders), backend Settings/load_settings/
ConfigError, MCP ToolSettings/load_tool_settings/ToolSettingsError, routing.check_startup_environment,
server /health, main._RunServer, API projects list and runs snapshot, persistence ProjectRow/
RunRow/GateRow, Event/EventBus, root pytest isolation, pyproject static configuration, relevant
index rows and PLAN decisions. No frontend/scripts exist. Reuse loaders; never reimplement
provider credentials/model selection, gate/restart/rewind state, HTTP request or execution ownership.
Launcher may import both process settings; MCP package itself still imports no backend.

Main verified version/engine metadata and official APIs: Vite8.3.2 (Node ^20.19 or >=22.12),
React/react-dom19.3.0, TanStack Query5.104.1, TypeScript5.9.3, @types/react and @types/react-dom19.3.0,
@types/node24.19.1; environment Node24.13.1. References: https://vite.dev/guide/,
https://vite.dev/guide/features, https://tanstack.com/query/latest/docs/framework/react/quick-start,
https://html.spec.whatwg.org/multipage/server-sent-events.html.
Vite supports TSX automatically; omit React plugin, router, charts, ESLint/Prettier and codegen.
Native EventSource retains Last-Event-ID on reconnect; chunk35 takes max(query,header).

## Exact interfaces and data

types.ts exports recursive JsonValue = null|boolean|number|string|JsonValue[]|JsonObject,
JsonObject string keys to JsonValue; ProjectSummary {id,slug,name,created_at:string};
RunMode 'guided'|'autonomous'; RunStatus 'pending'|'running'|'awaiting_user'|'paused'|'failed'|
'cancelled'|'completed'|'superseded'. RunSummary fields id,project_id,started_at:string;
mode:RunMode; status:RunStatus; current_node,forked_from_run_id,ended_at:string|null.
GateSummary {id,run_id,node_execution_id,status:string}. RunSnapshot {run:RunSummary,
state:JsonObject,gates:GateSummary[],execution_failed:boolean}. RunEvent {seq:number,
run_id,ts,type:string,payload:JsonObject}. These are consumed subsets of real responses;
additional backend properties are allowed/ignored by summary parsers, never fabricated.

client.ts exports ApiError extends Error with readonly status:number|null; constructor
(message:string,status:number|null=null), name='ApiError'; isJsonValue(value:unknown):value is
JsonValue; isJsonObject(value:unknown):value is JsonObject; parseProjects(value:unknown):ProjectSummary[];
parseRunSnapshot(value:unknown,runId:string):RunSnapshot; listProjects(signal?:AbortSignal):Promise<ProjectSummary[]>;
getRun(runId:string,signal?:AbortSignal):Promise<RunSnapshot>. Private requestJson(path:string,
signal?:AbortSignal):Promise<unknown>, requiredString(object:JsonObject,key:string):string,
nullableString(object:JsonObject,key:string):string|null, invalidResponse():ApiError.
No explicit any, unvalidated casts, validator bypass, untyped override bags or raw error bodies.

events.ts exports StreamStatus 'connecting'|'connected'|'reconnecting'|'closed'|'error';
EventSourceHandle structural native subset: readonly readyState:number, close():void,
onopen:((event:Event)=>void)|null, onerror:((event:Event)=>void)|null,
onmessage:((event:MessageEvent<string>)=>void)|null. Native EventSource satisfies this subset.
parseRunEvent(value:unknown,runId:string,lastEventId:string):RunEvent;
connectRunEvents(runId:string,onEvent:(event:RunEvent)=>void,onStatus:(status:StreamStatus)=>void,
createSource:(url:string)=>EventSourceHandle=(url)=>new EventSource(url)):()=>void.
The optional constructor seam is only for offline native-boundary tests, not a transport abstraction.

useRunEvents.ts exports useRunEvents(runId:string|null):{status:StreamStatus,lastSeq:number,
lastEvent:RunEvent|null}. Private hook state additionally contains runId:string|null. Initial
null identity/closed/0/null. Last event is a display summary only; complete workspace history
must later consume every connectRunEvents callback, not this potentially batched display state.

scripts/dev.py interfaces (all strict typed): check_configuration()->tuple[Settings,ToolSettings];
local_endpoints(settings:Settings,tools:ToolSettings)->tuple[str,str];
_health_ready(url:str)->bool; _exit_code(code:int)->int;
_stop(process:subprocess.Popen[bytes]|None)->None;
run_dev(settings:Settings,tools:ToolSettings)->int; main(argv:Sequence[str]|None=None)->int.
Private _ROOT=Path(__file__).resolve().parents[1], _READY_SECONDS=30.0, _POLL_SECONDS=0.1.
No new exception class: use ConfigError for unsafe/inconsistent config. Module import starts
nothing; __main__ exits main(). No argument parsing that echoes user input. No providers/models
named in this script or frontend. Every cancellation preserves ordered ownership/drain.

## Ordered per-file recipe

### 1. Package, TS, Vite and HTML

package.json private true, type module, name statlab-ui, version0.0.0; exact dependencies
react19.3.0/react-dom19.3.0/@tanstack/react-query5.104.1; exact dev dependencies
vite8.3.2/typescript5.9.3/@types/react19.3.0/@types/react-dom19.3.0/@types/node24.19.1.
Scripts: dev='vite', typecheck='tsc --noEmit', build='tsc --noEmit && vite build',
test='node --test tests/client.test.ts tests/events.test.ts'. engines Node >=22.12.0.
Node24 strips types in tests; use only erasable TS, no enum/parameter properties.
Install exact package file via npm install --ignore-scripts --no-audit --no-fund in frontend;
writer owns resulting lockfile, node_modules and build dist (last two ignored). If sandbox
network fails, retry SAME install with require_escalated for dependency download. Unexpected
package/version/engine failure stops; do not substitute versions or add dependencies.

tsconfig strict/noEmit/noUnusedLocals/noUnusedParameters/noUncheckedIndexedAccess/
exactOptionalPropertyTypes/isolatedModules/allowImportingTsExtensions all true; targetES2022,
lib ES2022/DOM/DOM.Iterable, moduleESNext, moduleResolutionBundler, jsxreact-jsx,
types node/react/react-dom; skipLibCheck true (dependency declarations only), include
src,tests,vite.config.ts. No extends/references/emit files.

vite.config.ts exports defineConfig with server host127.0.0.1, port5173, strictPort true;
/api proxy target from process.env.STATLAB_API_ORIGIN or http://127.0.0.1:8000,
changeOrigin true. Parse URL; accept ONLY http:, hostname127.0.0.1/localhost/[::1],
pathname'/' (empty normalizes '/'), no username/password/search/hash. Else throw fixed Error
'STATLAB_API_ORIGIN must be a loopback HTTP origin.' without input/cause. No root envDir,
VITE-prefixed secrets or browser-accessible backend/MCP credentials. Default build configuration
does not require .env. Build targetes2022, no public remote bind/preview change.
index.html lang en, UTF-8/viewport, titleStatlab, rootdiv and module /src/main.tsx.

### 2. JSON boundary and REST client

isJsonValue checks null, booleans/strings, finite numbers, arrays every recursive element,
and nonarray objects Object.values recursive; reject undefined/functions/nonfinite. isJsonObject
also checks nonnull object/nonarray and isJsonValue. Received JSON cannot contain cycles.
requiredString accepts string (including empty: actual backend field has no extra nonblank
wire guarantee). nullableString accepts null or string, missing undefined rejects.
All parse failures throw ApiError('Backend returned an invalid response.').
parseProjects: root must array; each entry JsonObject; extract exact four string fields.
parseRunSnapshot: root JsonObject, run/state object, gates array, execution_failed boolean;
run.id must equal requested runId; string fields above, exact mode/status unions; nullable fields
explicitly required. For each gate extract four fields and require gate.run_id matches runId.
Return freshly constructed summaries and validated state object. No guesses/defaults.

requestJson performs fetch same-origin path, GET, Accept application/json, signal signal??null.
Catch fetch failure: if signal?.aborted or error is DOMException with nameAbortError, rethrow
original; otherwise fixed ApiError('Cannot reach the backend.'). Non-OK response is rejected
BEFORE reading body: 404 'Resource not found.',409 'Run transition rejected.',422 'Invalid request.',
503 'Backend is unavailable.', all other statuses 'Backend request failed.'; status preserved.
For successful response assign response.json() to unknown. If decode fails, preserve aborted/
AbortError same rule; otherwise invalidResponse. No exception messages/body/details logged/rendered.
listProjects GET /api/projects then parseProjects. getRun rejects trimmed-empty ID with
ApiError('Run ID is required.') before fetch; otherwise encodeURIComponent literal original
runId in /api/runs/{encoded}, then parse matching identity. App trims before calling.

### 3. Native event stream and hook

parseRunEvent requires JsonObject with safe integer seq>=1; exact matching run_id string,
ts/type strings and payload JsonObject. lastEventId must equal String(seq), nonempty canonical
ASCIIdecimal; reject with fixed ApiError('Backend returned an invalid event.'). Use own fixed
event error, no exposing malformed event content. Extra envelope properties ignored.

connectRunEvents first reports connecting, lastSeq0, active true. Construct ONE source at
/api/runs/{encodeURIComponent(runId)}/events?since=0. Blank run ID or constructorfailure:
statuserror and return idempotent no-op cleanup, no thrown native exception. No timers,
sessionStorage or manually reopening EventSource. onopen while active->connected. onerror while
active->closed when readyState===2, else reconnecting (native owns retries/Last-Event-ID).
onmessage active only: JSON.parse assignedunknown then parseRunEvent; validation/decode failure
sets activefalse, closes source once and statuserror, never calls consumer. seq<=lastSeq silently
duplicates skipped; seq===lastSeq+1 updates cursor and invokes onEvent once; a gap sets error,
closes and invokes no consumer. Run/type unknown event kinds still delivered, never filtered.
Consumer callbacks occur outside the parser catch (do not mislabel consumer bugs as wire errors).
Cleanup idempotently sets activefalse, nulls handlers and closes once, reports closed only on
active cleanup; after prior fatal closure no duplicate close or changing error to closed.
Native reconnect same object preserves cursor even if browser fires error/open repeatedly.

Hook effect depends only runId. Null -> reset closed/0/null, no source. Nonnull -> reset identity/
connecting/0/null then connect with callbacks using functional state updates and that identity;
event updates lastSeq/lastEvent and retains status; status updates retains cursor/event. Local
alive flag guards all callbacks; cleanup sets alivefalse BEFORE connection cleanup, then closes.
State returned for mismatched identity must derive immediately as newID connecting/0/null (or
null closed), preventing old run data during the render before effect. No extra subscription,
backoff/REST call per event. StrictMode cleanup/remount remains leak-free.

### 4. Shell and entry point

main.tsx imports CSS, creates ONE QueryClient with queries retryfalse/refetchOnWindowFocusfalse,
requires root element (fixed error if absent), renders StrictMode+QueryClientProvider+App.
App uses useQuery key['projects'], queryFn listProjects(signal). Show headingStatlab and
'Local modelling workspace', project section with pending/error/empty/liststates. Each project
renders name/slug as React text (no HTML), no counts/scores invented. Error text only known
ApiError.message else fixed 'Request failed.'.
Run inspector form: labelled input 'Run ID', controlled draft string, submitpreventDefault,
trim; blank input shows 'Enter a run ID.' and does not activate; valid submission sets activeID.
Snapshot useQuery key['run',activeID], enabled nonnull, queryFn guarding nonnull before getRun;
use abortsignal, no non-null assertions. Subscribe hook only while successful snapshot for that
active identity exists; 404/error/initialloading opens no stream. Switching identity closes old.
Show pending/errorstates then id,mode,status,current_node or 'None', count gates statuspending,
execution_failed yes/no, streamstatus,lastsequence,lastEvent type or 'No events yet.'
Use definition list for facts; aria-live polite status and rolealert errors; buttonInspect run;
no autostart work, secrets, lifecycle/gate actions or pretending snapshotupdates on telemetry.
Note 'Snapshot captured when loaded.' so stale REST facts cannot appear live. Existing sameID
submission may remain cached; no manual reconnect UI yet. Keyboard form works.
CSS systemfont, light background/dark contrast, visiblefocus, readablemaxwidth, modestcards,
responsivegrid collapses narrow screens. No assets/icons/animations/theme dependency.

### 5. Local launcher (stdlib only)

check_configuration calls load_settings, load_tool_settings, check_startup_environment in that
order, returns settings/tools; ToolSettingsError wraps to ConfigError fixed message, missing
tuple copied, causeinternal retained. No paid/network LLM check; existing SDK subprocess is reused.
local_endpoints only used dev: require backendhost/toolhost in127.0.0.1/localhost/::1;
IPv4aliaslocalhost/127 maymatch, ::1 onlymatches::1. Require equal normalized data dirs and
tokens (compare get_secret_value internally); settings.mcp_url parses HTTP, exact /mcp,
matchingtool hostfamily andport, no userinfo/query/fragment. url.port ValueError maps ConfigError
fixed 'Local development configuration is inconsistent.'. No raw inputs. Returns API HTTPorigin
using backendhost/port (IPv6brackets) and MCPhealthurl using toolhost/port /health. Standalone
check retains existing explicit nonlocal opt-out; dev still loopback-only.

_health_ready uses urllib.request.build_opener(ProxyHandler({})), open URL timeout1, context
manager closesresponse, returns status==200. Catch URLError/OSError->False, do not log causes.
URL already built locally. _exit_code maps negative n to128+abs(n), zerounexpectedchildexit to1,
positivecodeunchanged. _stop(None) no-op; otherwise if poll isNone terminate, ignoring only
ProcessLookupError race; always wait with NO timeout/kill. API tool-boundary drain may be long.

run_dev derives endpoints, checks shutil.which('node') and frontend/node_modules/vite/bin/vite.js
exists BEFORE children. Missing->print 'Install frontend dependencies before make dev.' stderr1.
Use fixed absolute Python _ROOT/.venv/bin/python and resolvednode path; no shell/npm wrappers.
Signal handlers SIGINT/SIGTERM saveprevious; nested handler(sig:int,frame:FrameType|None)->None
sets requested_exit130/143 only if previouslyNone; repeatedsignals ignored. Parent never
KeyboardInterrupts owned cleanup once handlersinstalled; restore previous handlers finally.
Each child start_new_session=True, cwdroot (Vite cwdfrontend), stdin/stdout/stderr inherited,
Popen[bytes]. This avoids terminal Ctrl-C broadcast prematurely killing MCP. Commands fixed:
Python -m statlab_tools; Python -m statlab.main; node absolutevite.js. Vite env=os.environ.copy()
plus STATLAB_API_ORIGIN only; never browser-token vars. No shell=True. Narrow S603 noqa comment
on these three Popen calls permitted, explains fixed executables/module arguments and no shell,
as existing routing subprocess pattern; no type ignores/coverage suppression.

Install handlers BEFORE Popen; keep mcp/api/ui references initiallyNone. One try/finally owns
all startup/monitoring and ordered _stop(ui),_stop(api),_stop(mcp), then handler restoration.
Use nested finally blocks so EVERY owned stop and both handler restorations are attempted
even if an earlier stop raises; do not let a UI wait error skip API/MCP cleanup. Start MCP first unless requested;
deadline monotonic+30. Health loop: requested exit wins; childpoll exit returns mappedcode;
checkhealth, then checkrequested/poll AGAIN (child mayexit duringHTTP); successbreak; deadline
expired prints 'MCP readiness timed out.' stderr and returns1; otherwise sleep0.1. Before each
API/UI start check requestedexit, returning it without spawning next. Then monitor repeatedly:
requestedexit wins; poll children MCP,API,UI in that order; first terminated returns mappedcode;
else sleep0.1. No execution threads/globalrun task ownership changes.
Popen/healthsetup/stop OSError wraps public startupfailure at main (after owned finallycleanup);
no force kill/timeouts on backenddrain. Stop sequence retains MCP while API completes SQL/tools.
Signals during cleanup merely set flag, never interrupt waits. Unexpected childexit0->1.
No readiness/start ownership claim based solely on health: childpolls guard exit races, but actual
socket/PID binding remains runtime-unverified; no new healthprotocol/port-probing service added.

main argv default sys.argv[1:]; accepts exactly['check'] or['dev'], else fixed
'Usage: dev.py check|dev' stderr2. Calls check_configuration beforemodebranch. check success
prints 'Configuration OK' stdout0 and neverstartsownedservices. dev returns run_dev result.
ConfigError->stderr 'Configuration is invalid. Check .env and configuration values.' and if
missing tuple nonempty 'Missing: '+joined aliases; return2. Do not render message/cause/input.
OSError->fixed 'Local process startup failed.' stderr1; KeyboardInterrupt beforehandlers->130.

Makefile check-env one $(PY) scripts/dev.py check. api dependscheck-env then $(PY) -mstatlab.main.
ui recipe cd frontend && node node_modules/vite/bin/vite.js (native Vite default127proxy settings).
dev no check-envprerequisite (main does once), $(PY) scripts/dev.py dev. mcp prerequisite unchanged.
Make targets normal shellspaces; no source modifications from main/dryrun.
frontend/README.md documents npm install, npm run typecheck/build, make ui/make dev, Nodeengine,
server-only STATLAB_API_ORIGIN customization, .envrootsecrets neverVITE, early-signal launcher
requirement, runtime tests notrun, scaffold only and snapshot/display caveat. No new workflows.

## Concrete offline tests (write; do not run)

frontend/tests/client.test.ts Node assert.strict/test, import .ts extensions. Tests restore
globalfetch in finally for each case; sequential tests, no live fetch. Fixtures valid project
fourstrings; snapshot RunSummary guided/awaiting_user/current_node plan_models/forknull/endednull,
state {}, one pending gate matchingrun and execution_failed false. Typed factory helpers return
concrete data, negative cases directunknown objects, no override-dictionary helper.
1. parseProjects valid + extraignored; array malformeditem/missingname/rootobject rejectfixedApiError.
2. snapshotvalid/matchinggate; mode'auto',statusunknown,missingnullable,wrongrunid,wronggaterun,
  nonobjectstate/nonboolexecution reject; autonomous/superseded accepted.
3. finiteJSON nestedaccepted; NaN/Infinity/undefined/function rejected.
4. fakefetch receives exact GETpath/Accept/signal; listprojectsreturnsummary; runid 'r/a ?'
  pathencoded, matchingfixtureidentity; blankIDnofetch.
5. parameterHTTP404/409/422/503/500 with bodySECRET fixture rejects exactfixedmessage/status,
  and bodyreader spycount0. NetworkError('SECRET') fixedmessage/statusnull.
6. malformedJSON success returnsfixedinvalidresponse; abortcontroller alreadyaborted and
  rejected DOMExceptionAbortError preserve exacterroridentity (fetch and json phases).

events.test.ts fakehandle fields readyState0/onlistenersnull/closedcount; factoryrecordsurl,
returns samehandle; event delivery uses MessageEvent<string> with JSONdata and lastEventId.
1. parseeventvalid typedpayload candidateid,ts; rejectseq0/fraction/unsafeinteger, wrongrun,
  lastid mismatch/leadingzero/empty, arraypayload; unknown event type accepted.
2. connectstatusconnecting, urlencoded since0, oneconstructor; onopenconnected; events1/2
  alternatingcandidates bothdelivered, duplicate2ignored, cursor continues3 aftererror/open;
  readyState0 errorreconnecting, closed2errorclosed; constructorcountstill1.
3. malformedJSON/invalididentity/gapfirstseq2 each freshconnection: errorstatus, closecount1,
  no consumer; cleanup twice nosecondclose/closedoverwrite; latecallbacksignored.
4. normal cleanup twice close1/closedstatus, listenersnull; saveoldcallbacks then invoke them
  aftercleanup -> noevents/statuschange. constructor throwsSECRET -> error/noescapederror.
5. twoexplicitconnections simulate runchange: closefirst thennewsource seq1 acceptedfornewrun,
  stale firstcallback cannotdeliver; no global cursorstorage. EmptyIDnoconstrution/errorstatus.
No React renderer/test dependency. Hook identity/effect reviewed read-only; browsergap disclosed.

tests/test_dev.py imports scripts.dev; no real processes/network/SDK, monkeypatch all boundary
calls. Typed FakeProcess with name, code:int|None defaultNone, poll()->int|None,
terminate()->None records and code=-15; wait()->int records and returns codeor0. Fakefactory
receives command Sequence[str], cwd Path, start_new_session bool, env Mapping[str,str]|None
via typed named/default args; records commands, returns MCP/API/UI in order. All unrelated
Popen kwargs specified/accepted typed (no explicit any). Monkeypatch signatures permitted
without cast to subprocessclass; production receives realPopen, tests returnstructuralfake.
Signalpatch records handlersold/default and newCallable; invoke capturedhandler fromsleep/
healthfake at concrete stage. Patch shutil.which absolute'/usr/bin/node', Path.is_file only
for expectedvitepath or moduleboundary fileexistence as needed, monotonic deterministiccounter,
sleep no-op/hook, healthtrue default. Settings typed secretfixture, tmpdata/db, tools same.
Tests:
1. maincheck loaders/check_startup patched: callorder backend,tools,routing; printsconfigOK,
  code0, noPopen. ToolSettingsError/ConfigError secretmessage/cause fixedstderr code2/noSECRET;
  missingaliases displayed. Usageunknownfixed2, prehandlerKeyboardInterrupt130.
2. endpoints127default and IPv6matching produce bracketedorigins; hostlocalhost/127alias accepted;
  nonlocal typedSettings(allow_nonlocal_bind=True), wrongurlscheme/userinfo/port/path/query,
  IPv4/IPv6family mismatch, datadir/token mismatch -> fixedConfigError. No validatorbypass.
3. healthfakeopener contextmanager status200true/503false, URLErrorfalse, responseclosed,
  no proxies/timeout1; noactualurllibrequest.
4. run_dev signalSIGINT/SIGTERM parameter130/143: healthtrue, monitorfirstsleep invokeshandler
  twice; starts threefixedcommands/newsession, UIcwd/envpublicorigin; cleanuprecords
  UIterminate/wait, APIterminate/wait, MCPterminate/wait. At APIwait assert MCPstillalive;
  previoussignalhandlers restored. No forcedkill/timeouts.
5. APIpollreturns7 afterstartup ->7, cleanupUIthenAPIwaitthenMCP; return0child maps1; negative
  child -9 maps137. Assert allstartedchildwaitcalledonce.
6. MCPpoll5 beforehealth->5/noAPI/UI; readinessfalse clock0then31->timeout1/MCPcleaned only.
7. healthfake setsSIGINT before success ->130/noAPI/UI, MCPcleaned. Vite Popen raises
  OSError('SECRET') -> mainreturns1/fixedstderr, APIthenMCPcleanup, restoreshandlers.
8. missingnode orvitefile ->1/noPopen; _stop(None) no-op, alreadyexitedprocesswaitonly;
  terminate ProcessLookupError stillwait; _exit_code direct0/7/-9 asserted.
Test counts estimates. Add no mutation/replay requirement: runtime restriction stops them;
unexecuted tests express guards, cannot claim coverage. No paidlive LLM check.

## Permitted focused checks and stop conditions

Only writer: install command above; then .venv/bin/python -m ruff check scripts/dev.py tests/test_dev.py;
.venv/bin/python -m ruff format --check scripts/dev.py tests/test_dev.py;
.venv/bin/python -m mypy scripts/dev.py tests/test_dev.py;
frontend npm run typecheck; frontend npm run build; make -n api ui dev (dryrun only).
Owned-file formatter/import ordering is mechanical and allowed. Direct exec_command actualexit,
fresh logs /private/tmp/b14-chunk36-{install,ruff-check,ruff-format,mypy,typecheck,build,make-dryrun}.log.
Stop first unexpectedfinding/missingdecision; report partialdiff/exactexit/log; no package
substitution, workaround, validator weakening, globalgates or runtimecommands. Do not run
npm test, pytest/collection, check-env, make dev/api/ui, preview, browser or real health calls.

Completion requires allownedfiles, exactlaunchsequence/cancellation, nativeSSEcursor lifecycle,
boundaryguards, offline testinventory, and focusedstatics/buildexit0. Receipt includes baseHEAD,
allchangedpaths+SHA256, exactcommands/exits/fullsummary/logs, deviations/omissions (noneexpected),
runtime/provider/browser/process/coveragegap and nextstagehazards. No acceptance/statusclaim bywriter.
Main reviews terminal candidate then owns one final stable globalstatic acceptance; no globalgate
copiedhere. B14 completeonly after both reviewedstages and finalstatics; B15 remainsunstarted.

## C1 — terminal handoff corrections (main resolved 2026-10-05)

Initial terminal candidate: dependency install0 and dryrun0; Ruff check1 (27 findings), format1
(two files), mypy2 (dev/scripts.dev duplicate mapping), typecheck2 and build2 (two optional-field
errors). Vite never ran. Worker continued independent checks after the first failure; this is a
stop-policy deviation, not passing evidence. This addendum supersedes conflicting initial details.
Same Luna/low writer; no runtime commands. Preserve chunk35 and all unowned edits.

### Ownership, ordering and resolved product corrections

Ownership extends by exactly NEW scripts/__init__.py, empty package marker; no executable content.
This fixes explicit-file/import module identity without changing mypy policy. Other ownership stays
as above. Accepted _host_family(host:str)->str|None returns ipv4 for localhost/127.0.0.1, ipv6 for
::1, otherwise None; no expansion of aliases. Apply in this order:

1. scripts/__init__.py, then scripts/dev.py: import Callable/Sequence from collections.abc and
   suppress from contextlib. _stop ignores only ProcessLookupError around terminate via suppress;
   wait remains outside, unbounded. Infer previous-handler dict from a comprehension calling
   getsignal for both signals BEFORE installation; do not annotate it with a narrower union than
   typeshed (getsignal also admits int/None/callback returns). Install handlers in the existing loop.
   Remove result assignments; each branch directly returns its already specified value. Remove
   unreachable return after the try/finally. At the very start of the owned try, BEFORE MCP Popen,
   check requested_exit and return it if set. Keep all later pre/post readiness/spawn checks and
   nested UI/API/MCP cleanup and signal restoration unchanged. No force-kill or timeout added.
2. frontend/package.json and README: Node engine >=24.0.0 and documented Node24 minimum. Keep
   exact pins/scripts. Node22.12 is insufficient for this unflagged native TypeScript test command.
   Update lock metadata with npm install --package-lock-only --offline --ignore-scripts --no-audit
   --no-fund in frontend; no version changes. Cache failure returns to main, no network workaround.
   tsconfig adds erasableSyntaxOnly:true and verbatimModuleSyntax:true. ApiError declares readonly
   status:number|null as a class field; constructor(message:string,status:number|null=null) assigns
   it after super. No parameter property. Other public signatures/defaults stay unchanged.
   Main verified native strip-types limitations and compiler options against
   [Node docs](https://nodejs.org/api/typescript.html) and
   [TypeScript docs](https://www.typescriptlang.org/tsconfig/erasableSyntaxOnly.html).
3. vite.config.ts: declare parsedOrigin:URL; assign new URL(origin) inside try. Constructor catch
   throws the SAME fixed Error as the existing invalid-origin branch, no cause/input. Existing
   protocol/host/path/userinfo/query/fragment checks remain. No new helper/config API.
4. events.ts: existing private close closure sets closed=true, nulls all three handlers BEFORE
   calling source.close. Cleanup delegates that same close path; remove its redundant handler
   nulling. Fatal validation/gap still sets active=false, closes exactly once and reports error;
   subsequent cleanup leaves error intact. Saved callbacks remain gated by active. No extra source.
5. App.tsx: label Pending gates and count only gates with status exactly pending, as specified.
   Preserve query keys, snapshot identity guard, cancellation signals and current shell scope.
6. Offline tests below, then owned Python mechanical import ordering and formatter. Do not alter
   guards to satisfy a test; failures return to main.

### Frontend offline test corrections

client.test.ts requests entries have REQUIRED init:RequestInit|undefined (absence is represented
as explicit undefined). Encoded-ID fetch returns concrete snapshot with run.id='r/a ?' and its
gate.run_id='r/a ?'; getRun assertion equals that same ID, encoded path unchanged. HTTP failures
use actual Response('SECRET',{status}); replace its json method with a typed async spy incrementing
bodyReads and returning secret object. Return that Response without a Response cast. Assert no
body reads, exact fixed ApiError/status; retain fetch restoration finally and all existing cases.

events.test.ts FakeSource.emit(seq:number,runId?:string,id?:string,candidateId?:string):void,
defaults r1/String(seq)/c1. Payload uses candidate_id. In reconnect case emit seq1 c_a, seq2 c_b,
duplicate2 c_b, seq3 c_a; collect RunEvent[] and assert seqs[1,2,3] and candidate IDs[c_a,c_b,c_a].
Each fatal case saves onmessage/onopen/onerror before emission, then checks all handlers null;
invoke saved callbacks after cleanup twice, assert no delivery/status change and close1. Run-switch
case saves first.onmessage BEFORE first cleanup, then delivers seq1 r2 to second and invokes saved
first callback with VALID seq1 r1 envelope; only r2:1 delivered. Keep parser/constructor/blank cases.

### Python offline test setup and complete inventory

Replace hardcoded /tmp paths with tmp_path. configuration(tmp_path:Path,*,api_host:str='127.0.0.1',
tool_host:str='127.0.0.1',mcp_url:str|None=None,tool_data_dir:Path|None=None)->tuple[Settings,ToolSettings].
Data dir tmp_path/'data', db there/statlab.db, test-token, ports8000/8765. If mcp_url is None derive
HTTP tool_host (IPv6 brackets) /mcp; tools dir None means same data dir. Set allow_nonlocal_bind=True
on both typed settings ONLY to let dev's stricter boundary be exercised; no model_construct,
model_copy, dict overrides or validation bypass. All helper callers pass tmp_path. Positive IPv6
therefore has IPv6 MCP URL. Errors assert exc.value.message exactly, not unescaped regex.

Extend FakeProcess constructor(name:str,code:int|None=None,*,history:list[str]|None=None,
on_wait:Callable[[],None]|None=None). poll unchanged. terminate appends own terminate and optional
history name:terminate, sets code -15. wait appends own wait/history name:wait, calls on_wait if
present, returns code or0. Test race subclass override terminate()->None records attempt then raises
ProcessLookupError; _stop must still wait. No subprocess inheritance or casts.

Reuse one typed setup helper install_runtime(monkeypatch:pytest.MonkeyPatch,
processes:list[FakeProcess])->tuple[list[tuple[Sequence[str],Path,bool,Mapping[str,str]|None]],
dict[int,object],list[tuple[int,object]]]. It installs which('/usr/bin/node'), is_file true ONLY for
expected vite.js, monotonic0, healthtrue, sleep no-op, getsignal SIG_DFL. Typed fake Popen signature
matches existing factory; record command/cwd/session/env then return processes by start index.
Signal installer stores handlers and ordered signal_calls, returning prior handler/default.
No dynamic kwargs/override dictionaries. Each test supplies a finite exit hook; never rely on
no-op sleep for an infinite monitor. Invoke handler only after assert callable. Use this helper,
with test-local monkeypatch replacements for the specified exceptional cases. All real boundaries
remain patched. Helper functions have explicit return annotations, including patched callbacks.

Complete the original eight groups, using these exact actions/assertions (names may describe them):
1. Preserve maincheck call order/OK/noPopen and unknown usage. Add ToolSettingsError('SECRET',
   missing=('STATLAB_MCP_TOKEN',),cause=RuntimeError('SECRET')) loader failure, and separate backend
   ConfigError with same secret/missing fixture. Exact safe stderr plus missing alias, code2,
   noSECRET. Separate loader raising KeyboardInterrupt before handlers -> maincheck130.
2. Positive endpoint rows include default127, API127/toollocalhost and both::1; expected full
   origin/health strings. Negative mcp_url rows: https://127.0.0.1:8765/mcp,
   http://user:SECRET@127.0.0.1:8765/mcp, http://127.0.0.1:9999/mcp,
   http://127.0.0.1:8765/other, http://127.0.0.1:8765/mcp?x=1,
   http://127.0.0.1:8765/mcp#x, http://[::1]:8765/mcp, and
   http://127.0.0.1:invalid/mcp. Each rejects fixed ConfigError, even URL-port ValueError.
   Also API0.0.0.0, tool0.0.0.0 (typed explicit opt-out above), tools tmp_path/'other', and tools
   typed token different-token with same dir/host/port each reject. No new production guard.
3. FakeResponse.__init__(status:int)->None sets status and closed=False; __enter__()->FakeResponse;
   __exit__(exc_type:type[BaseException]|None,exc:BaseException|None,
   traceback:TracebackType|None)->None marks closed (discard unused args). FakeOpener.open
   (url:str,*,timeout:float)->FakeResponse records URL/1 and
   returns response. FakeProxyHandler.__init__(proxies:dict[str,str])->None stores proxies; patch
   ProxyHandler to that class. Patched build_opener(handler:FakeProxyHandler)->FakeOpener asserts
   handler.proxies=={} then returns opener. FakeOpener.__init__(response:FakeResponse)->None stores
   response and calls:list[tuple[str,float]]; open appends request then returns response. status200
   true and503false, both closed. Separate FakeOpener subclass overrides open with same signature,
   raises URLError('SECRET') ->False; no network.
4. Parameter signals SIGINT130/SIGTERM143: three FakeProcesses sharing history; API on_wait
   asserts MCP.code is None. First monitor sleep invokes captured handler then second different
   signal; first code wins. Assert exact three executable/module commands, root/root/frontend
   cwd, sessions true, UI origin env. History exactly ui:terminate,ui:wait,api:terminate,api:wait,
   mcp:terminate,mcp:wait; every wait once; last two signal calls restore SIG_DFL.
5. Parameter API exits7/0/-9: monitor firstsleep sets API.code; expected7/1/137. History UI
   terminate/wait, APIwait only, MCPterminate/wait; every started wait once. Do not prematurely
   end MCP: current initial test ending MCP cannot prove API drains while MCP lives.
6. MCP initialcode5 ->5, exactly one start, no healthcall (patch health pytest.fail), wait only.
   Timeout: healthfalse, monotonic iterator [0,31], no sleeping needed ->1, fixed timeoutstderr,
   only MCP started/terminate/wait. No actual elapsed-time wait.
7. Health hook invokes SIGINT and returns True ->130, only MCP start/cleanup. Vite-spawn-error
   test patch loaders/routing to valid settings, fake Popen records thirdattempt then raises
   OSError('SECRET'); main(['dev'])->1, exact fixed startupstderr/noSECRET, history APIterminate/
   wait then MCPterminate/wait, all handlers restored, no UI process falsely waited.
8. Missingnode and missingvite are distinct parameter cases ->1, fixed installstderr, no Popen.
   _stop(None), alreadyexitedprocess ->waitonly, terminate-race ->attempt+wait, _exit_code0/7/-9.
9. New pre-MCP cancellation window: signal installer invokes newly installed SIGINT handler
   when installing SIGTERM, before owned try. run_dev returns130, NO Popen, both saved handlers
   restored. No process exists to clean. This exercises a reachable signal between setup/spawn.

No runtime selector is authorized now. Tests are executable future evidence, not claimed results.
Node type stripping is statically enforced; malformed proxy errors and App pending-gate filtering
are reviewed source properties. No extra dependency, renderer or configuration test file.

### Exact C1 checks and handoff

Writer may mechanically run ruff check --select I --fix and ruff format on ONLY scripts/__init__.py,
scripts/dev.py, tests/test_dev.py. Then sequential checks, STOP first unexpected failure:
- .venv/bin/python -m ruff check scripts/__init__.py scripts/dev.py tests/test_dev.py
- .venv/bin/python -m ruff format --check scripts/__init__.py scripts/dev.py tests/test_dev.py
- .venv/bin/python -m mypy scripts/__init__.py scripts/dev.py tests/test_dev.py
- frontend npm run typecheck
- frontend npm run build
- make -n api ui dev
Use fresh /private/tmp/b14-chunk36-c1-{lock,ruff-check,ruff-format,mypy,typecheck,build,make-dryrun}.log.
Record lock update and formatter genuine exits too. No source work outside ownership, runtime
tests/collection, global gates, services or commits. Full terminal receipt: exact files/SHA256,
commands/exits/summaries/logs, deviations, runtime/coverage gaps. Missing decision returns to main.

### R1 — interrupted C1 recovery

Daemon restart interrupted chunk36 writer; runtime lists only root, so the old child cannot be
reused. This is orchestration loss, not a product failure/model escalation. Main inspected saved
candidate: empty package marker, launcher C1 behavior, engine/lock/compiler fields, proxy error
mapping, ApiError field, event cleanup, pending-gate count and client-test fixes are present.
Only lock log exists (up-to-date output, no recovered terminal exit); no C1 static receipt exists.
Preserve these edits. Do NOT repeat install/lock update or reimplement completed product steps.
Remove now-unused Callable from scripts/dev.py imports; only Sequence remains from collections.abc.
Unfinished scope: C1 events.test.ts changes, entire tests/test_dev.py C1 fixture/inventory rewrite,
owned Python mechanical format/import ordering, and C1 sequential checks. New recovery writer is
still Luna/low implementer, fork none, no nested agents; existing stage specification stays authority.
Owned source for recovery: frontend/tests/events.test.ts, tests/test_dev.py, scripts/dev.py (unused
import/format ONLY), scripts/__init__.py (format ONLY). All other product paths read-only; writer
may run frontend typecheck/build. Unexpected failure returns to main, ownership must not expand.
Use /private/tmp/b14-chunk36-r1-* logs. Receipt hashes all stage-owned files, naming preserved
partial edits and unrecovered lock exit. No process/test/service execution, force-kill or commit.
## C2 — reviewed recovery corrections

R1 formatter0 (two reformatted); first Ruff1, three findings, no later checks. Main read both test
files completely. Ownership for this correction: tests/test_dev.py and frontend/tests/events.test.ts
only; scripts files read-only. Preserve all other work. Same recovery writer; no runtime tests.

1. tests/test_dev.py: remove unused FrameType import. Remove unauthorized tool_token parameter
   from configuration; tools always gets SecretStr('test-token'). Use explicit None defaults for
   mcp_url/tool_data_dir, as C1 specified. Three mismatch tuple rows remain api0.0.0.0, tool0.0.0.0,
   otherdir; remove token column. After that loop construct a separate ToolSettings with
   SecretStr('different-token'), settings.data_dir, host127.0.0.1, port8765,
   allow_nonlocal_bind=True; use freshly obtained default settings and assert same fixed error.
   No secret-looking function defaults and no S107 suppression.
2. Split fixed configuration stderr literal into adjacent strings at newline; keep exact equality.
   Backend ConfigError case also captures stderr once and asserts that same full equality and no
   SECRET (current noSECRET alone omitted expected alias). Vite-spawn-error captures stderr once,
   asserts exact fixed startup message and noSECRET on that same captured value; current second
   capsys read made noSECRET assertion vacuous.
3. health test: replace build_opener lambdas with one local typed function
   build_opener(handler:FakeProxyHandler)->FakeOpener that asserts proxies=={} and returns current
   opener. Patch once; then reassign response/opener for503 and FailedOpener cases, closure sees
   current opener. Assert calls[(healthURL,1)] for both200 and503 and closed for both.
4. Signal drain test: final two signal_calls must equal [(int(SIGINT),SIG_DFL),
   (int(SIGTERM),SIG_DFL)], not just the signal IDs. Assert each started process actions.count(wait)
   exactly1. Vite-spawn-error likewise asserts final two actual restored values and len(commands)==3;
   only MCP/API wait once. Existing history assertion stays.
5. Pre-MCP signal fake installer currently invokes during restoration as well, when handler became
   SIG_DFL; this makes its callable assertion fail. Invoke captured SIGINT only when sig==SIGTERM
   AND callable(handler), i.e. installation of the new handler; default restoration must not invoke.
   Assert restored[-2:] exact default tuples plus commands==[] and run_dev130. No production change.
6. frontend/tests/events.test.ts imports RunEvent with import type from ../src/api/types.ts, its
   actual defining module. events collection annotation RunEvent[], remove wrong inline import
   from events.ts. No new export/re-export in production. All other C1 event tests are accepted.

Mechanical formatter/import ordering only these two files. Then the SAME C1 sequential scoped
checks (three Python paths, frontend typecheck/build, Make dryrun), fresh
/private/tmp/b14-chunk36-c2-* logs. Stop first unexpected failure; no broadening/weakening. Full
terminal stage receipt includes ALL nineteen stage-owned paths/hashes, genuine exits/summaries/logs,
preserved partial implementation and unverified interrupted lock exit. No runtime/coverage claims.

## C3 — small syntax and command correction

C2 command failed1: Ruff was incorrectly given a TypeScript file, producing parser diagnostics;
the first Python stderr equality also lost its left operand. No subsequent checks ran. Main read
the affected source. Same writer, ownership ONLY tests/test_dev.py for three resolved edits:
1. In the FIRST ToolSettingsError stderr assertion, restore left operand error before equality;
   compare it to the existing two adjacent string literals. Keep one valid parenthesized equality,
   remove the redundant nested parentheses left by C2 if needed for that syntax only.
2. configuration mcp_url assignment uses explicit conditional: None derives tool URL, otherwise
   use the supplied string verbatim (including empty). Existing or expression must be replaced.
3. configuration tools data_dir assignment uses explicit conditional: None chooses data_dir,
   otherwise supplied tool_data_dir. No other behavior or signature changes.
All other C2 changes are saved; preserve them. TypeScript is NOT input to Ruff/mypy/formatter.
Before checking, permitted exact mechanical commands are
.venv/bin/python -m ruff check --select I --fix tests/test_dev.py and
.venv/bin/python -m ruff format tests/test_dev.py ONLY.
Then sequentially: Ruff check, Ruff format --check and mypy on scripts/__init__.py scripts/dev.py
tests/test_dev.py; frontend npm run typecheck; frontend npm run build; make -n api ui dev.
Fresh /private/tmp/b14-chunk36-c3-* logs, genuine exits; stop first unexpected finding. No runtime,
new dependencies, source outside ownership, globals or commits. Same full terminal receipt.

## C4 — resolved strict typing boundaries

C3 Python import fix/formatter/Ruff check/format all exit0; scoped mypy exit1 with24 diagnostics.
Main read complete launcher/tests and all callers. No later check ran. Ownership ONLY
scripts/dev.py and tests/test_dev.py; all frontend/Makefile/other scripts remain read-only.
Same recovery writer; preserve all saved changes. No runtime execution or configuration change.

### Launcher edits, in order

1. _health_ready retains its signature, proxy-free opener, context close and errors. Return a bool
   conversion of the existing response.status equality with200. urllib's dynamically typed status
   currently leaks an inferred dynamic return; bool establishes the declared boundary without a
   cast, new protocol, explicit dynamic type, changed HTTP behavior or additional branch.
2. run_dev replaces scalar nonlocal requested_exit with a one-element list[int|None], initially
   [None]. This cell holds only the first requested signal exit. handle_signal has the same
   signature, drops frame, checks element0 for None, sets element0 to130 for SIGINT else143;
   remove nonlocal (the list itself is never rebound). No added abstraction or signal registration.
3. At EACH of the six existing cancellation checkpoints, read element0 into local exit_code,
   then return that local if it is not None. Preserve exact checkpoint locations: pre-MCP spawn;
   start of readiness loop; immediately after health; before API spawn; before UI environment/
   spawn; start of monitor loop. Reading a mutable cell reflects asynchronous callback changes
   and avoids mypy incorrectly retaining the initial scalar narrowing across callback execution.
   Preserve first-signal wins, early cancellation, all process polls/deadline checks/order,
   unbounded UI/API/MCP drain and saved-handler restoration. _stop signature stays Popen[bytes]|None.

### Test edits, in order

1. Import shutil, time and urllib.request directly as stdlib modules. Replace ALL dev.shutil,
   dev.time, dev.signal and dev.urllib.request monkeypatch targets with shutil, time, signal and
   urllib.request respectively. Existing direct signal/subprocess imports stay. These are the
   exact same module objects used by production; no production import re-exports or bypasses.
2. In test_check_configuration_order_errors_usage_and_interrupt replace append-or-value lambdas
   with local backend_settings()->Settings, tool_settings()->ToolSettings and routing()->None.
   First appends backend then returns settings; second appends tools then returns tools; third
   appends routing and returns implicitly. Patch the three existing loaders/checker with these
   functions. Preserve existing call-order, stdout/error/usage/cancellation assertions.
3. In test_stop_race_exit_codes_and_pre_mcp_signal, keep _stop(None), exit-code assertions and
   the later pre-MCP signal case. Obtain settings/tools before the process cases. Do not pass
   FakeProcess directly to _stop, cast it, inherit Popen, broaden _stop's contract or spawn a child.
   Instead use the existing install_runtime factory boundary and run_dev for the two cases:
   - done=FakeProcess('done',4); install_runtime with [done]; patch _health_ready to pytest.fail
     if called. Assert run_dev==4, len(commands)==1, actions exactly['wait']. This reaches the
     genuine already-exited MCP cleanup path with the declared Popen boundary monkeypatched.
   - VanishedProcess remains a FakeProcess subclass whose terminate records then raises
     ProcessLookupError. install_runtime with [vanished], retain its installed-handler mapping.
     Add local vanished_health(url:str)->bool: discard url; obtain installed SIGINT handler,
     assert callable, call it with SIGINT and None, return True. Patch _health_ready with it.
     Assert run_dev==130, len(commands)==1, actions exactly['terminate','wait']. Cancellation
     after health reaches cleanup; ProcessLookupError suppression must still wait once.
   The subsequent pre-MCP case resets factory/signal targets as before; its existing assertions
   remain. No new fake protocol/class beyond existing VanishedProcess, no test weakening.

### Checks and stop

Mechanical import ordering/formatting ONLY scripts/dev.py and tests/test_dev.py. Then sequential
Ruff check, Ruff format --check, mypy on scripts/__init__.py scripts/dev.py tests/test_dev.py;
frontend npm run typecheck; frontend npm run build; make -n api ui dev. Use fresh
/private/tmp/b14-chunk36-c4-* logs, genuine exits; stop first unexpected failure and return it
without workaround. No Ruff/mypy over TypeScript, runtime tests/collection, probes/services,
global gates, dependencies, commits or plan/status edits. Terminal receipt covers all nineteen
stage paths/hashes, exact commands/exits/results, preserved edits and unrecovered lock exit.

## C5 — local annotation collision

C4 import fix/format/Ruff check/format exit0; mypy1 has one no-redef in the combined stop test.
Main read changed launcher and entire affected test. C4 behavior is accepted; no later checks ran.
Ownership ONLY tests/test_dev.py. In test_stop_race_exit_codes_and_pre_mcp_signal rename the
FINAL explicitly annotated empty commands list (the pre-MCP cancellation case) to
pre_spawn_commands. Its local popen closure appends to pre_spawn_commands, and final empty-list
assertion checks pre_spawn_commands. Earlier done/vanished commands locals remain unchanged.
No signature, fixture or behavior change. Same writer; preserve all other work.
Mechanical Ruff import ordering/formatting ONLY tests/test_dev.py if needed. Repeat the same
sequential scoped Ruff check/format/mypy (scripts/__init__.py scripts/dev.py tests/test_dev.py),
then frontend npm run typecheck, frontend npm run build, make -n api ui dev. Fresh
/private/tmp/b14-chunk36-c5-* logs; stop first unexpected finding. Same terminal receipt and
nineteen-path hashes. No runtime, globals, dependencies, source expansion or commits.
