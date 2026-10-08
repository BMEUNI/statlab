---
status: completed
dependencies: [43]
completion_date: 2026-10-08
implementation_notes: "chat setting changes fork with a new contract version; offline stand-in routes chat commands; fork note and clear 409 text; make check 3125 passed, 100%; npm 80/80"
---

# Chunk 44 — change run settings from the chat

User ask 2026-10-08 (chunk 42 feedback, verbatim): "I cant say ... go back to profiling or
something like that or set a param to this and that, basically I want it to use the chat more
often". Main's audit after chunk 43: "go back to <step>" works (change → `ForkService.handle`
→ `RewindIntent(target)` → fork), but "set the test size to 0.3" outside a pending gate is
classified `change` and `RewindIntent` carries only `target`; the setting is silently dropped.
The offline stand-in LLM classifies every message as qa/status, so none of this is testable
offline. User chose: fix the gap, make offline testable, keep the chat intro honest.

Goal: a chat message that asks to change a run setting creates a new contract version with the
change and forks a new run from the earliest step that uses that setting (or from the run's
current step if it has not got there yet). Non-goals: no change to gate answers or gate edits,
no new endpoint, no frontend change (the chat already shows the reply and opens `new_run_id`),
no editing of the dataset, no partial reuse beyond what `project_frontier` already does.

Relevant decisions: D27 (domain node names only in `domains/`), D32 (fork lineage and prior
test exposure: unchanged, `project_frontier`/fork already handle it), D13/D18 untouched.

## Stage 44a — backend

### Owned files

`contracts/statlab_contracts/gates.py`, `backend/statlab/graph/rewind.py`,
`backend/statlab/graph/router.py`, `backend/statlab/domains/tabular.py`,
`backend/statlab/api/runs.py`, `integration/offline_llm.py`; tests
`tests/test_control_gates.py`, `tests/test_control_rewind.py`, `tests/test_api_runs.py`.

### 1. `RewindIntent` (`contracts/statlab_contracts/gates.py`)

- `target: str | None = Field(default=None, description="The step to go back to, only when
  the user names one.")`
- New `edits: dict[str, JsonValue] = Field(default_factory=dict, description="Run settings the
  user explicitly asked to change: dotted paths into the run contract mapped to the new value,
  for example {\"split.test_size\": 0.3}.")` (import `JsonValue` from pydantic if missing).
- Existing `_nonblank` field validator: signature becomes `(cls, value: str | None) -> str |
  None`; `None` passes through unchanged; blank strings still raise.
- New `@model_validator(mode="after") def _target_or_edits(self) -> RewindIntent`: raise
  `ValueError("name a step to go back to or a setting to change")` when `target is None and
  not edits`.
- Tests in `tests/test_control_gates.py` next to `test_rewind_intent_rejects_blank_target`:
  `RewindIntent.model_validate({})` raises `ValidationError`; `RewindIntent(edits={"split.test_size":
  0.3}).target is None`; existing blank-target test unchanged.

### 2. `SETTING_OWNERS` (`backend/statlab/domains/tabular.py`)

After `TABULAR_DOMAIN`: `SETTING_OWNERS: dict[str, str]` mapping each top-level contract
section to the first tabular node that reads it: `target`, `dataset`, `features` → `"intake"`;
`objective`, `notes` → `"problem_definition"`; `split` → `"data_splitting"`;
`preprocessing`, `modelling`, `constraints` → `"experiment_design"`. One-line comment: the earliest
consumer, so a fork from there re-runs everything the setting can affect. `version` is
deliberately absent (not user-editable).

### 3. `ForkService` (`backend/statlab/graph/rewind.py`)

- Module type alias next to the others:
  `type SettingsEditor = Callable[[dict[str, JsonValue]], tuple[str, ContractRow]]`
  (import `ContractRow` from `statlab.persistence.models`, `ValidationError` from pydantic).
  Contract: returns `(owner_node_name, persisted_contract_row)`; raises `ValueError` (pydantic
  `ValidationError` included) for a change that cannot be applied.
- `fork(self, target, *, request_id=None, contract: ContractRow | None = None)`:
  - `create_run(... contract_id=contract.id if contract is not None else parent_row.contract_id, ...)`.
  - In the immutable-input copy loop: for `"contract.snapshot.json"` when `contract is not None`
    write `contract.contract_json.encode("utf-8")` to the child; every other case unchanged
    (copy when the parent file exists). The parent presence pre-check stays as is.
