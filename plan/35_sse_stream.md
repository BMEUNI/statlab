---
status: implemented
dependencies: [22, 34]
completion_date: 2026-10-05
implementation_notes: "B14 reviewed and static-only accepted; scoped/global mypy and Ruff exit0. Runtime tests, collection, probes, mutations and coverage remain stopped; receipt plan/receipts/B14.md."
---

# Chunk35 — durable SSE and prose deltas

## Goal and non-goals

Implement D9 / PLAN §11.1: one run stream, persisted envelope replay, atomic replay/live
handover, 15-second comment heartbeat, genuine provider text chunks for Q&A. Structured
decisions/gates/classification remain complete validated objects, never streamed. D14/D16/D18/
D27/D32/D36 remain binding. No frontend, artifact/report routes, cost enforcement, new packages,
new execution tasks, narration-only extra LLM requests, status polishing, or changes to restart,
gate/fork/revision/lineage algorithms. B13 receipt is static-only; B8 is last runtime acceptance.
Runtime checks remain prohibited. Chunk36 spec waits for this stage's terminal reviewed handoff.

## Ownership and inspected seams

Only these product paths: new `backend/statlab/api/stream.py`, new `tests/test_api_stream.py`,
new `tests/test_llm_stream.py`; change `backend/statlab/api/runs.py`,
`backend/statlab/main.py`, `backend/statlab/llm/client.py`,
`backend/statlab/graph/control.py`, and `tests/test_api_runs.py`.
No worker plan/status edits, commits, nested agents, independent design or other files.
Worker is not alone; preserve other edits. Main owns all decisions/review. Unspecified choice,
missing helper or unexpected check failure stops affected work and returns to main.

Reuse Event/EventBus in events.py unchanged: subscribe atomically queues replay and registers
live subscriber under one lock; append writes/flushes disk before publishing; torn tails repaired
on reopen; one writer per run/process. Use binding.loop.events exclusively; never open a second
bus for a live binding. RunRegistry.run validates SQL ownership and immutable paths/snapshots
without scheduling; binding cache remains authoritative. request() tracks admitted requests;
shutdown drains requests, then execution continuations, before MCP/SQLite close.

Main inspected runs.message/run/request/shutdown; main.create_app/lifespan; ControlContext,
answer_question, explain_status and router.handle/classify; all production complete callers in
domains.runtime, domains.binding revision, graph.gates and graph.rewind; complete_setup in
api.projects; LlmClient complete/_complete/_request and accounting helpers; tests/test_events,
test_api_runs fixtures, test_llm_client helpers, test_control_router FakeClient/context and
test_control_qa_status callers. Their default unstreamed behavior must remain compatible.

Installed guarantees inspected read-only: LiteLLM 1.100.0 CustomStreamWrapper has async iteration
yielding ModelResponseStream and async aclose; stream_chunk_builder returns
ModelResponse | TextCompletionResponse | None and includes usage. StreamingChoices.delta.content
is prose/JSON content; reasoning fields are separate and must never be emitted. Pydantic core
from_json allow_partial='trailing-strings' includes unfinished string values and decodes escapes;
ValueError means parsing failed, not a validated decision. Full model_validate_json still owns
validation/one repair. Starlette StreamingResponse does not guarantee generator aclose on a
failed ASGI2.4 send; our response wrapper explicitly closes it. Uvicorn.shutdown waits HTTP tasks
BEFORE lifespan shutdown; its handle_exit hook must signal stream closure before that wait.

## Exact interfaces and validation

In api/stream.py: `_HEARTBEAT_SECONDS = 15.0`; `parse_cursor(since: int,
last_event_id: str | None) -> int`; `event_frame(event: Event) -> str`;
`stream_events(bus: EventBus, *, since: int, closing: asyncio.Event)
-> AsyncGenerator[str]`; `SseResponse(stream: AsyncGenerator[str]) -> SseResponse` constructor;
async SseResponse.__call__(scope: Scope, receive: Receive, send: Send) -> None;
`stream_router(registry: Callable[[], RunRegistry]) -> APIRouter`.
Avoid runtime circular imports: RunRegistry import under TYPE_CHECKING, annotations postponed.

