"""Run creation, durable conversation, controls and caller-owned execution tasks."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, HTTPException, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)
from statlab_contracts.contract import DataContract
from statlab_contracts.gates import GateAction, GateAnswer
from statlab_contracts.layout import RunPaths

from statlab.api.projects import (
    APIResources,
    ContractCheck,
    ProjectsService,
    missing_setup_fields,
    require_complete,
    validation_issues,
)
from statlab.domains.binding import RunBinding, bind_tabular
from statlab.domains.tabular import SETTING_OWNERS, TABULAR_DOMAIN
from statlab.errors import (
    ConfigError,
    EngineError,
    LlmCallError,
    LlmOutputError,
    PersistenceError,
    traceback_frames,
)
from statlab.graph.checkpoint import Checkpointer
from statlab.graph.control import ControlContext, ControlResponse, ControlText, ConversationTurn
from statlab.graph.engine import RunTracker
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.reconciliation import RecoveryBinding, RestartReconciler
from statlab.graph.recovery import recover_call
from statlab.graph.router import ControlRouter, InterruptClass
from statlab.graph.state import RunState
from statlab.llm.routing import check_provider_environment
from statlab.llm.skills import load_run_skill, snapshot_skill
from statlab.memory import read_memory
from statlab.persistence.models import ContractRow, GateRow, MemoryRow, RunMode, RunRow
from statlab.persistence.repository import (
    create_message,
    create_run,
    create_session,
    get_contract,
    get_dataset,
    get_gate,
    get_run,
    get_session,
    list_gates,
    list_messages,
    list_projects,
    require_project,
    require_run,
    update_run_status,
)
from statlab.storage import ensure_run_layout

_LOGGER = logging.getLogger(__name__)
_RUN_ID = re.compile(r"^r_[0-9a-f]{16}$")
_CHUNK_SIZE = 64 * 1024


class RunCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: RunMode
    dataset_ids: tuple[str, ...] = Field(min_length=1, max_length=1)
    contract_id: str = Field(min_length=1)

    @field_validator("dataset_ids")
    @classmethod
    def _dataset_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("dataset id must not be blank")
        return values

    @field_validator("contract_id")
    @classmethod
    def _contract_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("contract id must not be blank")
        return value


class RunAccepted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str


class ForkAccepted(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    new_run_id: str


class RunSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run: RunRow
    state: RunState
    gates: tuple[GateRow, ...]
    execution_failed: bool = False


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_id: str = Field(min_length=1)
    memory: tuple[MemoryRow, ...]
    fork: dict[str, JsonValue] | None = None

    @field_validator("dataset_id", "session_id")
    @classmethod
    def _nonblank_config_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifier must not be blank")
        return value


class GateSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    revision: int = Field(default=0, ge=0)
    action: GateAction
    edits: dict[str, JsonValue] = Field(default_factory=dict)
    instruction: str | None = None
    reason: str | None = None
    target: str | None = None


class GateEditCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    edits: dict[str, JsonValue]


class ControlRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["pause", "resume", "cancel", "rewind"]
    target: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("target", "request_id")
    @classmethod
    def _nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def _rewind_fields(self) -> ControlRequest:
        if (self.action == "rewind") != (self.target is not None and self.request_id is not None):
            raise ValueError("rewind requires target and request_id")
        if self.action != "rewind" and (self.target is not None or self.request_id is not None):
            raise ValueError("target and request_id are rewind only")
        return self


async def _retrying[T](call: Callable[[], Awaitable[T]]) -> T:
    """Chat answers retry transient provider failures the way node calls do."""
    return await recover_call(call)


class RunRegistry:
    def __init__(self, resources: APIResources) -> None:
        self.resources = resources
        self.projects = ProjectsService(resources)
        self.tracker = RunTracker()
        self.bindings: dict[str, RunBinding] = {}
        self.execution_tasks: dict[str, asyncio.Task[RunState]] = {}
        self.failed_tasks: set[str] = set()
        self.closing = False
        self.streams_closing = asyncio.Event()
        self._lock = asyncio.Lock()
        self._admitted: set[asyncio.Task[object]] = set()
        self.requests_drained = asyncio.Event()
        self.requests_drained.set()
        self.validated_models = set(resources.router.models) | {
            resources.router.model_for(item.node, skill_model=item.model)
            for item in resources.policy.nodes
        }

    @asynccontextmanager
    async def request(self) -> AsyncIterator[None]:
        if self.closing:
            raise HTTPException(503, "application is closing")
        # Route handlers always run inside an event-loop task.
        task = cast("asyncio.Task[object]", asyncio.current_task())
        self._admitted.add(task)
        self.requests_drained.clear()
        try:
            yield
        finally:
            self._admitted.discard(task)
            if not self._admitted:
                self.requests_drained.set()

    @asynccontextmanager
    async def mutation(self) -> AsyncIterator[None]:
        async with self._lock:
            yield

    def _capacity(self, run_id: str | None = None) -> None:
        maximum = self.resources.settings.max_concurrent_runs
        if maximum and sum(not task.done() for task in self.execution_tasks.values()) >= maximum:
            raise HTTPException(409, {"message": "run capacity exhausted", "run_id": run_id})

    def binding(self, row: RunRow, *, initial: RunState | None = None) -> RunBinding:
        cached = self.bindings.get(row.id)
        if cached is not None:
            if initial is not None:
                cached.lifecycle.initial = initial
            return cached
        if _RUN_ID.fullmatch(row.id) is None:
            raise PersistenceError("stored run id is invalid")
        project = require_project(self.resources.conn, row.project_id)
        try:
            config = RunConfig.model_validate_json(row.config_json)
        except ValidationError as exc:
            raise PersistenceError("stored run config is invalid", cause=exc) from exc
        contract_row = get_contract(self.resources.conn, row.contract_id)
        if contract_row is None or contract_row.project_id != row.project_id:
            raise PersistenceError("run contract ownership mismatch")
        dataset = get_dataset(self.resources.conn, config.dataset_id)
        if dataset is None or dataset.project_id != row.project_id:
            raise PersistenceError("run dataset ownership mismatch")
        session = get_session(self.resources.conn, config.session_id)
        if session is None or session.project_id != row.project_id:
            raise PersistenceError("run session ownership mismatch")
        if any(memory.project_id != row.project_id for memory in config.memory):
            raise PersistenceError("run memory ownership mismatch")
        for memory in config.memory:
            memory.value()
        paths_project = self.projects.paths(project)
        paths = RunPaths(self.resources.settings.data_dir, project.slug, row.id)
        root = paths.root.resolve()
        if not root.is_relative_to(paths_project.runs.resolve()):
            raise PersistenceError("run path escapes project runs")
        for child in (
            paths.artifacts,
            paths.nodes,
            paths.candidates,
            paths.report,
            paths.state_json,
            paths.events_jsonl,
            paths.contract_snapshot_json,
            paths.skill_snapshot_md,
            paths.root / "uv.lock",
        ):
            if not child.resolve().is_relative_to(root):
                raise PersistenceError("run child path escapes run root")
        contract_path = Path(contract_row.path).resolve()
        if (
            not contract_path.is_relative_to(paths_project.contracts.resolve())
            or not contract_path.is_file()
        ):
            raise PersistenceError("stored contract path is invalid")
        if contract_path.read_bytes() != contract_row.contract_json.encode("utf-8"):
            raise PersistenceError("stored contract file differs from SQL")
        if dataset.sha256 != config.dataset_sha256:
            raise PersistenceError("run dataset hash differs from SQL")
        dataset_path = Path(dataset.path).resolve()
        if (
            not dataset_path.is_relative_to(paths_project.datasets.resolve())
            or not dataset_path.is_file()
        ):
            raise PersistenceError("stored dataset path is invalid")
        digest = hashlib.sha256()
        with dataset_path.open("rb") as stream:
            while block := stream.read(_CHUNK_SIZE):
                digest.update(block)
        if digest.hexdigest() != config.dataset_sha256:
            raise PersistenceError("stored dataset bytes differ from run config")
        contract = contract_row.contract()
        try:
            require_complete(contract)
        except HTTPException as exc:
            raise PersistenceError("stored run contract is incomplete", cause=exc) from exc
        if (
            not paths.contract_snapshot_json.is_file()
            or not paths.skill_snapshot_md.is_file()
            or not (paths.root / "uv.lock").is_file()
        ):
            raise PersistenceError("run immutable snapshot is missing")
        if paths.contract_snapshot_json.read_bytes() != contract_row.contract_json.encode("utf-8"):
            raise PersistenceError("run contract snapshot differs from SQL")
        try:
            skill = load_run_skill(
                paths.root,
                TABULAR_DOMAIN.skill_file,
                spine=TABULAR_DOMAIN.nodes,
                tools=self.resources.mcp.tools,
            )
        except ConfigError as exc:
            raise PersistenceError("run skill snapshot is invalid", cause=exc) from exc
        models = tuple(
            dict.fromkeys(
                self.resources.router.model_for(item.node, skill_model=item.model)
                for item in skill.nodes
            )
        )
        unvalidated = tuple(model for model in models if model not in self.validated_models)
        if unvalidated:
            check_provider_environment(unvalidated, environment=self.resources.environment)
            self.validated_models.update(unvalidated)
        actual_initial = initial or RunState.initial(
            run_id=row.id, mode=row.mode, spine_id=TABULAR_DOMAIN.name, nodes=TABULAR_DOMAIN.nodes
        )
        lockfile = paths.root / "uv.lock"

        def resolver(child_paths: RunPaths, child_state: RunState) -> RunLifecycle:
            child_row = require_run(self.resources.conn, child_paths.run_id)
            return self.binding(child_row, initial=child_state).lifecycle

        result = bind_tabular(
            conn=self.resources.conn,
            settings=self.resources.settings,
            paths=paths,
            contract=contract,
            dataset=dataset.sha256,
            skill=skill,
            router=self.resources.router,
            llm=self.resources.llm,
            mcp=self.resources.mcp,
            memory=config.memory,
            tracker=self.tracker,
            initial=actual_initial,
            resolver=resolver,
            lockfile_path=lockfile,
        )
        self.bindings[row.id] = result
        return result

    def run(self, run_id: str) -> RunBinding:
        row = get_run(self.resources.conn, run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        return self.binding(row)

    async def boot(self) -> None:
        project_ids = tuple(project.id for project in list_projects(self.resources.conn))

        def resolver(row: RunRow) -> RecoveryBinding:
            binding = self.binding(row)
            return RecoveryBinding(binding.loop, binding.lifecycle.initial, binding.lifecycle)

        batch = await RestartReconciler(
            conn=self.resources.conn, resolver=resolver, tracker=self.tracker
        ).reconcile_projects(
            project_ids, max_concurrent_runs=self.resources.settings.max_concurrent_runs
        )
        for task in batch.tasks:
            run_id = task.get_name().removeprefix("statlab-run:")
            self._adopt(run_id, task)

    async def shutdown(self) -> None:
        self.begin_shutdown()
        await self.requests_drained.wait()
        first_failure: BaseException | None = None
        for run_id, binding in tuple(self.bindings.items()):
            row = get_run(self.resources.conn, run_id)
            task = self.execution_tasks.get(run_id)
            if task is not None and task.done():
                self._completed(run_id, task)
                task = self.execution_tasks.get(run_id)
            if row is None or run_id in self.failed_tasks:
                continue
            active_owned = task is not None and not task.done()
            if row.status not in ("pending", "running") or (
                row.status == "running" and not active_owned
            ):
                continue
            try:
                await binding.lifecycle.pause()
            except EngineError as exc:
                refreshed = get_run(self.resources.conn, run_id)
                if (
                    refreshed is None
                    or refreshed.status
                    not in (
                        "completed",
                        "failed",
                        "cancelled",
                        "superseded",
                        "awaiting_user",
                        "paused",
                    )
                ) and first_failure is None:
                    first_failure = exc
            except Exception as exc:
                first_failure = first_failure or exc
        tasks = tuple(self.execution_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run_id, task in tuple(self.execution_tasks.items()):
            self._completed(run_id, task)
        if first_failure is not None:
            raise first_failure

    def begin_shutdown(self) -> None:
        self.closing = True
        self.streams_closing.set()

    def _adopt(self, run_id: str, task: asyncio.Task[RunState]) -> None:
        self.execution_tasks[run_id] = task
        task.add_done_callback(lambda completed: self._completed(run_id, completed))

    def _completed(self, run_id: str, task: asyncio.Task[RunState]) -> None:
        if task.cancelled():
            failure = True
            _LOGGER.warning("Run %s execution task was cancelled", run_id)
        else:
            exc = task.exception()
            failure = exc is not None
            if exc is not None:
                _LOGGER.error(
                    "Run %s execution failed with %s\n%s",
                    run_id,
                    type(exc).__name__,
                    traceback_frames(exc),
                )
        if self.execution_tasks.get(run_id) is task:
            del self.execution_tasks[run_id]
        if failure:
            self.failed_tasks.add(run_id)
        else:
            self.failed_tasks.discard(run_id)

    def _launch(
        self, binding: RunBinding, *, resume: bool = False, deduplicate: bool = False
    ) -> bool:
        if self.closing:
            raise HTTPException(503, "application is closing")
        current = self.execution_tasks.get(binding.lifecycle.initial.run_id)
        if current is not None and not current.done():
            if deduplicate:
                return False
            raise HTTPException(409, "run transition rejected")
        self._capacity(binding.lifecycle.initial.run_id)
        runner = binding.lifecycle.resume if resume else binding.lifecycle.start
        task = asyncio.create_task(runner(), name="statlab-run:" + binding.lifecycle.initial.run_id)
        self._adopt(binding.lifecycle.initial.run_id, task)
        return True

    async def _settle_gate_task(self, binding: RunBinding) -> None:
        task = self.execution_tasks.get(binding.lifecycle.initial.run_id)
        if (
            task is not None
            and not task.done()
            and any(
                gate.status == "pending"
                for gate in list_gates(self.resources.conn, binding.lifecycle.initial.run_id)
            )
        ):
            await asyncio.shield(task)

    async def create(self, project_id: str, body: RunCreate) -> RunAccepted:
        async with self.mutation():
            project = self.projects.project(project_id)
            contract_row = get_contract(self.resources.conn, body.contract_id)
            if contract_row is None or contract_row.project_id != project.id:
                raise HTTPException(404, "contract not found")
            dataset = self.projects.dataset(project.id, body.dataset_ids[0])
            contract = contract_row.contract()
            require_complete(contract)
            paths_project = self.projects.paths(project)
            contract_path = Path(contract_row.path).resolve()
            if (
                not contract_path.is_relative_to(paths_project.contracts.resolve())
                or not contract_path.is_file()
                or contract_path.read_bytes() != contract_row.contract_json.encode("utf-8")
            ):
                raise PersistenceError("stored contract path is invalid")
            dataset_path = Path(dataset.path)
            digest = hashlib.sha256()
            with dataset_path.open("rb") as stream:
                while block := stream.read(_CHUNK_SIZE):
                    digest.update(block)
            if digest.hexdigest() != dataset.sha256:
                raise PersistenceError("stored dataset bytes differ from SQL")
            validation = await self.projects.validate(project.id, dataset.id, contract)
            if not validation.valid:
                raise HTTPException(422, "contract does not match dataset")
            if self.closing:
                raise HTTPException(503, "application is closing")
            self._capacity()
            memory = read_memory(self.resources.conn, project.id)
            session = create_session(self.resources.conn, project_id=project.id, title=project.name)
            config = RunConfig(
                dataset_id=dataset.id,
                dataset_sha256=dataset.sha256,
                session_id=session.id,
                memory=memory,
            )
            row = create_run(
                self.resources.conn,
                project_id=project.id,
                contract_id=contract_row.id,
                mode=body.mode,
                config_json=config.model_dump_json(),
                status="cancelled",
            )
            paths = RunPaths(self.resources.settings.data_dir, project.slug, row.id)
            paths.root.mkdir(parents=True, exist_ok=False)
            ensure_run_layout(paths)
            with paths.contract_snapshot_json.open("xb") as stream:
                stream.write(contract_row.contract_json.encode("utf-8"))
            snapshot_skill(self.resources.policy, paths.root)
            lock_source = Path("uv.lock").resolve()
            if not lock_source.is_file() or not lock_source.stat().st_size:
                raise PersistenceError("uv.lock source is missing or empty")
            with (paths.root / "uv.lock").open("xb") as stream:
                stream.write(lock_source.read_bytes())
            initial = RunState.initial(
                run_id=row.id,
                mode=row.mode,
                spine_id=TABULAR_DOMAIN.name,
                nodes=TABULAR_DOMAIN.nodes,
            )
            Checkpointer(paths).save(initial)
            binding = self.binding(row, initial=initial)
            update_run_status(self.resources.conn, row.id, status="pending", current_node=None)
            self._launch(binding)
            return RunAccepted(run_id=row.id)

    async def snapshot(self, run_id: str) -> RunSnapshot:
        binding = self.run(run_id)
        state = await binding.lifecycle.state()
        row = require_run(self.resources.conn, run_id)
        return RunSnapshot(
            run=row,
            state=state,
            gates=list_gates(self.resources.conn, run_id),
            execution_failed=run_id in self.failed_tasks,
        )

    async def _resume(self, binding: RunBinding) -> ControlResponse:
        run_id = binding.lifecycle.initial.run_id
        task = self.execution_tasks.get(run_id)
        if task is not None and not task.done():
            raise HTTPException(409, "run transition rejected")
        row = require_run(self.resources.conn, run_id)
        if row.status not in ("paused", "pending"):
            raise HTTPException(409, "run transition rejected")
        self._launch(binding, resume=row.status == "paused")
        return ControlResponse(category="lifecycle", text="Run continuation scheduled.")

    async def _continue(self, binding: RunBinding, response: ControlResponse) -> None:
        run_id = response.new_run_id or binding.lifecycle.initial.run_id
        row = get_run(self.resources.conn, run_id)
        if row is not None and row.status in ("pending", "running"):
            self._launch(self.run(run_id), deduplicate=True)

    async def control(self, run_id: str, body: ControlRequest) -> ControlResponse | ForkAccepted:
        async with self.mutation():
            binding = self.run(run_id)
            await binding.lifecycle.audit_anchor()
            try:
                if body.action == "resume":
                    return await self._resume(binding)
                if body.action == "rewind":
                    fork = await binding.forks.fork(
                        str(body.target), request_id=str(body.request_id)
                    )
                    await self._continue(
                        binding,
                        ControlResponse(
                            category="change", text="Run forked.", new_run_id=fork.new_run_id
                        ),
                    )
                    return ForkAccepted(new_run_id=fork.new_run_id)
                state = (
                    await binding.lifecycle.pause()
                    if body.action == "pause"
                    else await binding.lifecycle.cancel()
                )
            except EngineError:
                raise HTTPException(409, "run transition rejected") from None
            return ControlResponse(category="lifecycle", text=f"Run {state.status}.")

    async def answer(self, run_id: str, gate_id: str, body: GateSubmission) -> ControlResponse:
        async with self.mutation():
            binding = self.run(run_id)
            gate = get_gate(self.resources.conn, gate_id)
            if gate is None or gate.run_id != run_id:
                raise HTTPException(404, "gate not found")
            await self._settle_gate_task(binding)
            await binding.lifecycle.audit_anchor()
            try:
                answer = GateAnswer(
                    gate_id=gate_id,
                    revision=body.revision,
                    action=body.action,
                    edits=body.edits,
                    instruction=body.instruction,
                    reason=body.reason,
                    target=body.target,
                )
                response = await binding.gates.answer(answer)
                await self._continue(binding, response)
                return response
            except ValidationError:
                raise HTTPException(422, "invalid request") from None
            except EngineError:
                raise HTTPException(409, "run transition rejected") from None

    async def check_edits(self, run_id: str, gate_id: str, body: GateEditCheck) -> ContractCheck:
        async with self.mutation():
            binding = self.run(run_id)
            gate = get_gate(self.resources.conn, gate_id)
            if gate is None or gate.run_id != run_id:
                raise HTTPException(404, "gate not found")
            try:
                binding.gates.check_edits(gate, dict(body.edits))
            except ValidationError as exc:
                return ContractCheck(errors=validation_issues(exc))
            except EngineError:
                raise HTTPException(409, "run transition rejected") from None
            return ContractCheck(errors=())

    async def _lifecycle(
        self, binding: RunBinding, message: str, classified: InterruptClass, context: ControlContext
    ) -> ControlResponse:
        if classified.lifecycle_action == "resume":
            return await self._resume(binding)
        try:
            return await binding.lifecycle.handle(message, classified, context)
        except EngineError:
            raise HTTPException(409, "run transition rejected") from None

    def _edit_settings(
        self, binding: RunBinding, project_id: str, edits: dict[str, JsonValue]
    ) -> tuple[str, ContractRow]:
        data: dict[str, JsonValue] = binding.runtime.contract.model_dump(
            mode="json", exclude_unset=True
        )
        for path, value in edits.items():
            keys = path.split(".")
            if keys[0] not in SETTING_OWNERS:
                raise ValueError(f"{path} cannot be changed from the chat")
            node = data
            for key in keys[:-1]:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = {}
                    node[key] = child
                node = child
            node[keys[-1]] = value
        contract = DataContract.model_validate(data)
        missing = missing_setup_fields(contract)
        if missing:
            raise ValueError("these settings would become empty: " + ", ".join(missing))
        names = [node.name for node in TABULAR_DOMAIN.nodes]
        owner = min((SETTING_OWNERS[path.split(".")[0]] for path in edits), key=names.index)
        return owner, self.projects.persist_contract(project_id, contract)

    async def message(self, run_id: str, body: ControlText) -> ControlResponse:
        async with self.mutation():
            binding = self.run(run_id)
            await self._settle_gate_task(binding)
            run_row = require_run(self.resources.conn, run_id)
            config = RunConfig.model_validate_json(run_row.config_json)
            user = create_message(
                self.resources.conn,
                session_id=config.session_id,
                run_id=run_id,
                role="user",
                content=body.text,
            )
            await binding.loop.events.append(
                "message.user",
                {"message_id": user.id, "session_id": user.session_id, "text": user.content},
            )
            anchor = await binding.lifecycle.audit_anchor()
            state = await binding.lifecycle.state()
            turns = tuple(
                ConversationTurn(role=row.role, text=row.content)
                for row in list_messages(self.resources.conn, config.session_id)
            )
            while sum(len(turn.text) for turn in turns) > 16000:
                turns = turns[1:]
            current_node = anchor.node_name if state.cursor < len(state.spine) else None

            async def change(
                message: str, classified: InterruptClass, ctx: ControlContext
            ) -> ControlResponse:
                return await binding.forks.handle(
                    message,
                    classified,
                    ctx,
                    request_id=user.id,
                    edit_settings=lambda edits: self._edit_settings(
                        binding, run_row.project_id, edits
                    ),
                )

            router = ControlRouter(
                decision=binding.gates.handle,
                change=change,
                lifecycle=lambda message, classified, ctx: self._lifecycle(
                    binding, message, classified, ctx
                ),
                polish_status=False,
            )

            async def prose_sink(text: str, reset: bool) -> None:
                await binding.loop.events.append(
                    "message.assistant.delta",
                    {
                        "reply_to": user.id,
                        "session_id": user.session_id,
                        "text": text,
                        "reset": reset,
                    },
                )

            context = ControlContext(
                conn=self.resources.conn,
                llm=self.resources.llm,
                router=self.resources.router,
                state=state,
                contract=binding.runtime.contract,
                skill=binding.runtime.skill,
                audit_execution_id=anchor.id,
                current_node=current_node,
                memory=binding.runtime.memory,
                conversation=turns,
                artifact_summaries=(),
                estimated_remaining_seconds=None,
                recovery=_retrying,
                prose_sink=prose_sink,
            )
            try:
                response = await router.handle(body.text, context)
            except EngineError:
                raise HTTPException(409, "run transition rejected") from None
            except LlmCallError, LlmOutputError:
                await binding.loop.events.append(
                    "error",
                    {
                        "reply_to": user.id,
                        "message": "I could not get an answer from the model. Please ask again.",
                        "discard_draft": True,
                    },
                )
                raise
            except asyncio.CancelledError:
                with suppress(PersistenceError):
                    await binding.loop.events.append(
                        "error",
                        {
                            "reply_to": user.id,
                            "message": "Assistant response cancelled.",
                            "discard_draft": True,
                        },
                    )
                raise
            assistant = create_message(
                self.resources.conn,
                session_id=config.session_id,
                run_id=run_id,
                role="assistant",
                content=response.text,
                meta=response.model_dump(mode="json", exclude={"text"}),
            )
            await binding.loop.events.append(
                "message.assistant.complete",
                {
                    "message_id": assistant.id,
                    "reply_to": user.id,
                    "session_id": assistant.session_id,
                    "text": assistant.content,
                    "response": response.model_dump(mode="json", exclude={"text"}),
                },
            )
            if response.category in ("decision", "change"):
                await self._continue(binding, response)
            return response


def runs_router(registry: Callable[[], RunRegistry]) -> APIRouter:
    router = APIRouter()

    @router.post("/api/projects/{project_id}/runs", status_code=201, response_model=RunAccepted)
    async def create_run_endpoint(project_id: str, body: RunCreate) -> RunAccepted:
        return await registry().create(project_id, body)

    @router.get("/api/runs/{run_id}", response_model=RunSnapshot)
    async def snapshot_endpoint(run_id: str) -> RunSnapshot:
        return await registry().snapshot(run_id)

    @router.post("/api/runs/{run_id}/messages", response_model=ControlResponse)
    async def message_endpoint(run_id: str, body: ControlText) -> ControlResponse:
        return await registry().message(run_id, body)

    @router.post("/api/runs/{run_id}/gates/{gate_id}", response_model=ControlResponse)
    async def answer_endpoint(run_id: str, gate_id: str, body: GateSubmission) -> ControlResponse:
        return await registry().answer(run_id, gate_id, body)

    @router.post("/api/runs/{run_id}/gates/{gate_id}/check", response_model=ContractCheck)
    async def check_edits_endpoint(run_id: str, gate_id: str, body: GateEditCheck) -> ContractCheck:
        return await registry().check_edits(run_id, gate_id, body)

    @router.post(
        "/api/runs/{run_id}/control",
        response_model=ControlResponse | ForkAccepted,
        status_code=200,
    )
    async def control_endpoint(
        run_id: str, body: ControlRequest, response: Response
    ) -> ControlResponse | ForkAccepted:
        result = await registry().control(run_id, body)
        if isinstance(result, ForkAccepted):
            response.status_code = 201
        return result

    return router
