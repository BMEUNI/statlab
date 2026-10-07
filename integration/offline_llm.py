"""Deterministic stand-in for litellm.acompletion (schema-valid decisions, no data judgement)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from litellm import ModelResponse
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage
from openai.types.completion_usage import PromptTokensDetails
from pydantic import JsonValue
from statlab_contracts.candidate import (
    CandidateSet,
    CandidateSpec,
    EstimatorSpec,
    changed_dimensions,
)
from statlab_contracts.contract import DataContract

from integration.model import Scenario

RATIONALE = "Offline stand-in decision."

_RATIONALE_ONLY = frozenset(
    {
        "IntakeDecision",
        "ProblemDecision",
        "ProfileDecision",
        "ProfileFindings",
        "SplitDecision",
        "PreprocessingDecision",
        "FeatureDecision",
        "TrainingDecision",
        "EvaluationActions",
        "ValidationConclusion",
        "InterpretationDecision",
        "SearchDecision",
    }
)
_DEFAULT_INFERENTIAL = {
    "continuous": "ols",
    "binary": "logit",
    "multiclass": "mnlogit",
    "count": "glm_poisson",
}


class _Stream:
    def __init__(self, content: str, model: str) -> None:
        self._content = content
        self._model = model

    def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[ModelResponseStream]:
        half = len(self._content) // 2
        for piece in (self._content[:half], self._content[half:]):
            yield ModelResponseStream(
                choices=[StreamingChoices(index=0, delta=Delta(content=piece))],
                model=self._model,
            )
        yield ModelResponseStream(
            choices=[],
            model=self._model,
            usage=Usage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                prompt_tokens_details=PromptTokensDetails(cached_tokens=0),
            ),
        )

    async def aclose(self) -> None:
        return None


def _design(contract: DataContract, scenario: Scenario, user_text: str) -> dict[str, JsonValue]:
    revised = scenario.offline.revised_candidates
    if revised and "Revise the typed proposal" in user_text:
        names = revised
    else:
        names = contract.modelling.candidates
    if not names:
        raise RuntimeError("offline provider: contract has no candidates")
    base = CandidateSpec(
        label=names[0],
        rationale=f"Offline stand-in candidate {names[0]}.",
        is_baseline=True,
        estimator=EstimatorSpec(name=names[0]),
    )
    others: list[CandidateSpec] = []
    for name in names[1:]:
        draft = CandidateSpec(
            label=name,
            rationale=f"Offline stand-in candidate {name}.",
            estimator=EstimatorSpec(name=name),
        )
        others.append(
            CandidateSpec(
                label=name,
                rationale=f"Offline stand-in candidate {name}.",
                estimator=EstimatorSpec(name=name),
                varies_from_baseline=tuple(sorted(changed_dimensions(base, draft))),
            )
        )
    candidate_set = CandidateSet(candidates=(base, *others))
    return {"rationale": RATIONALE, "candidate_set": candidate_set.model_dump(mode="json")}


def _inference(contract: DataContract, scenario: Scenario) -> dict[str, JsonValue]:
    model = contract.modelling.inferential_model
    if model is None:
        target_type = contract.target.type
        if target_type is None:
            raise RuntimeError("offline provider: contract target has no type")
        model = _DEFAULT_INFERENTIAL[target_type]
    predictors: list[str] = []
    for goal in contract.objective.inference_goals:
        for predictor in goal.predictors:
            if predictor not in predictors:
                predictors.append(predictor)
    reference_levels: dict[str, JsonValue] = {
        k: v for k, v in scenario.offline.reference_levels.items() if k in predictors
    }
    return {
        "rationale": RATIONALE,
        "model": model,
        "predictors": list(predictors),
        "reference_levels": reference_levels,
    }


def _champion(user_text: str) -> dict[str, JsonValue]:
    start = user_text.find('"ranking"')
    match = re.search(
        r'"candidate"\s*:\s*"(c_\d{2,})"', user_text[start:] if start >= 0 else user_text
    )
    if match is None:
        match = re.search(r"\b(c_\d{2,})\b", user_text)
    chosen = match.group(1) if match else "c_01"
    return {"candidate_id": chosen, "rationale": RATIONALE}


class OfflineProvider:
    def __init__(self) -> None:
        self.scenario: Scenario | None = None

    async def __call__(self, **kwargs: object) -> object:
        raw = kwargs["messages"]
        if not isinstance(raw, list):
            raise RuntimeError("offline provider: malformed messages")
        messages: list[dict[str, str]] = []
        for item in raw:
            if not (
                isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("content"), str)
            ):
                raise RuntimeError("offline provider: malformed messages")
            messages.append({"role": item["role"], "content": item["content"]})
        model = str(kwargs["model"])
        system = messages[0]["content"]
        marker = "\nSchema: "
        index = system.rfind(marker)
        if index == -1:
            raise RuntimeError("offline provider: request has no schema")
        schema = json.loads(system[index + len(marker) :])
        title = str(schema["title"])
        user_text = "\n".join(m["content"] for m in messages if m["role"] == "user")
        scenario = self.scenario
        if scenario is None:
            raise RuntimeError("offline provider: no active scenario")
        content = json.dumps(self._answer(title, user_text, scenario))
        if kwargs.get("stream") is True:
            return _Stream(content, model)
        return ModelResponse(
            choices=[
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            model=model,
            usage={
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        )

    def _answer(self, title: str, user_text: str, scenario: Scenario) -> dict[str, JsonValue]:
        contract = DataContract.model_validate(scenario.contract)
        if title in _RATIONALE_ONLY:
            return {"rationale": RATIONALE}
        if title == "DesignDecision":
            return _design(contract, scenario, user_text)
        if title == "InferenceDecision":
            return _inference(contract, scenario)
        if title == "ChampionChoice":
            return _champion(user_text)
        if title == "InterruptClass":
            category = "status" if "current status" in user_text.lower() else "qa"
            return {"category": category, "lifecycle_action": None}
        if title == "ControlText":
            return {"text": "Offline stand-in reply."}
        if title == "QaAnswer":
            return {"text": "Offline stand-in answer.", "references": []}
        if title == "ContractProposal":
            return {"contract": scenario.contract, "reasoning": "Offline stand-in proposal."}
        raise RuntimeError(f"offline provider has no answer for {title}")
