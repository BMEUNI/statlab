"""Generic role routing and offline selected-provider startup validation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values

from statlab.errors import ConfigError


class Role(StrEnum):
    DEFAULT = "default"
    REASONING = "reasoning"
    CLASSIFY = "classify"


MODELS: Mapping[Role, str] = {
    Role.DEFAULT: "deepseek/deepseek-flash",
    Role.REASONING: "deepseek/deepseek-v4-pro",
    Role.CLASSIFY: "deepseek/deepseek-flash",
}


def load_routing_environment(
    env_file: Path = Path(".env"), *, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    try:
        model_keys = {f"STATLAB_MODEL_{role.name}" for role in Role}
        values = {
            key: value if value is not None else ""
            for key, value in dotenv_values(env_file).items()
            if value is not None or key in model_keys
        }
    except OSError, UnicodeError:
        raise ConfigError("could not read routing environment") from None
    values.update(os.environ if environment is None else environment)
    return values


class ModelRouter:
    """Skill literal/role, role environment, role default, then DEFAULT fallback."""

    def __init__(
        self,
        node_roles: Mapping[str, Role],
        environment: Mapping[str, str],
        *,
        models: Mapping[Role, str] = MODELS,
    ) -> None:
        self._node_roles = dict(node_roles)
        self._models: dict[Role, str] = {}
        for role in Role:
            key = f"STATLAB_MODEL_{role.name}"
            model = environment.get(key, models.get(role, models[Role.DEFAULT]))
            if not model.strip():
                raise ConfigError("model override must not be blank")
            self._models[role] = model

    def role_for(self, node: str) -> Role:
        return self._node_roles.get(node, Role.DEFAULT)

    def model_for(self, node: str, *, skill_model: str | None = None) -> str:
        if skill_model is not None:
            if not skill_model.strip():
                raise ConfigError("skill model must not be blank")
            try:
                role = Role(skill_model)
            except ValueError:
                return skill_model
        else:
            role = self.role_for(node)
        return self._models[role]

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(self._models.values())


_SDK_CHECK = """
import json,os,sys
import litellm
missing=set()
try:
    for model in json.loads(sys.stdin.read()):
        litellm.get_llm_provider(model=model)
        environment=os.environ.copy()
        try:
            os.environ.clear()
            required=litellm.validate_environment(model=model)
        finally:
            os.environ.update(environment)
        if required['keys_in_environment'] or not required['missing_keys']:
            sys.exit(3)
        result=litellm.validate_environment(model=model)
        missing.update(result['missing_keys'])
    print(json.dumps(sorted(missing)))
except Exception:
    sys.exit(2)
"""


def check_provider_environment(
    models: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    env_file: Path = Path(".env"),
) -> None:
    """Use SDK alternative-credential rules with absent blanks and no network probe."""
    values = load_routing_environment(env_file, environment=environment)
    values = {key: value for key, value in values.items() if value.strip()}
    values["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    values["LITELLM_MODE"] = "PRODUCTION"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter/helper, models travel on stdin.
            [sys.executable, "-c", _SDK_CHECK],
            input=json.dumps(tuple(models)),
            env=values,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode == 3:
            raise ConfigError("startup environment validation unavailable for selected provider")
        if completed.returncode != 0:
            raise ConfigError("provider configuration could not be validated")
        missing = tuple(json.loads(completed.stdout))
    except OSError, subprocess.TimeoutExpired, ValueError:
        raise ConfigError("provider configuration could not be validated") from None
    if missing:
        raise ConfigError(
            "missing or blank provider environment: " + ", ".join(missing), missing=missing
        )


def check_startup_environment() -> None:
    environment = load_routing_environment()
    router = ModelRouter({}, environment)
    check_provider_environment(router.models, environment=environment)


if __name__ == "__main__":
    check_startup_environment()
    print("LLM config OK")