GET /api/runs/{run_id}/events accepts `since: int = 0` using FastAPI Query(ge=0), and
`last_event_id: str | None = None` using Header(alias='Last-Event-ID'). The header, if supplied,
must be nonempty ASCII decimal digits only (no sign, whitespace, Unicode digits, floats).
Convert with int; ValueError including Python's digit-limit maps to422 'invalid request'.
Effective cursor is max(since, header integer), supporting native EventSource reconnect using
its unchanged URL. Missing header uses since. Future cursors are accepted and tail live.
Unknown run404 via registry.run, malformed query/header422, closing registry503. Validate and
resolve binding BEFORE response headers. No lifecycle/audit creation, mutation lock or launch.
Response status200, media_type text/event-stream, headers Cache-Control=no-cache and
X-Accel-Buffering=no. Frame is id decimal seq newline, data compact Event.model_dump_json
newline newline; no event: field (all events use EventSource.onmessage). JSON escapes embedded
newlines, so there is exactly one data line. Heartbeat literal ': heartbeat\n\n', no id/seq/disk
append. Terminal run status does not close the subscription: later chat/control still allowed.

RunRegistry adds `streams_closing: asyncio.Event` initially unset and
`begin_shutdown() -> None`, idempotently setting closing=True and streams_closing.set().
shutdown first calls begin_shutdown(), then retains existing drain/pause/gather/error ordering.
Stream generator owns precisely two asyncio tasks: pending anext(subscription) and
closing.wait(); it never owns/cancels run execution or LLM/control requests.

main.py adds `_RunServer(uvicorn.Server)` with constructor
`(application: FastAPI, settings: Settings) -> None`, field `_application: FastAPI`,
override `handle_exit(sig: int, frame: FrameType | None) -> None` and `main() -> int`.
Constructor passes uvicorn.Config(application, host=settings.api_host, port=settings.api_port)
to super. create_app lifespan publishes `application.state.run_registry` after registry creation
and clears it to None when clearing active_registry (remove del application). handle_exit gets
that state attribute with default None, narrows with isinstance(RunRegistry), invokes
begin_shutdown if present, then return if should_exit is already true; otherwise super.handle_exit
(C3 preserves drain on repeated signals). main loads
settings, makes app/settings server, calls server.run(), returns0; ConfigError prints the fixed
text `Backend configuration is invalid. Check .env and configuration values.` to stderr and
returns2. Never print the exception, message, cause or validation input. __main__ guard exits
with main(). Existing global lazy app stays.
Supported launcher is python -m statlab.main; chunk36 Makefile will use it. Arbitrary external
uvicorn statlab.main:app lacks the early signal hook; record this explicit launcher constraint.

LlmClient adds `type ProseSink = Callable[[str, bool], Awaitable[None]]` (text suffix, reset).
Add @runtime_checkable private Protocol `_ProseStream` with
`__aiter__(self) -> AsyncIterator[ModelResponseStream]` and `aclose(self) -> Awaitable[None]`.
Add async `complete_prose[T: BaseModel](conn: sqlite3.Connection, *, run_id: str,
node_execution_id: str, model: str, messages: Sequence[dict[str,str]], output: type[T],
sink: ProseSink, reasoning: bool = False, example: dict[str,JsonValue] | None = None,
recovery: CallRecovery | None = None) -> T`.
Original complete and complete_setup signatures unchanged, preserving FakeClient overrides.
complete_prose verifies the same execution SQL run ownership as complete (extract private
`_verify_execution(conn, run_id, node_execution_id) -> None`, used by both), and requires output
schema properties.text.type == 'string'; otherwise LlmCallError bug 'prose output has no text
field' before provider call. Schema lookup failure becomes LlmCallError bug, no cause disclosure.
Private _complete/_request gain `sink: ProseSink | None = None`, forwarded; old defaults unchanged.
New async `_consume_prose(raw: object, *, sink: ProseSink,
messages: list[dict[str,str]]) -> ModelResponse` and `_partial_text(content: str) -> str | None`.

ControlContext adds `prose_sink: ProseSink | None = None` after existing public default fields.
answer_question and polished explain_status choose complete when absent; choose complete_prose
with identical arguments plus sink when present. Do not pass new kwargs to complete.
API still polish_status=False. Classification, changes, gates, revision and nodes use complete.

## Ordered per-file algorithms

1. llm/client.py: extract execution verifier without changing error messages/SQL. Add
   complete_prose validation then call shared _complete with sink. Reuse tiers/schema injection,
   messages copying, repair and recovery exactly. _request sets stream to sink is not None;
   when streaming add stream_options={'include_usage': True}, leaving other kwargs unchanged.
   Inside existing semaphore/started clock/provider try: for sink None use old call; otherwise
   async with asyncio.timeout(60) around provider acquisition AND consumption, call sink('', True)
   before provider acquisition on EVERY attempt (including recovery retries/repair), acquire raw
   via _acompletion then consume. No hidden retry. Failed/cancelled attempts retain existing
   zero accounting; cancellation re-raised. TimeoutError is transient (add to transient tuple).
   PersistenceError from sink must propagate as PersistenceError rather than provider bug;
   catch it explicitly before generic Exception. Foreign provider/consumer failures use existing
   LlmCallError mapping/accounting. Successfully assembled ModelResponse goes through existing
   _record_response and full output validation exactly once per attempt. Signature output class,
   not a new synthetic signature. Semaphore held for entire provider stream.
