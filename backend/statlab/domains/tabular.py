"""Normative tabular spine; deferred declarations are never executable here."""

from pathlib import Path

from statlab_contracts.contract import DataContract

from statlab.domains.base import Domain, NodeSpec
from statlab.domains.decisions import (
    ChampionChoice,
    DesignDecision,
    EvaluationDecision,
    FeatureDecision,
    InferenceDecision,
    IntakeDecision,
    InterpretationDecision,
    PersistenceDecision,
    PreprocessingDecision,
    ProblemDecision,
    ProfileDecision,
    SplitDecision,
    TrainingDecision,
)
from statlab.graph.candidates import TraversalPlan

TABULAR_SPINE = (
    NodeSpec("intake", "SHARED", False, IntakeDecision, ("intake",), "always"),
    NodeSpec("problem_definition", "SHARED", False, ProblemDecision, (), "always"),
    NodeSpec(
        "data_profiling", "SHARED", True, ProfileDecision, ("profiling", "cleaning"), "always"
    ),
    NodeSpec("data_splitting", "SHARED", False, SplitDecision, ("cleaning", "splitting"), "always"),
    NodeSpec("experiment_design", "SHARED", False, DesignDecision, ("training",), "always"),
    NodeSpec(
        "inferential_analysis",
        "SHARED",
        True,
        InferenceDecision,
        ("inference_matrix", "inference"),
        "always",
    ),
    NodeSpec(
        "preprocessing", "PER_CANDIDATE", True, PreprocessingDecision, ("cleaning",), "exception"
    ),
    NodeSpec(
        "feature_engineering",
        "PER_CANDIDATE",
        True,
        FeatureDecision,
        ("encoding", "features"),
        "exception",
    ),
    NodeSpec(
        "model_training", "PER_CANDIDATE", False, TrainingDecision, ("training",), "exception"
    ),
    NodeSpec(
        "model_evaluation",
        "PER_CANDIDATE",
        False,
        EvaluationDecision,
        ("evaluation",),
        "exception",
    ),
    NodeSpec(
        "model_interpretation",
        "PER_CANDIDATE",
        True,
        InterpretationDecision,
        ("interpretation",),
        "exception",
    ),
    NodeSpec(
        "champion_selection",
        "SHARED",
        False,
        ChampionChoice,
        ("champion", "evaluation", "reporting"),
        "always",
    ),
    NodeSpec(
        "artifact_persistence", "SHARED", False, PersistenceDecision, ("artifacts",), "always"
    ),
)

TABULAR_TRAVERSAL = TraversalPlan(
    overlap_shared="inferential_analysis",
    design_node="experiment_design",
    training_node="model_training",
    evaluation_node="model_evaluation",
)

TABULAR_DOMAIN = Domain(
    name="tabular_supervised",
    contract_model=DataContract,
    skill_file=Path("skills/modelling.md"),
    tool_phases=tuple(dict.fromkeys(phase for node in TABULAR_SPINE for phase in node.tool_phases)),
    nodes=TABULAR_SPINE,
)

# The earliest consumer, so a fork from there re-runs everything the setting can affect.
SETTING_OWNERS: dict[str, str] = {
    "target": "intake",
    "dataset": "intake",
    "features": "intake",
    "objective": "problem_definition",
    "notes": "problem_definition",
    "split": "data_splitting",
    "preprocessing": "experiment_design",
    "modelling": "experiment_design",
    "constraints": "experiment_design",
}
