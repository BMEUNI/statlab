"""Fresh per-run composition over shared, already-entered clients."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel
from statlab_contracts.contract import DataContract
from statlab_contracts.layout import RunPaths

from statlab.config import Settings
from statlab.domains.base import NodeResult, NodeSpec
from statlab.domains.candidate_models import candidate_retune_decider
from statlab.domains.candidate_search import CandidateSearchPlanner
from statlab.domains.executor import TabularExecutor
from statlab.domains.rewind import publish_retained_report
from statlab.domains.runtime import DomainRuntime
from statlab.domains.tabular import TABULAR_DOMAIN, TABULAR_TRAVERSAL
from statlab.errors import PersistenceError
from statlab.events import EventBus
from statlab.graph.candidates import RunControl, ToolBoundary
from statlab.graph.checkpoint import Checkpointer
from statlab.graph.engine import NodeInvocation, RunLoop, RunTracker
from statlab.graph.gates import GateQuestion, GateService
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.rewind import ForkResolver, ForkService
from statlab.graph.state import RunState
from statlab.llm.client import LlmClient
from statlab.llm.prompts import PromptContext
from statlab.llm.routing import ModelRouter, Role
from statlab.llm.skills import SkillPolicy
from statlab.mcp_client import McpClient
from statlab.persistence.models import MemoryRow
from statlab.persistence.repository import list_gates, list_node_executions


@dataclass(frozen=True, slots=True)
class RunBinding:
    runtime: DomainRuntime
    loop: RunLoop
    lifecycle: RunLifecycle
    gates: GateService
    forks: ForkService


def bind_tabular(
    *,
    conn: sqlite3.Connection,
    settings: Settings,
    paths: RunPaths,
    contract: DataContract,
    dataset: str,
    skill: SkillPolicy,
    router: ModelRouter,
    llm: LlmClient,
    mcp: McpClient,
    memory: tuple[MemoryRow, ...],
    nodes: tuple[NodeSpec, ...],
    tracker: RunTracker,
    initial: RunState,
    resolver: ForkResolver,
    lockfile_path: Path,
) -> RunBinding:
    executor: TabularExecutor

    async def execute(invocation: NodeInvocation) -> NodeResult:
        return await executor(invocation)

    control = RunControl()
    loop = RunLoop(
        conn=conn,
        nodes=nodes,
        spine_id=TABULAR_DOMAIN.name,
        executor=execute,
        checkpointer=Checkpointer(paths),
        events=EventBus(paths),
        settings=settings,
        tracker=tracker,
        constraints=contract.constraints,
        traversal=TABULAR_TRAVERSAL,
        retune_decider=candidate_retune_decider,
        control=control,
    )
    lifecycle = RunLifecycle(loop, initial)
    forks = ForkService(lifecycle, resolver=resolver, publish_domain=publish_retained_report)

    async def revise(
        question: GateQuestion, instruction: str, output: type[BaseModel]
    ) -> BaseModel:
        matching = tuple(
            row
            for row in list_gates(conn, lifecycle.initial.run_id)
            if row.status in ("pending", "answered", "prepared")
            and GateQuestion.model_validate(row.question()) == question
        )
        if len(matching) != 1:
            raise PersistenceError("revision gate anchor is unavailable or ambiguous")
        gate = matching[0]
        execution = next(
            (
                row
                for row in list_node_executions(conn, lifecycle.initial.run_id)
                if row.id == gate.node_execution_id
            ),
            None,
        )
        if (
            execution is None
            or execution.node_name != question.node
            or execution.candidate_id != question.candidate_id
        ):
            raise PersistenceError("revision gate execution ownership mismatch")
        state = await lifecycle.state()
        prompt = PromptContext(
            skill.preamble, skill.node(question.node), contract, memory
        ).messages(
            state_summary={
                "question": question.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
            },
            recent_tool_results=question.evidence,
            question=(
                "Revise the typed proposal using this untrusted user instruction: " + instruction
            ),
        )
        node_skill = skill.node(question.node)
        try:
            effective_role = (
                Role(node_skill.model)
                if node_skill.model is not None
                else router.role_for(question.node)
            )
        except ValueError:
            effective_role = router.role_for(question.node)
        return await llm.complete(
            conn,
            run_id=lifecycle.initial.run_id,
            node_execution_id=execution.id,
            model=router.model_for(question.node, skill_model=node_skill.model),
            messages=prompt,
            output=output,
            reasoning=effective_role == Role.REASONING,
        )

    gates = GateService(lifecycle, revise=revise, rewind=forks.gate_rewind)
    runtime = DomainRuntime(
        conn=conn,
        paths=paths,
        dataset=dataset,
        contract=contract,
        tools=ToolBoundary(mcp, loop.control, loop.budget),
        llm=llm,
        skill=skill,
        router=router,
        memory=memory,
        approval=gates.approval,
        decision_override=gates.override,
        lockfile_path=lockfile_path,
    )
    executor = TabularExecutor(runtime)
    loop.candidate_planner = CandidateSearchPlanner(runtime)
    return RunBinding(runtime, loop, lifecycle, gates, forks)
