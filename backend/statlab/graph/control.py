"""Captured, read-only control context and grounded Q&A/status responses."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from statlab_contracts.artifacts import ArtifactRef, parse_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import Failure

from statlab.errors import EngineError, LlmOutputError
from statlab.graph.candidates import BudgetState
from statlab.graph.digest import boundary_digest, candidate_digest
from statlab.graph.state import RunState
from statlab.llm.client import CallRecovery, LlmClient, ProseSink
from statlab.llm.prompts import PromptContext
from statlab.llm.routing import ModelRouter, Role
from statlab.llm.skills import NodeSkill, SkillPolicy
from statlab.persistence.models import MemoryRow, RunMode, RunStatus

type InterruptCategory = Literal["qa", "status", "decision", "change", "lifecycle", "noise"]
_MAX_CONTROL_TEXT = 8000


class ControlText(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str = Field(min_length=1, max_length=_MAX_CONTROL_TEXT)

    @field_validator("text")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ConversationTurn(ControlText):
    role: Literal["user", "assistant"]


class ArtifactSummary(ControlText):
    ref: ArtifactRef


class QaAnswer(ControlText):
    references: tuple[ArtifactRef, ...] = Field(default=(), max_length=32)

    @field_validator("references")
    @classmethod
    def _syntax(cls, refs: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        return tuple(parse_artifact_ref(ref) for ref in refs)


class CandidateStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    public_id: str
    label: str
    status: str
    wave: int
    position: int
    current_node: str | None
    completed: tuple[str, ...]
    skipped: tuple[str, ...]
    remaining: tuple[str, ...]
    retune_count: int


class PendingStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node: str
    candidate_id: str | None
    status: Literal["completed", "skipped", "failed", "awaiting_user"]
    error: Failure | None
    warnings: tuple[str, ...]


class StatusFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    status: RunStatus
    mode: RunMode
    current_node: str | None
    cursor: int
    wave: int
    completed_shared: tuple[str, ...]
    skipped_shared: tuple[str, ...]
    remaining_shared: tuple[str, ...]
    candidates: tuple[CandidateStatus, ...]
    pending: tuple[PendingStatus, ...]
    budget: BudgetState | None
    approximate_remaining_seconds: float | None


class ControlResponse(ControlText):
    category: InterruptCategory
    references: tuple[ArtifactRef, ...] = ()
    facts: StatusFacts | None = None
    template: str | None = None
    new_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ControlContext:
    conn: sqlite3.Connection
    llm: LlmClient
    router: ModelRouter
    state: RunState
    contract: DataContract
    skill: SkillPolicy
    audit_execution_id: str
    current_node: str | None
    memory: tuple[MemoryRow, ...] = ()
    conversation: tuple[ConversationTurn, ...] = ()
    artifact_summaries: tuple[ArtifactSummary, ...] = ()
    estimated_remaining_seconds: float | None = None
    recovery: CallRecovery | None = None
    prose_sink: ProseSink | None = None
    _prompt: PromptContext = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.current_node is not None and self.current_node not in {
            entry.name for entry in self.state.spine
        }:
            raise EngineError("control current node is outside captured spine")
        if len(self.conversation) > 20 or sum(len(turn.text) for turn in self.conversation) > 16000:
            raise EngineError("control conversation exceeds summary limit")
        if (
            len(self.artifact_summaries) > 32
            or sum(len(item.text) for item in self.artifact_summaries) > 16000
        ):
            raise EngineError("control artifact summaries exceed limit")
        estimate = self.estimated_remaining_seconds
        if estimate is not None and (not math.isfinite(estimate) or estimate < 0):
            raise EngineError("control ETA must be finite and nonnegative")
        node_skill = (
            self.skill.node(self.current_node)
            if self.current_node is not None
            else NodeSkill(
                "",
                "\n\n".join(item.section for item in self.skill.nodes),
                "Read-only Q&A",
                "",
                "",
                "",
                "",
                frozenset(),
            )
        )
        object.__setattr__(
            self,
            "_prompt",
            PromptContext(self.skill.preamble, node_skill, self.contract, self.memory),
        )

    @property
    def visible_refs(self) -> frozenset[ArtifactRef]:
        pending = tuple(
            boundary
            for boundary in (
                self.state.pending_result,
                *(branch.pending_result for branch in self.state.branches),
            )
            if boundary is not None
        )
        return (
            self.state.retained_refs
            | frozenset(ref for boundary in pending for ref in boundary.result.artifacts)
            | frozenset(item.ref for item in self.artifact_summaries)
        )


def status_facts(context: ControlContext) -> StatusFacts:
    state = context.state
    shared = {item.node_name: item.result.status for item in state.shared_results}
    candidates: list[CandidateStatus] = []
    for branch in state.branches:
        position = (
            max(state.traversal.region(state.spine)[0], branch.position)
            if state.traversal is not None
            else state.cursor
        )
        latest = {item.node_name: item.result.status for item in branch.results}
        before = tuple(
            entry.name for entry in state.spine[:position] if entry.kind == "PER_CANDIDATE"
        )
        remaining = tuple(
            entry.name for entry in state.spine[position:] if entry.kind == "PER_CANDIDATE"
        )
        candidates.append(
            CandidateStatus(
                public_id=branch.public_id,
                label=branch.spec.label,
                status=branch.status,
                wave=branch.wave,
                position=position,
                current_node=remaining[0] if remaining and branch.status != "pending" else None,
                completed=tuple(name for name in before if latest.get(name) == "completed"),
                skipped=tuple(name for name in before if latest.get(name) == "skipped"),
                remaining=remaining,
                retune_count=branch.retune_count,
            )
        )
    pending = tuple(
        (candidate_id, boundary)
        for candidate_id, boundary in (
            (None, state.pending_result),
            *((branch.public_id, branch.pending_result) for branch in state.branches),
        )
        if boundary is not None
    )
    return StatusFacts(
        run_id=state.run_id,
        status=state.status,
        mode=state.mode,
        current_node=context.current_node,
        cursor=state.cursor,
        wave=state.wave,
        completed_shared=tuple(
            entry.name for entry in state.spine if shared.get(entry.name) == "completed"
        ),
        skipped_shared=tuple(
            entry.name for entry in state.spine if shared.get(entry.name) == "skipped"
        ),
        remaining_shared=tuple(
            entry.name
            for entry in state.spine
            if entry.kind == "SHARED" and entry.name not in shared
        ),
        candidates=tuple(candidates),
        pending=tuple(
            PendingStatus(
                node=boundary.node_name,
                candidate_id=candidate_id,
                status=boundary.result.status,
                error=boundary.result.error,
                warnings=boundary.result.warnings,
            )
            for candidate_id, boundary in pending
        ),
        budget=state.budget,
        approximate_remaining_seconds=context.estimated_remaining_seconds,
    )


def status_template(facts: StatusFacts) -> str:
    eta = (
        "unknown"
        if facts.approximate_remaining_seconds is None
        else f"approximately {facts.approximate_remaining_seconds:g} seconds"
    )
    lines = [
        f"Run {facts.run_id}: {facts.status}; mode {facts.mode}; wave {facts.wave}.",
        f"Current node: {facts.current_node or 'none (between/after nodes)'}.",
        f"Shared completed: {', '.join(facts.completed_shared) or 'none'}; "
        f"skipped: {', '.join(facts.skipped_shared) or 'none'}.",
        f"Shared remaining: {', '.join(facts.remaining_shared) or 'none'}.",
        f"Remaining time: {eta}.",
    ]
    candidate_counts = Counter(branch.status for branch in facts.candidates)
    pending_counts = Counter(item.status for item in facts.pending)
    candidate_summary = ", ".join(
        f"{status}={count}" for status, count in sorted(candidate_counts.items())
    )
    pending_summary = ", ".join(
        f"{status}={count}" for status, count in sorted(pending_counts.items())
    )
    lines.append(
        f"Candidates: {len(facts.candidates)} ({candidate_summary or 'none'}). "
        f"Pending boundaries: {len(facts.pending)} ({pending_summary or 'none'})."
    )
    if facts.budget is not None:
        lines.append(
            f"Fit budget: {facts.budget.total_fit_ms}/{facts.budget.limit_ms} ms; "
            f"exhausted: {facts.budget.exhausted}."
        )
    for branch in facts.candidates:
        lines.append(
            f"Candidate {branch.public_id} ({branch.label}): {branch.status}; "
            f"wave {branch.wave}; position {branch.position}; "
            f"completed {len(branch.completed)}; skipped {len(branch.skipped)}; "
            f"current {branch.current_node or 'none'}; "
            f"remaining {', '.join(branch.remaining) or 'none'}; retunes {branch.retune_count}."
        )
    for pending in facts.pending:
        lines.append(
            f"Pending {pending.candidate_id or 'shared'}/{pending.node}: {pending.status}"
            + (f"; {pending.error.error_class}: {pending.error.message}" if pending.error else "")
            + "."
        )
    template = "\n".join(lines)
    if len(template) <= _MAX_CONTROL_TEXT:
        return template
    notice = "\nDetails truncated; complete status facts are included."
    prefix = template[: _MAX_CONTROL_TEXT - len(notice)]
    complete_lines, separator, _ = prefix.rpartition("\n")
    return (complete_lines if separator else prefix) + notice


async def answer_question(message: str, context: ControlContext) -> ControlResponse:
    node = context.current_node or ""
    skill_model = context.skill.node(node).model if context.current_node is not None else None
    role = context.router.role_for(node)
    if skill_model in tuple(item.value for item in Role):
        role = Role(skill_model)
    facts = status_facts(context)
    messages = context._prompt.messages(
        state_summary={
            "status": facts.model_dump(mode="json"),
            "boundaries": boundary_digest(context.state.shared_results, keep_tools=False),
            "candidates": [candidate_digest(item) for item in context.state.branches],
            "visible_refs": [str(ref) for ref in sorted(context.visible_refs)],
            "conversation": [item.model_dump(mode="json") for item in context.conversation],
        },
        recent_tool_results=tuple(
            item.model_dump(mode="json") for item in context.artifact_summaries
        ),
        question=message,
    )
    model = context.router.model_for(node, skill_model=skill_model)
    request_messages = (
        messages[0],
        {
            "role": "system",
            "content": "Answer the user's question from the captured evidence only. "
            "Cite only visible_refs. "
            "Disclose missing/unexecuted evidence. Never recommend test-based selection, "
            "inspection or refitting. Do not perform actions or claim actions happened. "
            "Reply briefly in plain language: at most three short sentences unless the user "
            "asks for more detail. Use a markdown list or table only when it makes the answer "
            "clearer.",
        },
        messages[1],
    )
    if context.prose_sink is None:
        answer = await context.llm.complete(
            context.conn,
            run_id=context.state.run_id,
            node_execution_id=context.audit_execution_id,
            model=model,
            reasoning=role == Role.REASONING,
            output=QaAnswer,
            messages=request_messages,
            recovery=context.recovery,
        )
    else:
        answer = await context.llm.complete_prose(
            context.conn,
            run_id=context.state.run_id,
            node_execution_id=context.audit_execution_id,
            model=model,
            reasoning=role == Role.REASONING,
            output=QaAnswer,
            messages=request_messages,
            recovery=context.recovery,
            sink=context.prose_sink,
        )
    if not set(answer.references) <= context.visible_refs:
        raise LlmOutputError(
            Failure(
                error_class="tool_param",
                message="Q&A cited unavailable artifacts",
                field_errors={"references": "cites artifacts unavailable to this question"},
            )
        )
    return ControlResponse(category="qa", text=answer.text, references=answer.references)


async def explain_status(
    message: str, context: ControlContext, *, polish: bool = False
) -> ControlResponse:
    facts = status_facts(context)
    template = status_template(facts)
    text = template
    if polish:
        model = context.router.model_for("status_explainer")
        request_messages = (
            {
                "role": "system",
                "content": "Add brief explanatory context only. "
                "Canonical status, progress and ETA are "
                "delivered separately: do not restate or replace them or invent timing.",
            },
            {"role": "user", "content": template + "\n\n" + message},
        )
        if context.prose_sink is None:
            result = await context.llm.complete(
                context.conn,
                run_id=context.state.run_id,
                node_execution_id=context.audit_execution_id,
                model=model,
                reasoning=False,
                output=ControlText,
                messages=request_messages,
                recovery=context.recovery,
            )
        else:
            result = await context.llm.complete_prose(
                context.conn,
                run_id=context.state.run_id,
                node_execution_id=context.audit_execution_id,
                model=model,
                reasoning=False,
                output=ControlText,
                messages=request_messages,
                recovery=context.recovery,
                sink=context.prose_sink,
            )
        text = result.text
    return ControlResponse(category="status", text=text, facts=facts, template=template)