2. _consume_prose checks isinstance(raw, _ProseStream), else raises LlmCallError bug 'LLM
   returned a malformed stream'. Store typed chunks, aggregate content='', emitted=''. In try/finally
   async iterate: require each chunk ModelResponseStream else bug malformed stream; append chunk;
   choices empty allowed usage-only; use first choice only; require index0 for first choice else
   bug malformed stream; delta.content None/empty ignored; content must str else bug malformed
   stream. Accumulate ONLY content, never reasoning/tool fields. Reject aggregate JSON content
   longer than 131072 characters with LlmCallError bug 'prose response exceeds limit' before
   partial parsing; this fixed bound protects provisional parsing against untrusted oversized
   output (not a new setting). _partial_text calls from_json
   with allow_partial='trailing-strings', allow_inf_nan=False, binds result as object; ValueError
   returns None; return root dictionary's text only when str, otherwise None. If prefix missing,
   wait; if decoded text does not start with emitted, raise LlmCallError bug 'prose prefix changed'
   (duplicate text key/non-monotonic stream cannot retract text). For a longer prefix await sink
   with suffix and reset=False, then update emitted. After exhaustion call SDK stream_chunk_builder
   with chunks/messages; require ModelResponse else bug malformed stream. Require assembled
   _content exactly equals accumulated content (mismatch bug malformed stream). Return response.
   Finally always await raw.aclose(), preserving cancellation; SDK close shields its own cleanup.
   Protocol-based tests' close cannot throw. Parsing is provisional prose only; final schema/
   citation validation still mandatory. Bad JSON can reach one repair; reset clears previous draft.
   Prefix violations/provider failures do not receive semantic repair. O(n²) partial parse of
   bounded chat JSON: add ponytail comment naming bound and incremental parser upgrade only if
   chat sizes justify it. No bespoke JSON state machine, no network probe.
3. graph/control.py: add sink and dispatch choice in two prose call sites only. Preserve all
   captured facts/context/refs validation, role/model precedence and no-action semantics.
4. api/runs.py: initialize signal, add begin_shutdown and use it first in shutdown. Within
   message after user persisted/audit/state built, define async prose sink(text: str, reset: bool)
   -> None appending message.assistant.delta to binding.loop.events with payload
   {reply_to: user.id, session_id: user.session_id, text, reset}. Pass to ControlContext. Wrap
   router.handle to append error on LlmCallError/LlmOutputError with payload
   {reply_to:user.id, message:'Assistant response failed.', discard_draft:True}, then re-raise.
   On asyncio.CancelledError append same error with public message 'Assistant response cancelled.'
   then re-raise; if diagnostic append PersistenceError occurs preserve original cancellation.
   Other sink/persistence failures propagate without fictitious complete. No raw cause in events.
   Existing EngineError409 preserved. On success persist only final validated assistant text/meta;
   complete event adds reply_to:user.id alongside current fields. Nonprose responses get complete
   only. No fabricated assistant message until validation. Existing scheduling order retained.
5. api/stream.py: resolve effective cursor and cached binding; response wraps stream_events.
   Generator uses bus.subscribe(since) and creates one pending anext task and one closing wait
   task. If closing already set, return before subscribing. Wait asyncio.wait FIRST_COMPLETED
   with timeout15. Closing wins even if next event ready: break without draining backlog.
   On timeout yield heartbeat, retain pending anext (never wait_for/cancel on heartbeat).
   On event-ready get task.result (StopAsyncIteration ends), yield event_frame, create next
   anext only when generator resumes. Finally cancel both remaining tasks, await gather
   return_exceptions=True, then subscription.aclose; retrieve failures but do not swallow the
   original generator exception/cancellation. SseResponse constructor stores exact async generator,
   sets response parameters; __call__ try await super, finally with anyio.CancelScope(shield=True)
   await stored generator.aclose. This is response-local cleanup, not detached background work.
6. main.py: include stream_router using same registry getter. Publish/clear registry state in
   existing lifespan. Add server/main as above; imported lazy app makes no paid/network call.
7. tests: fixtures and cases below only; no production changes to make tests easier.

## Concrete cases and test recipe

Runtime is STOPPED. Add runnable offline tests but do not execute pytest/collection/import
probes/server/browser, mutation/replay, or coverage. No mutation requirement beyond these tests;
restoration probes are not appropriate while execution is stopped. Counts are estimates.

