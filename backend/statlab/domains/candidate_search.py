"""Bounded candidate waves, audited through an existing saved design execution."""

from __future__ import annotations

import logging

from pydantic import JsonValue, ValidationError
from statlab_contracts.candidate import CandidateSet, CandidateSpec

from statlab.domains.candidate_pipeline import candidate_result
from statlab.domains.decisions import DesignDecision, SearchDecision
from statlab.domains.design_inference import (
    VARIATION_RULE,
    DesignInferenceExecutor,
    initial_candidate_planner,
)
from statlab.domains.foundation import (
    _object,
    _RefusalError,
    _rejecting,
    problem_from_state,
    shared_result,
)
from statlab.domains.runtime import DomainRuntime
from statlab.domains.tabular import TABULAR_SPINE
from statlab.errors import EngineError, LlmOutputError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import RunState
from statlab.persistence.repository import list_node_executions

_LOGGER = logging.getLogger(__name__)


class CandidateSearchPlanner:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        if wave == 1:
            return await initial_candidate_planner(state, wave)
        limits = self.runtime.contract.constraints
        if (
            wave > limits.max_search_waves
            or len(state.branches) >= limits.max_candidates
            or any(branch.wave >= wave for branch in state.branches)
        ):
            return ()
        if wave != state.wave + 1:
            raise EngineError("search wave must follow the completed checkpoint wave")
        if state.budget is not None and state.budget.exhausted:
            return ()
        completed = tuple(branch for branch in state.branches if branch.status == "completed")
        if any(
            branch.status in ("pending", "running", "awaiting_user") for branch in state.branches
        ):
            raise EngineError("later-wave planning requires a joined candidate comparison")
        if not completed:
            return ()
        design = shared_result(state, "experiment_design")
        boundary = next(
            boundary
            for boundary in reversed(state.shared_results)
            if boundary.node_name == "experiment_design" and boundary.result.status == "completed"
        )
        execution = next(
            (
                row
                for row in list_node_executions(self.runtime.conn, state.run_id)
                if row.id == boundary.execution_id
            ),
            None,
        )
        if (
            execution is None
            or execution.status != "completed"
            or execution.candidate_id is not None
        ):
            raise EngineError(
                "candidate planning requires its persisted successful design execution"
            )
        spec = next(spec for spec in TABULAR_SPINE if spec.name == "experiment_design")
        invocation = NodeInvocation(state, spec, execution, None)
        self.runtime.context(invocation)
        old = _object(design.summary.get("decision"), "saved design decision")
        prior = _object(old.get("family_justifications"), "saved family justifications")
        justifications = {name: value for name, value in prior.items() if isinstance(value, str)}
        registry = _object(design.summary.get("registry"), "design registry")
        problem = problem_from_state(state)
        existing = tuple(branch.spec for branch in state.branches)

        def cumulative(proposal: SearchDecision) -> None:
            if not proposal.candidates:
                return
            try:
                candidate_set = CandidateSet(candidates=(*existing, *proposal.candidates))
            except ValidationError as exc:
                reasons = "; ".join(
                    str(error["msg"])
                    for error in exc.errors(include_input=False, include_context=False)
                )
                raise _RefusalError(f"invalid cumulative candidate proposal: {reasons}") from exc
            DesignInferenceExecutor(self.runtime).validate_design(
                invocation,
                DesignDecision(
                    rationale=proposal.rationale,
                    candidate_set=candidate_set,
                    family_justifications={**justifications, **proposal.family_justifications},
                ),
                registry,
                problem,
                require_saved=False,
            )

        comparison: list[JsonValue] = [
            {
                "candidate": branch.public_id,
                "spec": branch.spec.model_dump(mode="json"),
                "validation": candidate_result(branch, "model_evaluation").summary,
                "retune_count": branch.retune_count,
            }
            for branch in completed
        ]
        try:
            proposal = await self.runtime.decide(
                invocation,
                SearchDecision,
                evidence=(
                    {
                        "comparison": comparison,
                        "next_wave": wave,
                        "remaining_candidates": limits.max_candidates - len(state.branches),
                        "limits": limits.model_dump(mode="json"),
                        "budget": state.budget.model_dump(mode="json") if state.budget else None,
                    },
                ),
                question="Propose additional candidates justified by completed validation "
                "comparison. "
                "Preserve every old specification, baseline and split. Respect remaining "
                "candidate/fit/wave limits. Avoid duplicates; justify families above two. "
                "Empty proposal ends search. "
                "Never use test evidence. The engine saves this wave before cumulative approval. "
                "Estimator params hold single fixed values (never grids or lists). "
                + VARIATION_RULE,
                check=_rejecting(cumulative),
            )
        except LlmOutputError:
            _LOGGER.warning(
                "Run %s wave %d search proposal stayed invalid after repair; search ends",
                state.run_id,
                wave,
            )
            return ()
        if not proposal.candidates:
            return ()
        cumulative(proposal)
        return proposal.candidates
