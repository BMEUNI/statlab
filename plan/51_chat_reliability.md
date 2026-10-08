---
status: completed
dependencies: [50]
completion_date: 2026-10-08
implementation_notes: "chat retries transient provider errors; friendlier noise reply; no duplicate 502/503 note; attempt errors show their message; make check 100%; npm 91/91"
---

# Chunk 51 — the run chat answers reliably and shows errors cleanly

User report 2026-10-08, run `r_ca4d7d9637afd45e` (events 237-243, `llm_calls` 19:36:57):

1. "can you compare these models that you trained here?" got "Assistant response failed."
   `QaAnswer` recorded 0 tokens after 1,025 ms: a transient provider error (`LlmCallError`,
   `transient`). Node calls retry transient errors through `recover_call`; the chat endpoint
   builds its `ControlContext` with `recovery=None` (`backend/statlab/api/runs.py` ~738), so
   one provider blip fails the question.
2. "hi" and "hello" were classified `noise` and got the fixed reply "Acknowledged."
   (`backend/statlab/graph/router.py:84`).
3. The failed send returned HTTP 503. `RunChat` `onError` adds a fault note to `extra`, which
   always renders after every message and never clears, so "Backend is unavailable." sat under
   later answers and stayed after the run finished. The backend had already emitted its own
   `error` event for the same failure.
4. The step-details attempt list prints `execution.error` raw:
   `{"error_class":"data","message":"Fit budget exhausted","field_errors":null}`
   (`frontend/src/run/NodeInspector.tsx:116`).

Also observed, not fixed here: each Q&A prompt was ~151k tokens (the whole state summary).

Decisions:
1. The chat endpoint retries transient provider failures exactly like nodes: `recover_call`
   (transient budget 3 attempts, backoff 1 s then 2 s). The prose sink already resets at the
   start of every attempt. `LlmOutputError` is never retried (`recover_call` re-raises it).
   The failure event text becomes "I could not get an answer from the model. Please ask again."
2. Noise reply: `I'm here. Ask me anything about this run, for example "compare the models".`
3. Frontend: a failed send with HTTP 502 or 503 adds no fault note (the backend's `error`
   event already explains it in place); any successful send clears `extra`.
4. Frontend: an attempt error is parsed; when it is JSON with a string `message`, show the
   message; otherwise show the text as is.

## Stage 51a

Owned: `backend/statlab/api/runs.py`, `backend/statlab/graph/router.py`,
`tests/test_api_runs.py`, `tests/test_api_stream.py`, `tests/test_control_router.py`,
`frontend/src/run/model.ts`, `frontend/src/run/NodeInspector.tsx`,
`frontend/src/chat/RunChat.tsx`, `frontend/tests/run-model.test.ts`. Preserve other edits.

1. `runs.py`: import `recover_call` from `statlab.graph.recovery`; add `Awaitable` to the
   existing `collections.abc` import. New module-level function (place it just above the
   class that owns `message`):
   `async def _retrying[T](call: Callable[[], Awaitable[T]]) -> T:` with one-line docstring
   "Chat answers retry transient provider failures the way node calls do." and body
   `return await recover_call(call)`. In `message`'s `ControlContext(...)`, `recovery=None`
   becomes `recovery=_retrying`. In the `except LlmCallError, LlmOutputError:` block the event
   `"message"` becomes `"I could not get an answer from the model. Please ask again."`.
   Nothing else changes.
2. `router.py:84`: the noise text becomes
   `'I\'m here. Ask me anything about this run, for example "compare the models".'`
   (write it as a double-quoted Python string with escaped inner quotes, or a single-quoted one
   with an escaped apostrophe; either is fine for Ruff's quote rule, use whatever Ruff format
   keeps).
3. Tests (Python):
   - `tests/test_control_router.py:161` and `tests/test_api_runs.py:918`: expect the new noise
     text.
   - `tests/test_api_stream.py:491`, `tests/test_api_runs.py:1024` and `:1073`: expect the new
     failure message.
   - `tests/test_api_runs.py` `test_messages_status_noise_qa_and_provider_failure`, the
     `tmp_path / "transient"` block: add at the start of the block
     `monkeypatch.setattr(runs_api, "recover_call", partial(recover_call, sleep=_no_sleep))`
     where `import statlab.api.runs as runs_api`, `from functools import partial`,
     `from statlab.graph.recovery import recover_call` are module imports and
     `async def _no_sleep(delay: float) -> None: return None` is a module-level helper.
     Replies become `deque([_decision(), qa, provider_error, provider_error, provider_error])`;
     expect `len(api.records.requests) == 5` and `len(qa_rows) == 3`, every QA row
     zero tokens/cost (`all(row[3] == row[4] == row[5] == 0 for row in qa_rows)`). The rest of
     the block is unchanged (still 503, one user message, events `message.user`, `error`).
   - New block in the same test, after the transient block, `tmp_path / "recovered"`: same
     monkeypatch; replies `deque([_decision(), qa, provider_error, answer])` (reuse the
     test's `answer`). Post `{"text": "question"}`; assert 200 and the JSON `text` is
     `"Evidence unavailable."`; `len(api.records.requests) == 4`; replayed events filtered to
     `("message.user", "message.assistant.complete", "error")` have types
     `("message.user", "message.assistant.complete")`.
4. `model.ts`: new exported `failureText(raw: string): string`: `JSON.parse` inside
   try/catch; when the result `isJsonObject` and its `message` is a non-empty string, return
   that `message`; in every other case (parse error, other shapes) return `raw`.
5. `NodeInspector.tsx:116`: render `{failureText(execution.error)}` (import from
   `./model.ts`, merged into the existing import).
6. `RunChat.tsx` mutation: `onSuccess` calls `setExtra([])` first. `onError`: when
   `error instanceof ApiError && (error.status === 502 || error.status === 503)`, return
   without adding a note; otherwise unchanged.
7. `frontend/tests/run-model.test.ts`: test `failureText reads the failure message`:
   `failureText('{"error_class":"data","message":"Fit budget exhausted","field_errors":null}')`
   is `'Fit budget exhausted'`; `failureText('plain text')` is `'plain text'`;
   `failureText('{"message":""}')` is `'{"message":""}'`; `failureText('[1]')` is `'[1]'`.

Checks:
- `make test-fast TESTS="tests/test_api_runs.py tests/test_api_stream.py tests/test_control_router.py"`
- Ruff check, Ruff format --check, mypy on the five Python files.
- From `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: review, `make check`, commit.

## Acceptance receipt (2026-10-08)

Implementer: focused pytest (api_runs, api_stream, control_router) exit 0; Ruff check/format
and mypy on the five Python files exit 0; npm test 91/91, typecheck 0, build 0. Long string
literals split for E501 (same values). Main reviewed the diff: `_retrying` also covers the
classifier and other chat-path LLM calls, which are idempotent model requests. Main
`make check` exit 0: coverage 100 %, mypy 195 files, Ruff and format clean (log
`check51.log`, temporary). Open: Q&A prompt size (~151k tokens per question).