tests/test_llm_stream.py imports llm_context/_settings/_response/_rows from test_llm_client.
New slots dataclass FakeStream: chunks tuple[ModelResponseStream,...], closed bool=False,
entered/release optional asyncio.Event, error Exception | None=None. Async __aiter__ generator
yields chunks in order, signals/waits optional events before final chunk, then raises error when
supplied. aclose sets closed=True. New chunk helper(content: str | None, *, usage: bool=False)
-> ModelResponseStream: index0/Delta(content), model='test-model'; usage chunk uses choices=[]
and usage prompt3/completion2/total5/cached0. Use typed SDK StreamingChoices/Delta constructors.
New result BaseModel text:str min_length1; valid stream starts '{"text":"Hello ', next 'world\\n',
next '\\u00e9"}', then usage. sink records tuple[str,bool]. Cases:

- Success: real LlmClient.complete_prose, same-run SQL fixture, supports schema monkeypatch,
  cost monkeypatch .2. Assert resets [('',True)], suffix concat 'Hello world\né'; callback sees
  partial before release; final typed text equal; closed; stream/include_usage/timeout60/retries0;
  exactly one llm_calls row prompt3/completion2/cached0/cost.2 and signature output class.
- Repair: first stream '{"text":""}' then valid 'fixed'; one reset per attempt, two accounting
  rows, repair prompt, result fixed; two invalid outputs LlmOutputError, exactly two closed streams.
- Invalid ownership no provider/sink/accounting; output class without text no provider call.
- Empty/usage-only stream produces empty JSON validation and exactly one repair; ordinary
  ModelResponse instead of stream -> LlmCallError bug; text prefix duplicate keys changing
  text -> bug; malformed partial JSON waits, final invalid receives repair; escaped quote,
  backslash, newline and incomplete Unicode fragments emit only decoded text.
- Provider APIConnectionError midstream -> transient, closed, zero accounting; cancellation
  during release -> CancelledError/closed/zero accounting/semaphore reusable by following call.
- Semaphore limit1: two calls, blocked first prevents second provider entry; release admits
  second; no orphan task. Timeout: monkeypatch _REQUEST_TIMEOUT to0 ONLY in test (change
  timeout context to use _REQUEST_TIMEOUT, not literal), awaiting blocked fake -> transient.
- Sink PersistenceError survives unchanged, fake closed; no final text/schema result.
- Content length131073 rejects before emitting oversized text;131072 passes this guard and
  remains subject to normal final schema validation. Use a valid bounded text plus whitespace
  to exercise exact limit without bypassing the output validator.
- Nonstream complete with existing _response still streamFalse, no stream_options; structured
  output never uses sink. No validators bypassed or malformed SDK instances manufactured.

tests/test_api_runs.py fixture complete return annotation changes to object; if streamTrue
return FakeStream constructed from JSON reply split at midpoint into two chunk helper objects
plus usage; otherwise unchanged _model_response. Import these helpers from new test_llm_stream;
BaseException queue behavior stays unchanged. Existing Q&A cases now exercise streaming without
changed intended assertions; status/control/gate/fork remain nonstream. Add assertion in existing
Q&A response case that classifier request streamFalse, Q&A streamTrue; delta content joins final
QaAnswer.text for latest user reply, complete.reply_to matches user event id, only final SQL
assistant message. Do not edit previous acceptance cases otherwise.

tests/test_api_stream.py reuse _open_run_api/_create_run/_gate/_decision/FixtureRecords from
test_api_runs. New tests can directly drive stream_events for finite reads; HTTPX ASGITransport
buffers infinite streams, so SSE endpoint tests use a small local ASGI driver, no live listener.
Driver async function accepts app,path/query, optional Last-Event-ID; creates typed Scope with
type=http/asgi.spec_version2.0/http_version1.1/schemehttp/server('test',80)/client('test',1)/
methodGET/path/raw_path/query_string/headers; receive first http.request then wait a disconnect
event and return http.disconnect; send records typed messages, queues body chunks and signals
response start. create_task app(scope,receive,send), return handles; test owns cleanup: set
disconnect, await task, assert registry.requests_drained and no subscribers. No helper guesses
timing; use asyncio.Event/Queue to coordinate, with asyncio.timeout(2) around bounded waits.

- Cursor parser cases None/0/'12'; effective max(4,'2')=4/max(4,'8')=8; header ''/'-1'/'+2'/
  ' 2'/'2.0'/'²' reject422; negative query through route422. Future100 accepted.
- Frame roundtrip Event seq2/type tool.result payload candidate_id='c_a', text embedded newline;
  exactly id2/data line, model_validate_json matches original; no event field.