- `handle(self, message, classified, context, *, request_id=None, edit_settings: SettingsEditor
  | None = None)`:
  1. Binding check unchanged.
  2. LLM call unchanged except the system content becomes:
     `"Parse the user's explicit rewind target and/or run setting changes. Declared nodes: "
     + ", ".join(names) + ". Put setting changes in edits as dotted paths into the current run
     contract with the new value, for example {\"split.test_size\": 0.3} or
     {\"modelling.tuning.cv_folds\": 10}. Only include targets and settings the user explicitly
     asked for; never invent values. Current contract: " + json.dumps(context.contract.model_dump(mode="json"), sort_keys=True)`.
  3. If `intent.edits` is empty: existing behaviour unchanged, calling
     `self.fork(intent.target or "", request_id=request_id)` (the model validator guarantees a
     target here; `""` would be rejected as undeclared). Same reply text as today.
  4. Else:
     - `edit_settings is None` → `raise EngineError("setting changes are not configured")`.
     - `owner, contract = edit_settings(dict(intent.edits))`; on `ValueError as exc` return
       `ControlResponse(category="change", text="I could not change that setting, so nothing
       was changed: " + details)` with no fork, where `details` is, for a `ValidationError`,
       `"; ".join(f"{'.'.join(str(part) for part in error['loc']) or 'contract'}: {error['msg']}"
       for error in exc.errors())`, otherwise `str(exc)`; cut `details` to 1,500 characters.
     - `names = tuple(entry.name for entry in context.state.spine)`. If `owner` or a non-None
       `intent.target` is not in `names` → `raise EngineError("rewind target is not declared")`.
     - `frontier = min(context.state.cursor, len(names) - 1)`;
       `indexes = [min(names.index(owner), frontier)]`, append `names.index(intent.target)` when
       it is not None; `target = names[min(indexes)]`.
     - `fork = await self.fork(target, request_id=request_id, contract=contract)`.
     - Reply `ControlResponse(category="change", text=f"Changed {changes}. A new run starts
       from {target.replace('_', ' ')}; earlier steps are reused.", new_run_id=fork.new_run_id)`
       with `changes = ", ".join(f"{path} to {json.dumps(value)}" for path, value in
       sorted(intent.edits.items()))`.

### 4. Classifier prompt (`backend/statlab/graph/router.py` `classify`)

Replace `"decision = gate answer; change = change/rewind request; "` with
`"decision = answer to the pending gate's proposal (approve, reject or edit a proposed field); "
"change = rewind to a step, or change a run setting such as test size, folds, metric or "
"candidate models; "`. Rest unchanged.

### 5. Editor (`backend/statlab/api/runs.py`)

- Import `SETTING_OWNERS` from `statlab.domains.tabular` (next to `TABULAR_DOMAIN`),
  `missing_setup_fields` from `statlab.api.projects`, `ContractRow` from
  `statlab.persistence.models`, `DataContract` if not imported, `JsonValue` from pydantic.
- New method `_edit_settings(self, binding: RunBinding, project_id: str, edits: dict[str,
  JsonValue]) -> tuple[str, ContractRow]`:
  1. `data: dict[str, JsonValue] = binding.runtime.contract.model_dump(mode="json",
     exclude_unset=True)`.
  2. For each `path, value` in `edits.items()`: `keys = path.split(".")`; if `keys[0] not in
     SETTING_OWNERS` → `raise ValueError(f"{path} cannot be changed from the chat")`; walk
     `node = data`; for each key in `keys[:-1]`: `child = node.get(key)`; if not a `dict`, set
     `child = {}` and `node[key] = child`; `node = child`; finally `node[keys[-1]] = value`.
  3. `contract = DataContract.model_validate(data)` (`ValidationError` propagates).
  4. `missing = missing_setup_fields(contract)`; if non-empty → `raise ValueError("these
     settings would become empty: " + ", ".join(missing))`.
  5. `names = [node.name for node in TABULAR_DOMAIN.nodes]`;
     `owner = min((SETTING_OWNERS[path.split(".")[0]] for path in edits), key=names.index)`.
  6. `return owner, self.projects.persist_contract(project_id, contract)`.
