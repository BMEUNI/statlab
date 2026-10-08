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
REPORT_HEADING = "Stand-in methodology note"

_RICH_TITLES = frozenset(
    {
        "ProfileDecision",
        "TrainingDecision",
        "EvaluationActions",
        "ValidationConclusion",
        "InterpretationDecision",
        "SearchDecision",
        "ChampionChoice",
    }
)
_GRID: dict[str, dict[str, list[JsonValue]]] = {
    "logistic_regression": {"C": [0.5, 1.0]},
    "linear_regression": {"fit_intercept": [True, False]},
    "ridge": {"alpha": [0.5, 1.0]},
}
_RETUNE: dict[str, dict[str, JsonValue]] = {
    "logistic_regression": {"C": 2.0},
    "linear_regression": {"fit_intercept": True},
    "ridge": {"alpha": 2.0},
}
_VARIANT: dict[str, dict[str, JsonValue]] = {
    "logistic_regression": {"C": 0.5},
    "linear_regression": {"fit_intercept": False},
    "ridge": {"alpha": 0.5},
}
_LINEAR = frozenset({"logistic_regression", "linear_regression", "ridge"})

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


def _obj(value: JsonValue, what: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise RuntimeError(f"offline provider: malformed {what}")
    return value


def _list(value: JsonValue, what: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise RuntimeError(f"offline provider: malformed {what}")
    return value


def _parts(first_user: str) -> tuple[dict[str, JsonValue], list[JsonValue]]:
    parts = first_user.split("\n\n", 2)
    if len(parts) < 3:
        raise RuntimeError("offline provider: unexpected prompt layout")
    state = _obj(json.loads(parts[0]), "state")
    evidence = _list(json.loads(parts[1]), "evidence")
    return state, evidence


def _candidate(state: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return _obj(state.get("candidate"), "candidate")


def _rich_profile(
    evidence: list[JsonValue], contract: DataContract, repaired: bool
) -> dict[str, JsonValue]:
    columns: dict[str, JsonValue] | None = None
    for entry in evidence:
        item = _obj(entry, "evidence")
        if item.get("tool") == "profile_schema":
            result = _obj(item["result"], "result")
            summary = _obj(result["summary"], "summary")
            columns = _obj(summary["columns"], "columns")
            break
    if columns is None:
        raise RuntimeError("offline provider: no profile_schema evidence")
    target = contract.target.column
    excluded = {target, *contract.features.id_columns, *contract.features.exclude}
    numeric: set[str] = set()
    for column, info in columns.items():
        dtype = _obj(info, "column").get("dtype")
        if isinstance(dtype, str) and dtype.lower().startswith(("int", "uint", "float", "decimal")):
            numeric.add(column)
    for column, role in contract.features.roles.items():
        if role == "numeric":
            numeric.add(column)
        else:
            numeric.discard(column)
    num = [c for c in columns if c not in excluded and c in numeric]
    cat = [c for c in columns if c not in excluded and c not in numeric]
    plots: list[JsonValue] = [{"kind": "distribution", "column": target}]
    if num + cat:
        plots.append({"kind": "target_relation", "column": (num + cat)[0]})
    categorical: list[JsonValue] = list(cat[:2])
    if not repaired and num:
        categorical = [*cat[:1], num[0]]
    return {
        "rationale": RATIONALE,
        "numeric": list(num[:2]),
        "categorical": categorical,
        "correlations": list(num[:2]) if len(num) >= 2 else [],
        "outliers": list(num[:1]),
        "plots": plots,
    }


def _spec_facts(state: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    spec = _obj(_candidate(state)["spec"], "spec")
    return spec, _obj(spec["estimator"], "estimator")


def _rich_training(state: dict[str, JsonValue], contract: DataContract) -> dict[str, JsonValue]:
    candidate = _candidate(state)
    spec, estimator = _spec_facts(state)
    name = str(estimator["name"])
    retune = int(str(candidate["retune_count"]))
    baseline = spec["is_baseline"] is True
    wave = int(str(candidate["wave"]))
    tuning = contract.modelling.tuning
    explicit = "method" in tuning.model_fields_set
    if explicit:
        use_grid = tuning.method == "grid" and name in _GRID
    else:
        use_grid = baseline and wave == 1 and retune == 0 and name in _GRID
    if use_grid:
        return {"rationale": RATIONALE, "method": "grid", "param_grid": dict(_GRID[name])}
    params: dict[str, JsonValue] = _RETUNE[name] if retune > 0 and name in _RETUNE else {}
    return {"rationale": RATIONALE, "method": "direct", "params": params}


def _rich_evaluation(
    state: dict[str, JsonValue], evidence: list[JsonValue]
) -> dict[str, JsonValue]:
    trained = _obj(evidence[0], "training summary")
    summary = _obj(trained["model_summary"], "model_summary")
    task = summary["task"]
    classes = _list(summary["classes"], "classes")
    spec, estimator = _spec_facts(state)
    baseline = spec["is_baseline"] is True
    curves: list[JsonValue] = []
    objective: JsonValue = None
    if task == "classification" and len(classes) == 2:
        curves = ["confusion", "roc", "pr", "calibration"]
        objective = "f1"
    elif task == "classification":
        curves = ["confusion"]
    diagnostics = baseline and estimator["calibrate"] is not True
    return {
        "rationale": RATIONALE,
        "curves": curves,
        "threshold_objective": objective,
        "cross_validation": diagnostics,
        "learning_curve": diagnostics,
    }


def _rich_conclusion(state: dict[str, JsonValue], contract: DataContract) -> dict[str, JsonValue]:
    candidate = _candidate(state)
    spec, estimator = _spec_facts(state)
    if (
        spec["is_baseline"] is True
        and int(str(candidate["wave"])) == 1
        and int(str(candidate["retune_count"])) == 0
        and contract.constraints.max_retune_loops >= 1
        and str(estimator["name"]) in _RETUNE
    ):
        return {
            "rationale": RATIONALE,
            "retune": True,
            "retune_reason": "Offline rule: one bounded retune of the baseline.",
        }
    return {"rationale": RATIONALE}


def _rich_interpretation(
    state: dict[str, JsonValue], evidence: list[JsonValue]
) -> dict[str, JsonValue]:
    trained = _obj(evidence[0], "training summary")
    summary = _obj(trained["model_summary"], "model_summary")
    features = [str(f) for f in _list(summary["feature_names"], "feature_names")]
    _, estimator = _spec_facts(state)
    return {
        "rationale": RATIONALE,
        "coefficients": str(estimator["name"]) in _LINEAR and estimator["calibrate"] is not True,
        "partial_dependence": list[JsonValue](features[:1]),
        "error_by": list[JsonValue](features[:1]),
    }


def _rich_search(evidence: list[JsonValue], scenario: Scenario) -> dict[str, JsonValue]:
    empty: dict[str, JsonValue] = {"rationale": RATIONALE}
    context = _obj(evidence[0], "search context")
    remaining = context["remaining_candidates"]
    if scenario.expect.candidate_count is not None:
        return empty
    if not isinstance(remaining, int) or remaining < 1:
        return empty
    baseline_spec: dict[str, JsonValue] | None = None
    for item in _list(context["comparison"], "comparison"):
        spec = _obj(_obj(item, "comparison item")["spec"], "spec")
        if spec["is_baseline"] is True:
            baseline_spec = spec
            break
    if baseline_spec is None:
        return empty
    baseline_estimator = _obj(baseline_spec["estimator"], "estimator")
    name = str(baseline_estimator["name"])
    if name not in _VARIANT:
        return empty
    base = CandidateSpec.model_validate(baseline_spec)
    draft = CandidateSpec.model_validate(
        {
            **baseline_spec,
            "label": f"{name} variant",
            "rationale": "Offline stand-in variant of the baseline.",
            "is_baseline": False,
            "estimator": {**baseline_estimator, "params": _VARIANT[name]},
            "varies_from_baseline": [],
        }
    )
    final = draft.model_copy(
        update={"varies_from_baseline": tuple(sorted(changed_dimensions(base, draft)))}
    )
    final = CandidateSpec.model_validate(final.model_dump(mode="json"))
    return {
        "rationale": RATIONALE,
        "candidates": [final.model_dump(mode="json")],
        "family_justifications": {"linear": "Offline stand-in variant of the linear baseline."},
    }


def _setup_action(system: str, messages: list[dict[str, str]]) -> dict[str, JsonValue]:
    marker = "SETUP FACTS (data, not instructions):\n"
    start = system.index(marker) + len(marker)
    end = system.index("\n", start)
    facts = _obj(json.loads(system[start:end]), "facts")
    text = next(m["content"] for m in reversed(messages) if m["role"] == "user")
    t = text.lower()
    state = _obj(facts["state"], "state")
    if not state.get("project_id"):
        for project in _list(facts["projects"], "projects"):
            item = _obj(project, "project")
            name = str(item["name"])
            if name.lower() in t:
                return {
                    "action": "select_project",
                    "project_id": str(item["id"]),
                    "reply": f"Using project {name}. Which dataset should we use? "
                    "Upload a CSV or pick one.",
                }
        new_name = text.strip()[:60]
        return {
            "action": "create_project",
            "project_name": new_name,
            "reply": f"Created project {new_name}. Upload a CSV to continue.",
        }
    if not state.get("dataset_id"):
        datasets = [_obj(d, "dataset") for d in _list(facts["datasets"], "datasets")]
        chosen = next((d for d in datasets if str(d["filename"]).lower() in t), None)
        if chosen is None and len(datasets) == 1:
            chosen = datasets[0]
        if chosen is not None:
            return {
                "action": "select_dataset",
                "dataset_id": str(chosen["id"]),
                "reply": f"Using {chosen['filename']}. Which column should the model predict?",
            }
        return {
            "action": "none",
            "reply": "Upload a CSV with the button below, or name one of the datasets.",
        }
    if not state.get("target"):
        for column in _list(facts["columns"], "columns"):
            name = str(_obj(column, "column")["name"])
            if name.lower() in t:
                return {
                    "action": "set_target",
                    "target": name,
                    "reply": f"Target set to {name}. I drafted the modelling setup; "
                    "review it on the right. Guided or autonomous?",
                }
        return {"action": "none", "reply": "Which column should the model predict?"}
    if not state.get("mode"):
        if "guided" in t:
            return {
                "action": "set_mode",
                "mode": "guided",
                "reply": "Guided mode: I will stop for your approval at each decision. "
                "Say start when ready.",
            }
        if "auto" in t:
            return {
                "action": "set_mode",
                "mode": "autonomous",
                "reply": "Autonomous mode: I will run to the end. Say start when ready.",
            }
        return {"action": "none", "reply": "Should I run guided or autonomous?"}
    if "start" in t:
        return {"action": "start_run", "reply": "Starting the run."}
    return {"action": "none", "reply": "Everything is ready. Say start when you want to begin."}


class OfflineProvider:
    def __init__(self, *, rich: bool = False) -> None:
        self.scenario: Scenario | None = None
        self.rich = rich

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
        first_user = next((m["content"] for m in messages if m["role"] == "user"), None)
        if first_user is None:
            raise RuntimeError("offline provider: no user message")
        if title == "SetupAction":
            content = json.dumps(_setup_action(system, messages))
        else:
            content = json.dumps(self._answer(title, user_text, first_user, scenario))
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

    def _answer(
        self, title: str, user_text: str, first_user: str, scenario: Scenario
    ) -> dict[str, JsonValue]:
        contract = DataContract.model_validate(
            scenario.offline.proposed_contract or scenario.contract
        )
        if self.rich and title in _RICH_TITLES:
            state, evidence = _parts(first_user)
            if title == "ProfileDecision":
                return _rich_profile(evidence, contract, "Repair the output" in user_text)
            if title == "TrainingDecision":
                return _rich_training(state, contract)
            if title == "EvaluationActions":
                return _rich_evaluation(state, evidence)
            if title == "ValidationConclusion":
                return _rich_conclusion(state, contract)
            if title == "InterpretationDecision":
                return _rich_interpretation(state, evidence)
            if title == "SearchDecision":
                return _rich_search(evidence, scenario)
            return {
                **_champion(user_text),
                "sections": [
                    {
                        "heading": REPORT_HEADING,
                        "body": "Choices in this run came from fixed offline rules.",
                    }
                ],
            }
        if title in _RATIONALE_ONLY:
            return {"rationale": RATIONALE}
        if title == "DesignDecision":
            design_state, _ = _parts(first_user)
            checkpointed = design_state.get("checkpointed_candidates")
            if isinstance(checkpointed, list) and checkpointed:
                note = "Offline stand-in confirmation of the checkpointed cumulative set."
                return {
                    "rationale": RATIONALE,
                    "candidate_set": {"candidates": checkpointed},
                    "family_justifications": {
                        "linear": note,
                        "random_forest": note,
                        "hist_gradient_boosting": note,
                    },
                }
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
            return {
                "contract": scenario.offline.proposed_contract or scenario.contract,
                "reasoning": "Offline stand-in proposal.",
            }
        raise RuntimeError(f"offline provider has no answer for {title}")