- Bus unit stream interleaved 6 events c_a/c_b since3 -> exact4,5,6 then live7, since100 tails
  new event8 despite lower seq. Closing ends and subscriber removed; cancellation awaiting
  anext releases subscriber; no orphan owned tasks.
- Heartbeat monkeypatch module constant0.01, read one comment before append, subscriber remains;
  subsequent append delivers seq1, no events written for heartbeat. Closing wins with queued
  replay, no stale replay after closing.
- HTTP route real guided run gated/dormant (enqueue _decision then create/_gate); since=known
  maximum and Last-Event-ID lower -> append live receives exact envelope; header higher than
  query skips replay. Assert headers/content/status; disconnect releases admitted request and
  subscriber, leaves gate/execution ownership/status unchanged. Missing run404, invalid query
  or header422 produce JSON; closing503. Each driver must finish on errors without waiting body.
- shutdown with live stream: run gated/dormant; begin shutdown, live stream exits, requests drain,
  registry.shutdown finishes while conn SELECT works and MCP entered; no task cancellation of
  execution. State awaiting_user/gate pending preserved. Context final shutdown idempotent.
- Q&A failure stream (unavailable artifact ref) emits deltas then error discard_draft, no assistant
  SQL or complete event; existing user SQL remains. User cancellation likewise discard event
  and no final assistant; release/close provider, admitted request drain intact. Use registry.message
  directly with async with registry.request for controlled cancellation, real fixture binding.
- _RunServer.handle_exit test constructs actual server without running; app.state registry set
  to real fixture registry, monkeypatch parent Server.handle_exit to record call; assert closing/
  signal before parent called. Before startup missing state allowed; repeated signal idempotent.
  main success/config failure test monkeypatch load_settings/create_app/_RunServer.run only;
  assert host/port config and0/2, no server/paid call. ConfigError fixed public text, stderr;
  exception message/cause containing `SECRET_SENTINEL` must not appear in captured output.

## Permitted checks, acceptance and stop

Implementer may mechanically format owned Python files. Run exactly focused Ruff check,
Ruff format --check and mypy against the eight owned paths above using .venv/bin/python -m.
Redirect each full output to unique /private/tmp/b14-chunk35-{ruff-check,ruff-format,mypy}.log,
report actual exits and summaries (including any initial mechanical formatting failures).
No global gates, pytest, runtime probes, package installs, source generators, server runs or
mutation/replay execution. On static failure other than mechanical import/format, stop with
diagnostics and partial diff; main resolves. No behavioral workaround by worker.

Terminal receipt: base HEAD, changed paths, SHA256 per owned file, commands/scopes/exits,
summaries/log locations, deviations, unexecuted tests/coverage and launcher/next-stage hazards.
Main reviews whole diff, then resolves corrections on same worker. Chunk36 can start only after
terminal reviewed acceptance of stage35's static evidence; B14 final global static acceptance
belongs to main once stable. Static success does not prove runtime shutdown, streamed repair,
SSE disconnect/replay, API integration or coverage. Preserve WHATS_NEXT obligations unchanged.

## Resolved correction C1 — startup error display

The implementer found that ConfigError has no public_message property. Its inherited message
can contain Pydantic validation input, so displaying it is unsafe. Ownership stays unchanged.
In main.py finish the ConfigError branch with the fixed text specified above, return2, and no
error-object interpolation. Finish the existing launcher tests in tests/test_api_stream.py:
raise ConfigError with message and nested cause both containing SECRET_SENTINEL, assert exact
stderr text plus newline, empty stdout, exit2 and no server run. Do not edit errors.py/config.py.
Resume all unfinished original scope; run only the originally permitted eight-file static checks.

## Resolved correction C2 — complete review and missing cases

First candidate on HEAD49dcd4f: Ruff check1 (18 findings), format check0 (8 files), mypy1
(9 diagnostics, 8 files). Logs: /private/tmp/b14-chunk35-{ruff-check,ruff-format,mypy}.log.
Main reviewed all production changes and both new test modules. No runtime evidence exists.
Same writer, same eight owned files; all original requirements remain unless explicitly refined
below. No worker investigation, new ownership, runtime execution, global gates or plan edits.

### Production corrections, in this order

1. api/runs.py: both diagnostic event names become `error`, as the original recipe/PLAN
   require. Keep payloads and re-raise ordering. Import contextlib.suppress alongside existing
   context-manager imports; use suppress(PersistenceError) around only the cancellation
   diagnostic append, then bare re-raise outside it. No suppression around ordinary failures.