- In `message()`, the `change` closure passes
  `edit_settings=lambda edits: self._edit_settings(binding, run_row.project_id, edits)` to
  `binding.forks.handle(...)`.

### 6. Offline stand-in (`integration/offline_llm.py`)

- Import `TABULAR_DOMAIN` from `statlab.domains.tabular`.
- `_SET = re.compile(r"\bset (?:the )?([a-z ]+?) to (\d+(?:\.\d+)?)")`,
  `_FOLDS = re.compile(r"\buse (\d+) (?:cv )?folds\b")`,
  `_SETTING_PHRASES = {"test size": "split.test_size", "validation size": "split.valid_size",
  "cv folds": "modelling.tuning.cv_folds", "folds": "modelling.tuning.cv_folds"}`.
- `def _number(text: str) -> JsonValue`: `float(text)` when it contains `"."`, else `int(text)`.
- `def _interrupt(user_text: str) -> dict[str, JsonValue]` (replaces the inline
  `InterruptClass` branch): `t = user_text.lower()`; `\bpause\b` → lifecycle `pause`;
  `\bresume\b` → lifecycle `resume`; `\b(stop|cancel)\b` → lifecycle `cancel`; else if
  `"go back" in t or "rewind" in t or "redo" in t or _SET.search(t) or _FOLDS.search(t)` →
  `{"category": "change", "lifecycle_action": None}`; else the existing status/qa rule.
- `def _rewind(user_text: str) -> dict[str, JsonValue]`: `t = user_text.lower()`; `edits` from
  every `_SET` match whose phrase (stripped) is in `_SETTING_PHRASES`, plus `_FOLDS` →
  `modelling.tuning.cv_folds`; `target` = first `TABULAR_DOMAIN.nodes` name whose `name` or
  `name.replace("_", " ")` occurs in `t`, else `None`. Return `{"target": target, "edits": edits}`.
- In `_answer`: `if title == "RewindIntent": return _rewind(user_text)`; the `InterruptClass`
  branch returns `_interrupt(user_text)`.

### 7. Tests

`tests/test_control_rewind.py` (use `dataclasses.replace(context(engine_world.conn,
minimal_contract, client), state=await lifecycle.state())` for a bound context; nodes
`(node("prefix"), node("target"), node("tail"))`; `lifecycle.start()` completes the run;
`immutable_inputs(engine_world)`; a new contract row via `create_contract(engine_world.conn,
project_id=require_run(engine_world.conn, engine_world.paths.run_id).project_id,
path=engine_world.paths.root / "edited.json", contract=DataContract(target=TargetSpec(column="edited")))`):
1. `test_handle_setting_edits_fork_with_new_contract`, parametrized
   `(intent_target, owner, expected) in [("target", "tail", "target"), (None, "target", "target")]`:
   client `FakeClient(RewindIntent(target=intent_target, edits={"split.test_size": 0.3}))`;
   editor appends its argument to `seen` and returns `(owner, row)`; call
   `handle("set the test size to 0.3", InterruptClass(category="change"), ctx,
   request_id="req-edit", edit_settings=editor)`. Assert `seen == [{"split.test_size": 0.3}]`;
   `response.new_run_id` is a str; the child `RunRow.contract_id == row.id`; the child's
   `RunPaths(engine_world.paths.data_dir, engine_world.paths.slug, child).contract_snapshot_json`
   bytes equal `row.contract_json.encode("utf-8")`; child config `fork["target"] == expected`;
   `response.text == f"Changed split.test_size to 0.3. A new run starts from {expected}; earlier steps are reused."`;
   the LLM system message contains `"Current contract: "`.
2. `test_handle_setting_edit_rejected_without_fork`, parametrized over two editors: one calling
   `DataContract.model_validate({"target": {"column": "a"}, "split": {"test_size": 1.5}})`
   (expect text starting `"I could not change that setting, so nothing was changed: split.test_size"`),
   one raising `ValueError("version cannot be changed from the chat")` (expect text ending with
   that message). Assert `new_run_id is None` and no run row has `forked_from_run_id ==
   engine_world.paths.run_id`.
