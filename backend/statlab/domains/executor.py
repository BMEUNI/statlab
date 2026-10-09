"""Caller-owned dispatcher for the declared tabular spine."""

from statlab.domains.base import NodeResult
from statlab.domains.candidate_models import CandidateModelExecutor
from statlab.domains.candidate_pipeline import CandidatePipelineExecutor
from statlab.domains.champion import ChampionExecutor
from statlab.domains.design_inference import DesignInferenceExecutor
from statlab.domains.foundation import FoundationExecutor
from statlab.domains.persistence import PersistenceExecutor
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError
from statlab.graph.engine import NodeExecutor, NodeInvocation


class TabularExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        foundation = FoundationExecutor(runtime)
        design = DesignInferenceExecutor(runtime)
        pipeline = CandidatePipelineExecutor(runtime)
        models = CandidateModelExecutor(runtime)
        champion = ChampionExecutor(runtime)
        persistence = PersistenceExecutor(runtime)
        self.executors: dict[str, NodeExecutor] = {
            "intake": foundation,
            "problem_definition": foundation,
            "data_profiling": foundation,
            "data_quality": foundation,
            "data_splitting": foundation,
            "experiment_design": design,
            "inferential_analysis": design,
            "preprocessing": pipeline,
            "feature_engineering": pipeline,
            "model_training": models,
            "model_evaluation": models,
            "model_interpretation": models,
            "champion_selection": champion,
            "artifact_persistence": persistence,
        }

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        executor = self.executors.get(invocation.spec.name)
        if executor is None:
            raise EngineError("unknown tabular node")
        return await executor(invocation)