2. llm/client.py: move the reset sink await inside asyncio.timeout(_REQUEST_TIMEOUT), before
   provider acquisition. Thus blocked reset, acquisition and consumption share the deadline.
   Extend _consume_prose's keyword-only signature with `model: str`; pass request model.
   SDK evidence: installed LiteLLM main.py stream_chunk_builder returns None for zero chunks;
   ChunkProcessor.build_base_response indexes the first choice even for usage-only chunks.
   After iteration, when no chunk has choices, construct ModelResponse with model=request
   model and a single typed Choices(index0, Message(role='assistant', content='')). Select usage
   by scanning chunks in reverse for first non-None getattr(chunk,'usage',None), bound as object;
   require Usage if present (otherwise bug 'LLM returned malformed usage metadata'); absent
   usage becomes typed Usage(prompt/completion/total=0). Pass that usage to ModelResponse.
   Otherwise use existing SDK builder. Keep response type/content equality checks. Empty output
   now goes through ordinary accounting and one JSON repair; usage-only preserves actual SDK
   usage. No estimated tokens for the truly empty attempt.
   Import Choices/Message/Usage from litellm.types.utils. No change to _content/_record_response.
3. api/stream.py: after asyncio.wait, end when closing.is_set() OR close_wait is in done.
   This makes shutdown win when its flag was set before the close-wait task was scheduled;
   never emit a heartbeat or queued event after observing closing. Existing cleanup unchanged.
4. main.py: ConfigError except need not bind/delete an unused exception; catch class only.
   Mechanically sort all affected import blocks. Remove now-unused ModelResponse import from
   test_api_runs.py. LlmOutputError import in stream tests is used by the missing repair case.

### Existing test corrections

tests/test_llm_stream.py:

- Import tests.test_llm_client as a module, assign its decorated llm_context fixture to the
  module's llm_context name. Keep fixture argument annotations and existing helper imports.
  This exposes the existing fixture without the F401/F811 import/argument clash. Do not call
  the fixture or register a plugin/new database fixture.
- chunk's usage branch explicitly returns ModelResponseStream(choices=[], model='test-model',
  usage=typed Usage(prompt3, completion2, total5, cached0)). Normal branch explicitly returns
  ModelResponseStream with one typed StreamingChoices(index0, Delta(content)). No dict splat.
- FakeStream adds `block_before: int | None = None`. When release exists, block before that
  chunk index if specified, otherwise before the last chunk; set entered immediately before
  waiting. With entered but no release, signal before first chunk. Success test sets index1,
  so entered proves 'Hello ' was already emitted but next fragment was not. Remove its sleep.
- Bound entered/task coordination with asyncio.timeout(2), including cancellation tests.
  Partial-decoder test's quote/backslash input must represent JSON escapes exactly: build
  complete valid JSON using stdlib json.dumps of text values quote-double-quote, backslash,
  newline and é, then remove only the final closing brace. Assert decoded original text.
  Keep separate incomplete Unicode case (JSON text containing backslash-u00, no closing
  quote) returning None and non-text root case returning None. Do not change parser behavior
  to satisfy an incorrectly escaped Python literal.

tests/test_api_stream.py:

- Frame has exactly ONE newline-prefixed data line; fix count assertion to1.
- Three direct stream tests use conftest's run_paths:RunPaths and EventBus(run_paths), rather
  than unrelated API/gate fixture events. Replay test appends six tool.result events with
  alternating c_a/c_b candidate_id; since3 gives4,5,6. Start pending read, bounded wait until
  subscriber registered, then append7; result7. Close and assert no subscribers. New since100
  stream: start read, bounded wait for subscriber BEFORE append8; result8, close and empty.
- Cancellation uses a fresh EMPTY bus, ensuring pending read is blocked; bounded subscriber
  wait, cancel/read CancelledError, close and no subscribers. Closing test uses a populated
  bus: read first replay event, set closing, resume and expect StopAsyncIteration rather than
  second queued event; no subscribers. Also closing already set yields no registration.
- Heartbeat uses fresh empty bus; comment arrives while subscriber remains, replay stays(),
  append seq1 then read frame1, close and empty. No SQL/run work in these three tests.
- _drive_request creates a nested typed async invoke()->None which awaits app(scope,receive,
  send), then creates asyncio.Task[None] from invoke(). This converts ASGI Awaitable into a
  coroutine without cast/type ignore. Keep returned handles.
- Existing live-route test runs both cursor combinations (query latest/header latest-1 and
  query0/header latest). For each: snapshot SQL gate/run and execution_tasks beforehand;
  in try/finally, read start, assert status200/content-type starts text/event-stream and
  existing cache headers; bounded wait subscriber, append live, assert exact first body.
  Finally set disconnect and await task with timeout2 even if an assertion fails. Assert no
  subscribers, admitted requests drained, identical pending gate/run status/task ownership.