3. `test_handle_setting_edits_guards`: `RewindIntent(edits={"a": 1})` with no editor →
   `EngineError` matching `"setting changes are not configured"`; `RewindIntent(target="invented",
   edits={"a": 1})` with an editor returning `("target", row)` → `EngineError` matching
   `"rewind target is not declared"`.

`tests/test_api_runs.py`:
4. `test_message_change_with_setting_edits`: like the `change` case of
   `test_message_decision_and_change`; replies `_decision()`, then
   `InterruptClass(category="change")`, `RewindIntent(edits={"split.test_size": 0.3,
   "modelling.tuning.cv_folds": 3})`, `_decision("Child")`; message text `"set the test size to
   0.3 and use 3 folds"`. Assert 200, category `change`, `new_run_id` str; `await _gate(api,
   child)`; `child_row.contract_id != parent_row.contract_id`; `get_contract(api.conn,
   child_row.contract_id).contract()` has `split.test_size == 0.3`,
   `modelling.tuning.cv_folds == 3` and otherwise equals the parent contract (compare
   `model_dump` after setting those two fields on the parent dump); child config
   `fork["target"] == TABULAR_DOMAIN.nodes[0].name` (parent waits at the first node, so the
   `data_splitting` owner is clamped to it); assistant message text contains
   `"split.test_size to 0.3"`; parent status `superseded`.
5. `test_message_change_rejects_invalid_setting`, parametrized over
   `({"split.test_size": 1.5}, "split.test_size")`, `({"version": 1}, "version cannot be
   changed from the chat")`, `({"split.strategy": None}, "these settings would become empty:
   split.strategy")`: replies `_decision()`, `InterruptClass(category="change")`,
   `RewindIntent(edits=...)`; assert 200, category `change`, `new_run_id` null, text starts
   with `"I could not change that setting"` and contains the expected fragment; no child run;
   the project still has exactly one contract row (`list_contracts` or a SQL count).
6. `test_setting_owners_cover_contract_sections`: `set(SETTING_OWNERS) ==
   set(DataContract.model_fields) - {"version"}` and every value is a
   `TABULAR_DOMAIN.nodes` name.

### Checks (implementer)

- `make test-fast TESTS="tests/test_control_gates.py tests/test_control_rewind.py tests/test_api_runs.py tests/test_control_router.py tests/test_control_qa_status.py"`
- Ruff check + format --check and mypy on the six owned source files and three test files
  (mypy only on the files under `backend`, `contracts`, `tests`; Ruff on all including
  `integration/offline_llm.py`).

Stop and report on any other failing test or a missing decision.

## Stage 44b — main acceptance (browser, offline)

Restart the offline stack; guided run on `churn.csv`. At the first gate send "set the test size
to 0.3" → new run opens; its run contract (Run details / report) shows 0.3. Let it reach data
profiling, send "go back to data profiling" → new run from data profiling. Send "pause", then
"resume". Then `make check` and commit.

### 44a Addendum A — test context node

The `test_control_rewind.py` edit tests failed with "control current node is outside captured
spine": `context()` defaults `current_node="alpha"`. Build the context with
`replace(context(engine_world.conn, minimal_contract, client, current_node=None), state=await
lifecycle.state())` (the parent run is complete, so there is no current node). Keeping
`min_length=1` on `RewindIntent.target` is accepted. Re-run the spec's pytest selector.

### 44a handoff (2026-10-08)

Implemented with Addendum A (implementer). Focused pytest exit 0; Ruff, format and mypy exit 0.
Main browser test (offline stack, guided `churn.csv`):
- "set the test size to 0.3" at the intake gate created contract v2 (`split.test_size` 0.3) and
  child `r_9cf66389c87695c5` from intake; the parent became superseded.
- "go back to data profiling" at the splitting gate created a child from data_profiling that
  keeps contract v2.
- "pause" while a decision waits returned 409, which the chat shows as "Run transition
  rejected." (unclear).
- The new run's chat never shows what changed: the confirmation stays in the parent's chat.

### 44 Addendum B — say what happened

