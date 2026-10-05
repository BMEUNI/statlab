"""Bind caller-owned clients and immutable prompt inputs to an engine invocation."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, JsonValue
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab.errors import EngineError
from statlab.graph.candidates import ToolBoundary
from statlab.graph.engine import NodeInvocation
from statlab.llm.client import LlmClient
from statlab.llm.prompts import PromptContext
from statlab.llm.routing import ModelRouter, Role
from statlab.llm.skills import SkillPolicy
from statlab.mcp_client import CallContext
from statlab.persistence.models import MemoryRow

Approval = Callable[[NodeInvocation, BaseModel, tuple[JsonValue, ...]], Awaitable[BaseModel | None]]


@dataclass(frozen=True, slots=True)
class DomainRuntime:
    conn: sqlite3.Connection
    paths: RunPaths
    dataset: str
    contract: DataContract
    tools: ToolBoundary
    llm: LlmClient
    skill: SkillPolicy
    router: ModelRouter
    memory: tuple[MemoryRow, ...] = ()
    approval: Approval | None = None

    def context(self, invocation: NodeInvocation) -> CallContext:
        if (
            invocation.state.run_id != self.paths.run_id
            or invocation.execution.run_id != self.paths.run_id
            or invocation.execution.node_name != invocation.spec.name
        ):
            raise EngineError("domain invocation identity mismatch")
        candidate = invocation.candidate
        if candidate is None:
            if invocation.spec.kind != "SHARED" or invocation.execution.candidate_id is not None:
                raise EngineError("shared invocation identity mismatch")
        elif (
            invocation.spec.kind != "PER_CANDIDATE"
            or invocation.execution.candidate_id != candidate.database_id(self.paths.run_id)
            or candidate not in invocation.state.branches
        ):
            raise EngineError("candidate invocation identity mismatch")
        return CallContext(
            paths=self.paths,
            node=invocation.spec.name,
            node_execution_id=invocation.execution.id,
            allowed=self.skill.node(invocation.spec.name).allowed,
            contract=self.contract,
            dataset=self.dataset,
        )

    async def decide[T: BaseModel](
        self,
        invocation: NodeInvocation,
        output: type[T],
        *,
        evidence: tuple[JsonValue, ...],
        question: str,
    ) -> T:
        context = self.context(invocation)
        skill = self.skill.node(context.node)
        prompt = PromptContext(self.skill.preamble, skill, self.contract, self.memory)
        return await self.llm.complete(
            self.conn,
            run_id=context.paths.run_id,
            node_execution_id=context.node_execution_id,
            model=self.router.model_for(context.node, skill_model=skill.model),
            messages=prompt.messages(
                state_summary={
                    "node": context.node,
                    "boundaries": [
                        boundary.model_dump(mode="json")
                        for boundary in invocation.state.shared_results
                    ],
                    "candidate": invocation.candidate.model_dump(mode="json")
                    if invocation.candidate is not None
                    else None,
                    "checkpointed_candidates": [
                        branch.spec.model_dump(mode="json") for branch in invocation.state.branches
                    ],
                    "sibling_validation": [
                        {
                            "candidate": branch.public_id,
                            "wave": branch.wave,
                            "summary": boundary.result.summary,
                        }
                        for branch in invocation.state.branches
                        if invocation.candidate is None
                        or branch.public_id != invocation.candidate.public_id
                        for boundary in branch.results
                        if boundary.node_name == "model_evaluation"
                        and boundary.result.status == "completed"
                    ],
                },
                recent_tool_results=evidence,
                question=question,
            ),
            output=output,
            reasoning=self.router.role_for(context.node) == Role.REASONING,
        )

    async def approve[T: BaseModel](
        self, invocation: NodeInvocation, decision: T, evidence: tuple[JsonValue, ...]
    ) -> T | None:
        if invocation.state.mode == "autonomous":
            return decision
        if self.approval is None:
            return None
        approved = await self.approval(invocation, decision, evidence)
        if approved is None:
            return None
        return type(decision).model_validate(approved.model_dump(mode="json"))

    async def call(
        self, invocation: NodeInvocation, tool: str, params: dict[str, JsonValue]
    ) -> ToolSuccess:
        return await self.tools.call(self.context(invocation), tool, params)