- Replace direct-generator shutdown case with _drive_request of actual live route after a
  dormant guided gate. Read start, await subscriber, call begin_shutdown and then await real
  HTTP task termination and registry.shutdown. Assert request drain/subscriber cleanup,
  SELECT1 fetchone index0 equals1 (sqlite.Row is not tuple), _mcp_session(api) is api.session,
  SQL run still awaiting_user and same pending gate, execution_tasks unchanged. Finally
  disconnect/await driver even on failure. Repeat shutdown once for idempotence; no server.
- Signal-hook parent stub closes over typed api.registry; never dereference config.app's
  ASGI/string union. Record sig/closing/signal from that registry, assert both repeated calls.
  Add separate before-startup FastAPI without state.run_registry: parent stub records signal,
  no registry required, handle_exit forwards once. No startup/listener.
- Digit-limit parser case uses a decimal header length sys.get_int_max_str_digits()+1 (skip
  only if interpreter limit0); expect same422. This tests int conversion, not provider work.

### Missing LLM tests — exact actions and assertions

Use existing llm_context, typed StreamResult, FakeStream/chunk/_empty_sink/_rows and settings.
Each provider is injected into real LlmClient; schema-support monkeypatch remains offline.
Use async timeout2 around synchronization and cancel/gather pending tasks in test finally.

1. Wrong run ownership: call complete_prose with valid execution_id but run_id='other-run'.
   Expect PersistenceError before provider/sink; provider count0, sink list[], rows[].
2. Two invalid replies: two streams containing text='' and usage. Expect LlmOutputError;
   provider count2, reset count2, rows2, both closed, repair prompt present on second call.
3. Parametrize first attempt as empty tuple, usage-only tuple, or malformed JSON content
   '{"text": invalid}'. Follow with valid fixed+usage. Assert result fixed, two resets/two
   rows/both streams closed; first empty row tokens all0, usage-only row prompt3/completion2.
4. Ordinary ModelResponse returned for stream request: expect bug LlmCallError malformed
   stream, provider count1, no semantic repair, zero-token accounting row1.
5. Reasoning separation: typed StreamingChoices Delta(reasoning_content='SECRET_REASONING',
   content=None), then valid text='visible' JSON and usage. Sink suffixes equal visible;
   no SECRET_REASONING in sink, typed final visible, one row/closed.
6. Semaphore overlap: limit1 client and two calls. First stream blocks before index1 after
   partial text; entered proves first owns slot. Start second task, yield asyncio.sleep(0)
   once, assert provider call count1 and second pending. Release first; gather both, results
   first/second in order, provider count2, streams closed, rows2. Finally release/cancel/gather
   tasks to avoid hangs on failed assertions. Existing cancellation test proves slot reuse.
7. Timeout test existing blocked provider stream still asserts transient/closed/zero row;
   add reset-blocked test: sink reset signals entered then waits an unset event, timeout set
   to0.01; expect transient and provider count0, zero row1. Bounded timeout includes reset.

### Missing API tests — exact setup, failures and cancellation

Add in test_api_stream.py; import deque, ControlText/QaAnswer, InterruptClass, ArtifactRef,
RunConfig, list_messages/get_run/list_gates and the existing _decision/_mcp_session helpers.
No changes to fixture ownership beyond existing test_api_runs streaming return support.

1. Unavailable citation: FixtureRecords replies deque of _decision(), classifier qa, and
   QaAnswer(text='Draft answer.', references=(ArtifactRef('a_0123456789abcdef'),)) JSON.
   Create/gate dormant run, record task map/gate, POST message 'what evidence?'. Expect502 and
   {'detail':'LLM output failed'} through existing LlmOutputError mapping. Replay: non-reset delta suffixes join Draft answer.;
   terminal error payload message='Assistant response failed.', discard_draft=True, reply_to
   equals persisted user id. No assistant complete and only one user SQL message/no assistant.
   SQL gate/run and execution tasks unchanged. No raw failure/provider/citation input in error.
2. Cancellation: records replies _decision(), qa classifier. After create/gate, retain original
   api.registry.resources.llm._acompletion and monkeypatch this instance boundary with async
   provider(**kwargs:object)->object: nonstream delegates to original; stream returns blocked
   FakeStream with first '{"text":"partial', second ' answer","references":[]}' and usage,
   block_before1, entered/release Events. Start typed local coroutine which enters
   api.registry.request() then awaits registry.message(run_id,ControlText(text='question')).
   Await entered under timeout2, then cancel task and await CancelledError. Assert closed,
   partial delta emitted; error message cancelled/discard/real reply_to; no complete/assistant
   SQL; user persists; registry.requests_drained set; gate/status/task ownership unchanged.
   Joined llm_calls row with signature QaAnswer has zero prompt/completion/cached counts.
   Finally release and cancel/gather unfinished task. No transport-specific cancellation guess.

