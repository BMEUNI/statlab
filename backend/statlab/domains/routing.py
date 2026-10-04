"""Tabular domain and control roles; Q&A uses the current node's role."""

from statlab.llm.routing import Role

NODE_ROLES: dict[str, Role] = {
    "problem_definition": Role.REASONING,
    "experiment_design": Role.REASONING,
    "inferential_analysis": Role.REASONING,
    "model_interpretation": Role.REASONING,
    "champion_selection": Role.REASONING,
    "interrupt_router": Role.CLASSIFY,
    "status_explainer": Role.CLASSIFY,
}
