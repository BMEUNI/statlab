"""Per-run tabular composition over shared clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections import deque
from dataclasses import replace
from pathlib import Path

import litellm
import pytest
from litellm import ModelResponse
from pydantic import SecretStr
from statlab.config import Settings
from statlab.domains.base import NodeResult
from statlab.domains.binding import bind_tabular
from statlab.domains.candidate_models import candidate_retune_decider
from statlab.domains.candidate_search import CandidateSearchPlanner
from statlab.domains.decisions import IntakeDecision
from statlab.domains.rewind import publish_retained_report
from statlab.domains.routing import NODE_ROLES
from statlab.domains.tabular import TABULAR_DOMAIN, TABULAR_TRAVERSAL
from statlab.errors import EngineError, PersistenceError
from statlab.graph.engine import NodeInvocation, RunTracker
from statlab.graph.gates import GateQuestion
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.state import RunState
from statlab.llm.client import LlmClient
from statlab.llm.routing import ModelRouter
from statlab.llm.skills import load_skill, snapshot_skill
from statlab.mcp_client import McpClient
from statlab.memory import read_memory, remember
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import (
    create_contract,
    create_dataset,
    create_gate,
    create_node_execution,
    create_run,
    get_gate,
    list_gates,
    transition_gate,
)
from statlab.storage import ensure_run_layout
from statlab_contracts.contract import (
    DataContract,
    FeatureRules,
    Objective,
    SplitRules,
    TargetSpec,
)
from statlab_contracts.gates import GateAnswer
from statlab_contracts.layout import ProjectPaths, RunPaths

from tests.test_api_projects import SetupSession, _model_response


def complete_contract() -> DataContract:
    return DataContract(
        target=TargetSpec(column="a", type="binary", positive_class=1),
        objective=Objective(task="classification", primary_metric="roc_auc"),
        split=SplitRules(strategy="stratified"),
        features=FeatureRules(),
    )


@pytest.mark.asyncio
async def test_bind_tabular_builds_fresh_graphs_around_shared_clients(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    settings = Settings(
        mcp_token=SecretStr("fixture-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
    )
    mcp = McpClient(settings, migrated_db, session=SetupSession())
    async with mcp:
        contract = complete_contract()
        paths_project = ProjectPaths(settings.data_dir, project.slug)
        paths_project.contracts.mkdir(parents=True, exist_ok=True)
        contract_row = create_contract(
            migrated_db,
            project_id=project.id,
            path=paths_project.contracts / "contract.json",
            contract=contract,
        )
        Path(contract_row.path).write_text(contract_row.contract_json, encoding="utf-8")
        paths_project.datasets.mkdir(parents=True, exist_ok=True)
        dataset_path = paths_project.datasets / "dataset.csv"
        dataset_path.write_bytes(b"a,b\n0,1\n1,2\n")
        digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        dataset_row = create_dataset(
            migrated_db,
            project_id=project.id,
            filename="dataset.csv",
            path=dataset_path,
            sha256=digest,
            n_rows=2,
            n_cols=2,
        )
        contract_snapshot = contract_row.contract_json.encode("utf-8")
        policy = load_skill(TABULAR_DOMAIN.skill_file, spine=TABULAR_DOMAIN.nodes, tools=mcp.tools)
        router = ModelRouter(NODE_ROLES, {})
        llm = LlmClient(settings)
        tracker = RunTracker()
        remember(
            migrated_db,
            project_id=project.id,
            key="old",
            value="captured-old-fact",
        )
        memory = read_memory(migrated_db, project.id)
        bindings = []
        for _ in range(2):
            run = create_run(
                migrated_db,
                project_id=project.id,
                contract_id=contract_row.id,
                mode="guided",
                config_json="{}",
            )
            paths = RunPaths(settings.data_dir, project.slug, run.id)
            ensure_run_layout(paths)
            paths.contract_snapshot_json.write_bytes(contract_snapshot)
            snapshot_skill(policy, paths.root)
            lockfile = paths.root / "uv.lock"
            lockfile.write_bytes(b"version = 1\n")
            initial = RunState.initial(
                run_id=run.id,
                mode="guided",
                spine_id=TABULAR_DOMAIN.name,
                nodes=TABULAR_DOMAIN.nodes,
            )

            def resolver(child_paths: RunPaths, child_state: RunState) -> RunLifecycle:
                del child_paths, child_state
                raise AssertionError("fixture does not fork")

            binding = bind_tabular(
                conn=migrated_db,
                settings=settings,
                paths=paths,
                contract=contract,
                dataset=dataset_row.sha256,
                skill=policy,
                router=router,
                llm=llm,
                mcp=mcp,
                memory=memory,
                tracker=tracker,
                initial=initial,
                resolver=resolver,
                lockfile_path=lockfile,
            )
            bindings.append(binding)
        first, second = bindings
        assert first.runtime is not second.runtime
        assert first.loop is not second.loop and first.lifecycle is not second.lifecycle
        assert first.gates is not second.gates and first.forks is not second.forks
        assert first.loop.tracker is second.loop.tracker is tracker
        assert first.runtime.llm is second.runtime.llm is llm
        assert first.runtime.tools.client is second.runtime.tools.client is mcp
        assert first.runtime.memory is second.runtime.memory is memory
        assert first.runtime.tools.control is first.loop.control
        assert second.runtime.tools.control is second.loop.control
        assert first.runtime.tools.control is not second.runtime.tools.control
        assert first.runtime.tools.budget is first.loop.budget
        assert second.runtime.tools.budget is second.loop.budget
        assert first.loop.events is not second.loop.events
        assert first.loop.constraints is contract.constraints
        assert first.loop.traversal is TABULAR_TRAVERSAL
        assert isinstance(first.loop.candidate_planner, CandidateSearchPlanner)
        assert first.loop.candidate_planner.runtime is first.runtime
        assert first.loop.retune_decider is candidate_retune_decider
        assert first.runtime.approval is not None and first.runtime.decision_override is not None
        assert first.runtime.approval == first.gates.approval
        assert first.runtime.decision_override == first.gates.override
        assert first.loop.gate_reconciler is not None
        assert first.loop.gate_boundary_hook is not None
        assert first.loop.gate_reconciler == first.gates.reconcile
        assert first.loop.gate_boundary_hook == first.gates.capture
        assert first.loop.failure_hook == first.gates.failure
        assert first.forks.publish_domain is publish_retained_report
        assert first.gates.revise is not None
        assert first.gates.rewind == first.forks.gate_rewind

        spec = TABULAR_DOMAIN.nodes[0]
        execution = create_node_execution(
            migrated_db, run_id=first.lifecycle.initial.run_id, node_name=spec.name, seq=1
        )
        invocation = NodeInvocation(
            state=first.lifecycle.initial, spec=spec, execution=execution, candidate=None
        )
        context = first.runtime.context(invocation)
        assert context.allowed is policy.node(spec.name).allowed
        foreign = NodeInvocation(
            state=second.lifecycle.initial, spec=spec, execution=execution, candidate=None
        )
        with pytest.raises(EngineError):
            first.runtime.context(foreign)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", (False, True))
async def test_gate_revision_uses_captured_memory_and_exact_anchor(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    settings = Settings(
        mcp_token=SecretStr("fixture-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
    )
    mcp = McpClient(settings, migrated_db, session=SetupSession())
    async with mcp:
        contract = complete_contract()
        project_paths = ProjectPaths(settings.data_dir, project.slug)
        project_paths.contracts.mkdir(parents=True, exist_ok=True)
        contract_row = create_contract(
            migrated_db,
            project_id=project.id,
            path=project_paths.contracts / "contract.json",
            contract=contract,
        )
        Path(contract_row.path).write_text(contract_row.contract_json, encoding="utf-8")
        project_paths.datasets.mkdir(parents=True, exist_ok=True)
        dataset_path = project_paths.datasets / "dataset.csv"
        dataset_path.write_bytes(b"a,b\n0,1\n1,2\n")
        dataset_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        dataset_row = create_dataset(
            migrated_db,
            project_id=project.id,
            filename="dataset.csv",
            path=dataset_path,
            sha256=dataset_sha256,
            n_rows=2,
            n_cols=2,
        )
        run = create_run(
            migrated_db,
            project_id=project.id,
            contract_id=contract_row.id,
            mode="guided",
            config_json="{}",
        )
        paths = RunPaths(settings.data_dir, project.slug, run.id)
        ensure_run_layout(paths)
        paths.contract_snapshot_json.write_text(contract_row.contract_json, encoding="utf-8")
        policy = load_skill(TABULAR_DOMAIN.skill_file, spine=TABULAR_DOMAIN.nodes, tools=mcp.tools)
        snapshot_skill(policy, paths.root)
        policy = replace(
            policy,
            nodes=tuple(
                replace(item, model="fixture/explicit-model") if item.node == "intake" else item
                for item in policy.nodes
            ),
        )
        lockfile = paths.root / "uv.lock"
        lockfile.write_bytes(b"version = 1\n")
        remember(
            migrated_db,
            project_id=project.id,
            key="old",
            value="captured-old-fact",
        )
        memory = read_memory(migrated_db, project.id)
        initial = RunState.initial(
            run_id=run.id,
            mode="guided",
            spine_id=TABULAR_DOMAIN.name,
            nodes=TABULAR_DOMAIN.nodes,
        )
        answers: deque[ModelResponse | asyncio.CancelledError] = deque(
            (
                _model_response(IntakeDecision(rationale="First").model_dump_json()),
                asyncio.CancelledError()
                if cancel
                else _model_response(IntakeDecision(rationale="Revised").model_dump_json()),
            )
        )
        requests: list[object] = []

        async def acompletion(**kwargs: object) -> ModelResponse:
            requests.append(kwargs["messages"])
            answer = answers.popleft()
            if isinstance(answer, asyncio.CancelledError):
                raise answer
            return answer

        monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
        llm = LlmClient(settings, acompletion=acompletion)
        router = ModelRouter(NODE_ROLES, {})
        tracker = RunTracker()

        def resolver(child_paths: RunPaths, child_state: RunState) -> RunLifecycle:
            del child_paths, child_state
            raise AssertionError("fixture does not fork")

        binding = bind_tabular(
            conn=migrated_db,
            settings=settings,
            paths=paths,
            contract=contract,
            dataset=dataset_row.sha256,
            skill=policy,
            router=router,
            llm=llm,
            mcp=mcp,
            memory=memory,
            tracker=tracker,
            initial=initial,
            resolver=resolver,
            lockfile_path=lockfile,
        )

        async def execute(invocation: NodeInvocation) -> NodeResult:
            decision = await binding.runtime.decide(
                invocation,
                IntakeDecision,
                evidence=(),
                question="Fixture",
            )
            approved = await binding.runtime.approve(invocation, decision, evidence=())
            if approved is None:
                return NodeResult(status="awaiting_user")
            return NodeResult(status="completed")

        binding.loop.executor = execute
        state = await binding.lifecycle.start()
        assert state.status == "awaiting_user"
        gate = next(row for row in list_gates(migrated_db, run.id) if row.status == "pending")
        question = GateQuestion.model_validate(gate.question())
        assert "user_summary" not in question.editable
        assert "rationale" in question.editable
        remember(
            migrated_db,
            project_id=project.id,
            key="new",
            value="uncaptured-new-fact",
        )
        answer = GateAnswer(
            gate_id=gate.id,
            revision=0,
            action="instruct",
            instruction="Revise",
        )
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await binding.gates.answer(answer)
            retained = get_gate(migrated_db, gate.id)
            assert retained is not None and retained.status == "answered"
            retained_question = GateQuestion.model_validate(retained.question())
            assert retained_question.revision == 0
            assert retained_question.proposal["rationale"] == "First"
            events = await binding.loop.events.replay()
            assert not any(event.type == "gate.revised" for event in events)
            assert len(requests) == 2
        else:
            await binding.gates.answer(answer)
            retained = get_gate(migrated_db, gate.id)
            assert retained is not None and retained.status == "pending"
            retained_question = GateQuestion.model_validate(retained.question())
            assert retained_question.revision == 1
            assert len(retained_question.history) == 1
            assert retained_question.proposal["rationale"] == "Revised"
            calls = migrated_db.execute(
                "SELECT run_id, node_execution_id FROM llm_calls WHERE run_id = ?",
                (run.id,),
            ).fetchall()
            assert len(calls) == 2
            assert {row["node_execution_id"] for row in calls} == {gate.node_execution_id}
            assert {
                row["model"]
                for row in migrated_db.execute(
                    "SELECT model FROM llm_calls WHERE run_id = ?", (run.id,)
                )
            } == {"fixture/explicit-model"}
            assert all(row["run_id"] == run.id for row in calls)
            assert len(requests) == 2
            assert all(
                "captured-old-fact" in json.dumps(messages)
                and "uncaptured-new-fact" not in json.dumps(messages)
                for messages in requests
            )

        assert binding.gates.revise is not None
        missing_question = GateQuestion(
            node=question.node,
            candidate_id=question.candidate_id,
            phase=question.phase,
            proposal=question.proposal,
            decision_schema=question.decision_schema,
            decision_type=question.decision_type,
            evidence=question.evidence,
            reasoning="No matching durable question",
            editable=question.editable,
            default=question.default,
            options=question.options,
            context=question.context,
            revision=question.revision,
            history=question.history,
            failure=question.failure,
            awaiting_result=question.awaiting_result,
        )
        request_count = len(requests)
        with pytest.raises(PersistenceError):
            await binding.gates.revise(missing_question, "Revise", IntakeDecision)
        assert len(requests) == request_count

        current = get_gate(migrated_db, gate.id)
        assert current is not None
        transition_gate(migrated_db, current, status="applied", answer_json=current.answer_json)
        other = create_node_execution(
            migrated_db, run_id=run.id, node_name="problem_definition", seq=2
        )
        create_gate(migrated_db, run_id=run.id, execution_id=other.id, question=current.question())
        with pytest.raises(PersistenceError, match="revision gate execution ownership mismatch"):
            await binding.gates.revise(
                GateQuestion.model_validate(current.question()), "Revise", IntakeDecision
            )
