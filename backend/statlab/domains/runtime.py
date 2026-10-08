"""Bind caller-owned clients and immutable prompt inputs to an engine invocation."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel, JsonValue
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab.errors import EngineError
from statlab.graph.candidates import ToolBoundary
from statlab.graph.engine import NodeInvocation
from statlab.graph.recovery import recover_call
from statlab.graph.state import BoundaryResult
from statlab.llm.client import LlmClient
from statlab.llm.prompts import PromptContext
from statlab.llm.routing import ModelRouter, Role
from statlab.llm.skills import SkillPolicy
from statlab.mcp_client import CallContext
from statlab.persistence.models import MemoryRow
from statlab.persistence.repository import get_champion_model

_LOGGER = logging.getLogger(__name__)
Approval = Callable[[NodeInvocation, BaseModel, tuple[JsonValue, ...]], Awaitable[BaseModel | None]]
DecisionOverride = Callable[[NodeInvocation, type[BaseModel]], Awaitable[BaseModel | None]]
_DIGEST_DROP: frozenset[str] = frozenset({"registry", "candidate_set", "sections"})
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _brief(text: str) -> str:
    short = " ".join(_SENTENCE.split(text.strip())[:2])
    return short if len(short) <= 400 else short[:399] + "…"


def _prune(value: JsonValue, drop: frozenset[str]) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: _brief(item)
            if key == "rationale" and isinstance(item, str)
            else _prune(item, drop)
            for key, item in value.items()
            if key not in drop
        }
    if isinstance(value, list):
        return [_prune(item, drop) for item in value]
    return value


def boundary_digest(boundaries: Sequence[BoundaryResult], *, keep_tools: bool) -> list[JsonValue]:
    """Compact shared results for prompt context only; code never reads it back."""
    drop = _DIGEST_DROP if keep_tools else _DIGEST_DROP | {"tools"}
    latest: dict[str, BoundaryResult] = {}
    for boundary in boundaries:
        latest[boundary.node_name] = boundary
    digest: list[JsonValue] = []
    for boundary in latest.values():
        result = boundary.result
        entry: dict[str, JsonValue] = {
            "node": boundary.node_name,
            "status": result.status,
            "summary": _prune(result.summary, drop),
        }
        if result.warnings:
            entry["warnings"] = list(result.warnings)
        if result.skip_reason is not None:
            entry["skip_reason"] = result.skip_reason
        if result.error is not None:
            entry["error"] = result.error.model_dump(mode="json")
        digest.append(entry)
    return digest


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
    lockfile_path: Path | None = None
    decision_override: DecisionOverride | None = None

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
            champion_selected=invocation.spec.name == "champion_selection"
            and get_champion_model(self.conn, self.paths.run_id) is not None,
        )

    async def decide[T: BaseModel](
        self,
        invocation: NodeInvocation,
        output: type[T],
        *,
        evidence: tuple[JsonValue, ...],
        question: str,
        check: Callable[[T], None] | None = None,
    ) -> T:
        context = self.context(invocation)
        _LOGGER.info(
            "LLM decide node=%s candidate=%s output=%s execution=%s",
            context.node,
            invocation.candidate.public_id if invocation.candidate is not None else "-",
            output.__name__,
            context.node_execution_id,
        )
        if self.decision_override is not None:
            approved = await self.decision_override(invocation, output)
            if approved is not None:
                return output.model_validate(approved.model_dump(mode="json"))
        skill = self.skill.node(context.node)
        shared: JsonValue = {
            "boundaries": boundary_digest(
                invocation.state.shared_results, keep_tools=not invocation.state.branches
            ),
            "checkpointed_candidates": [
                branch.spec.model_dump(mode="json") for branch in invocation.state.branches
            ],
        }
        prompt = PromptContext(self.skill.preamble, skill, self.contract, self.memory, shared)

        async def recovery[U](call: Callable[[], Awaitable[U]]) -> U:
            async def request() -> U:
                self.tools.control.check()
                return await call()

            def can_retry() -> bool:
                self.tools.control.check()
                return True

            if self.tools.sleep is None:
                return await recover_call(request, can_retry=can_retry)
            return await recover_call(request, sleep=self.tools.sleep, can_retry=can_retry)

        self.tools.control.check()
        return await self.llm.complete(
            self.conn,
            run_id=context.paths.run_id,
            node_execution_id=context.node_execution_id,
            model=self.router.model_for(context.node, skill_model=skill.model),
            messages=prompt.messages(
                state_summary={
                    "node": context.node,
                    "candidate": invocation.candidate.model_dump(mode="json")
                    if invocation.candidate is not None
                    else None,
                    "sibling_validation": [
                        {
                            "candidate": branch.public_id,
                            "wave": branch.wave,
                            "metrics": boundary.result.summary.get("metrics"),
                            "primary_metric": boundary.result.summary.get("primary_metric"),
                            "threshold": boundary.result.summary.get("threshold"),
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
            recovery=recovery,
            check=check,
        )

    async def approve[T: BaseModel](
        self,
        invocation: NodeInvocation,
        decision: T,
        evidence: tuple[JsonValue, ...],
        *,
        after_action: bool = False,
    ) -> T | None:
        if invocation.state.mode == "autonomous" or invocation.spec.gate_policy == "never":
            return decision
        if self.approval is None:
            return None
        approved = await self.approval(
            replace(invocation, after_action=after_action), decision, evidence
        )
        if approved is None:
            return None
        return type(decision).model_validate(approved.model_dump(mode="json"))

    async def call(
        self,
        invocation: NodeInvocation,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
    ) -> ToolSuccess:
        return await self.tools.call(self.context(invocation), tool, params, trusted=trusted)