Run exactly original eight-file Ruff check/format check/scoped mypy, new unique logs
/private/tmp/b14-chunk35-c2-{ruff-check,ruff-format,mypy}.log. Mechanical owned-file formatting
and import ordering permitted. First unexpected failure: stop and report; do not omit specified
tests. Terminal receipt includes original failed checks and new genuine exits/file hashes,
all remaining omitted cases (must be none for completion), and runtime/coverage gap.

### C2a — persistence failure accounting

Main found one remaining mismatch to the original failed-attempt accounting requirement.
In LlmClient._request's explicit PersistenceError branch, attempt existing _record with the
same conn/run/execution/output signature/model and elapsed duration used by other failed
attempts. If this secondary accounting raises PersistenceError, log only the fixed warning
'Could not record failed prose request'; then re-raise the ORIGINAL sink/persistence exception.
No retry, no cost or tokens guessed, no exception contents logged. This preserves the original
error even when SQL also fails, while an event-file failure with usable SQL records the attempt.
Extend existing test_sink_persistence_error_propagates_and_closes: assert exactly one accounting
row with StreamResult signature and prompt/completion/cached all0. No other new cases required.
Same ownership and C2 checks/logs; include this correction before terminal handoff.

### C2b — check wrapper recovery

Writer's zsh wrapper failed assigning reserved `status`; wrapper exit1 gives no trusted
individual gate exit. No product change authorized by this recovery. Rerun the same three
eight-file checks with direct exec_command calls, each command redirected to its own fresh
/private/tmp/b14-chunk35-c2b-{ruff-check,ruff-format,mypy}.log. Do not assign shell variables;
report each exec_command exit_code and read each full log. A command exit1 is a real failure,
not a wrapper exit to infer away. No runtime/global checks. Preserve current source candidate.

### C3 — SDK import and repeated signal drain

Mypy identified one test-only import mismatch: litellm.types.utils imports OpenAI's
PromptTokensDetails without an explicit re-export. In tests/test_llm_stream.py import
PromptTokensDetails from openai.types.completion_usage; retain typed construction and all
assertions. No dependency change.

Read-only review also confirmed uvicorn.Server.handle_exit sets force_exit on a repeated
SIGINT, bypassing lifespan cleanup. In _RunServer.handle_exit keep begin_shutdown first,
then return without calling the parent when self.should_exit is already true; otherwise
call the parent once. This applies before registry publication as well. Repeated signals
must never bypass SQL/MCP/task drain. Update the existing parent-stub signal test to set
server.should_exit=True in that stub and expect exactly one forwarded signal across two
calls; registry closing/event remain true. Add a real-parent test with a fresh FastAPI app
and typed fixture settings: call SIGINT twice then SIGTERM; assert should_exit true,
force_exit false and captured signal list contains only the first SIGINT. No server starts.

Ownership stays the original eight paths. Run the same three eight-file focused static
checks, direct exec_command with fresh /private/tmp/b14-chunk35-c3-{ruff-check,ruff-format,mypy}.log.
Owned-file import/format mechanical correction allowed. Stop on any unexpected finding;
no runtime tests or global checks. Report actual exits/full logs and new file hashes.

### C4 — reviewed test setup corrections

Full review identified two deterministic test contradictions; no production behavior change.
In test_stream_closure_and_cancellation_release_subscriber, reopening EventBus(run_paths)
replays the same two durable events. Set the cancelled stream's since to the last replayed
event's seq (derive from fresh.replay()), so anext really blocks on the empty live tail
before cancellation. Keep subscriber registration wait, CancelledError and cleanup assertions.
Do not truncate files or bypass EventBus guarantees.

In test_unavailable_citation_discards_streamed_draft, a prose attempt deliberately starts
with one empty reset delta. Assert first delta has text='' and reset=True, all remaining
deltas reset=False, and join only non-reset suffixes to 'Draft answer.'. Keep citation/error,
same reply_to, no final SQL assistant and unchanged gate/task assertions. Cancellation test
also asserts initial empty reset and joins only non-reset suffixes to 'partial'.

Apply after C3, same eight-file ownership. The final C4 focused trio uses fresh
/private/tmp/b14-chunk35-c4-{ruff-check,ruff-format,mypy}.log; no runtime/global execution.
Stop/report unexpected findings; receipt covers both C3 and C4.