1. `backend/statlab/graph/rewind.py` `fork`: new keyword `edits: dict[str, JsonValue] | None =
   None`. Right after `projection = project_frontier(source, target)`, when `edits is not None`:
   `projection = projection.model_copy(update={"fork_metadata": {**projection.fork_metadata,
   "edits": edits}})`, so the config `fork` entry, the child state and the `run.forked` event
   carry the edits. `_handle_edits` passes `edits=dict(intent.edits)`.
   Tests: `test_handle_setting_edits_fork_with_new_contract` additionally asserts child config
   `fork["edits"] == {"split.test_size": 0.3}`; `test_message_change_with_setting_edits`
   asserts `child_config.fork["edits"] == {"split.test_size": 0.3, "modelling.tuning.cv_folds": 3}`.
2. `frontend/src/run/model.ts`: `export function forkNote(run: RunSummary): string | null`.
   `null` when `run.forked_from_run_id` is null, or `config_json` does not parse (try/catch) to an
   object whose `fork` is an object with a string `target`. Otherwise
   `` `This run continues from run ${parent} and starts again at ${nodeLabel(target)}; earlier steps are reused.` ``
   plus, when `fork.edits` is a non-empty object,
   `` ` Changed settings: ${entries.map(([key, value]) => `${key} = ${JSON.stringify(value)}`).join(', ')}.` ``
   (entries in object order).
3. `frontend/src/chat/RunChat.tsx`: new prop `forkNote: string | null`; when not null push
   `{ id: 'fork-note', role: 'assistant', text: forkNote }` right after the intro message.
   `RunPage.tsx` passes `forkNote={forkNote(data.run)}`.
   The chat `mutation` `onError`: when `error instanceof ApiError && error.status === 409`, the
   note text is `` `I can't do that right now because the run is ${STATUS_LABELS[status].toLowerCase()}. A run that waits for your decision can't be paused; answer the decision first.` ``
   instead of `errorText(error)` (import `ApiError` from `../api/client.ts`, `STATUS_LABELS`
   from `../run/model.ts`). Other errors unchanged.
4. `frontend/tests/run-model.test.ts`: `forkNote` returns null for a run with
   `forked_from_run_id: null`; null for `config_json: '{'`; the plain sentence for
   `{"fork":{"target":"data_profiling"}}`; the sentence plus `" Changed settings: split.test_size = 0.3."`
   for `{"fork":{"target":"intake","edits":{"split.test_size":0.3}}}` (build `RunSummary`
   literals with every field).
Checks: `make test-fast TESTS="tests/test_control_rewind.py tests/test_api_runs.py"`; Ruff,
format --check and mypy on `backend/statlab/graph/rewind.py` and the two test files; from
`frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

### 44 Addendum C — coverage

Main `make check`: all tests pass, coverage 99.98 %: `backend/statlab/api/runs.py:653-654`
(the "intermediate key missing or not an object" branch in `_edit_settings`) is never reached,
because the fixture contract already carries every section edited by the tests. Add one parameter
to `test_message_change_rejects_invalid_setting`: `({"split.seed.extra": 1}, "split.seed")`
(`seed` is an int, so the walk replaces it with an object, and validation then rejects the
contract). Check: `make test-fast TESTS="tests/test_api_runs.py"`; Ruff, format --check and mypy
on `tests/test_api_runs.py`.

## Acceptance receipt (2026-10-08)

Candidate: `c0cc6de` plus the chunk 44 working-tree diff (files staged in the chunk 44 commit;
the user's own `.gitignore` edit is excluded).
- `make check`: exit 0; 3,125 passed, 1 designed skip; coverage 100 % (12,043 statements,
  3,472 branches); mypy "Success: no issues found in 195 source files"; Ruff "All checks
  passed!". Log: session scratchpad `check44b.log` (temporary). The first run was exit 2 on
  coverage only (99.98 %, `runs.py:653-654`), fixed by Addendum C.
- Frontend: `npm test` exit 0 (80/80), typecheck and build exit 0.
- Browser (offline stack): "set the test size to 0.3" at a pending gate made contract v2 and a
  child run from intake; "go back to data profiling" made a child from data_profiling keeping
  v2; "set the test size to 0.3 and use 3 folds" shows "This run continues from run … Changed
  settings: split.test_size = 0.3, modelling.tuning.cv_folds = 3." in the new run's chat;
  "pause" at a pending gate explains why it cannot pause.
- Not covered: the real model's classification of these phrases (needs a paid run).
