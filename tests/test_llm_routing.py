"""Role precedence and SDK-only offline startup checks."""

import subprocess
from pathlib import Path

import pytest
from statlab.domains.routing import NODE_ROLES
from statlab.errors import ConfigError
from statlab.llm.routing import (
    MODELS,
    ModelRouter,
    Role,
    check_provider_environment,
    check_startup_environment,
    load_routing_environment,
)


def test_precedence_and_domain_roles() -> None:
    router = ModelRouter(NODE_ROLES, {"STATLAB_MODEL_REASONING": "vendor/env"})
    expected = {
        "problem_definition",
        "experiment_design",
        "inferential_analysis",
        "model_interpretation",
        "champion_selection",
    }
    assert {node for node, role in NODE_ROLES.items() if role == Role.REASONING} == expected
    assert (
        router.role_for("interrupt_router") == router.role_for("status_explainer") == Role.CLASSIFY
    )
    assert router.model_for("unknown") == MODELS[Role.DEFAULT]
    for node in expected:
        assert router.model_for(node) == "vendor/env"
        assert router.model_for(node, skill_model="classify") == MODELS[Role.CLASSIFY]
        assert router.model_for(node, skill_model="vendor/literal") == "vendor/literal"
    fallback = ModelRouter({"node": Role.REASONING}, {}, models={Role.DEFAULT: "vendor/default"})
    assert fallback.model_for("node") == "vendor/default"
    assert ModelRouter(NODE_ROLES, {}).model_for("experiment_design") == MODELS[Role.REASONING]


@pytest.mark.parametrize("role", tuple(Role))
def test_blank_environment_override(role: Role) -> None:
    with pytest.raises(ConfigError, match="blank"):
        ModelRouter({}, {f"STATLAB_MODEL_{role.name}": " "})


def test_blank_skill_override() -> None:
    with pytest.raises(ConfigError, match="blank"):
        ModelRouter({}, {}).model_for("unknown", skill_model="")


def test_env_precedence(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("STATLAB_MODEL_DEFAULT=vendor/file\nDEEPSEEK_API_KEY=file-key\nUNSET\n")
    values = load_routing_environment(path, environment={"STATLAB_MODEL_DEFAULT": "vendor/process"})
    assert ModelRouter({}, values).model_for("unknown") == "vendor/process"
    assert values["DEEPSEEK_API_KEY"] == "file-key"
    assert "UNSET" not in values
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="read"):
        load_routing_environment(path, environment={})


@pytest.mark.parametrize("role", tuple(Role))
def test_valueless_file_model_override(role: Role, tmp_path: Path) -> None:
    path = tmp_path / ".env"
    key = f"STATLAB_MODEL_{role.name}"
    path.write_text(f"{key}\nUNSET\n")
    values = load_routing_environment(path, environment={})
    assert values[key] == ""
    assert "UNSET" not in values
    with pytest.raises(ConfigError, match="blank"):
        ModelRouter({}, values)
    overridden = load_routing_environment(path, environment={key: "vendor/process"})
    assert ModelRouter({"node": role}, overridden).model_for("node") == "vendor/process"


@pytest.mark.parametrize("credential", [None, "", " \t"])
def test_missing_blank_provider_credential(credential: str | None, tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=file-secret\n")
    environment = {} if credential is None else {"DEEPSEEK_API_KEY": credential}
    if credential is None:
        path.write_text("")
    with pytest.raises(ConfigError) as caught:
        check_provider_environment([MODELS[Role.DEFAULT]], env_file=path, environment=environment)
    assert caught.value.missing == ("DEEPSEEK_API_KEY",)
    assert "file-secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_selected_only_and_alternative_credentials(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    check_provider_environment(
        [MODELS[Role.DEFAULT]], env_file=path, environment={"DEEPSEEK_API_KEY": "secret"}
    )
    check_provider_environment(
        ["anthropic/claude-sonnet-4"],
        env_file=path,
        environment={"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "secret"},
    )
    with pytest.raises(ConfigError) as caught:
        check_provider_environment(
            [MODELS[Role.DEFAULT], "openai/gpt-4o-mini"],
            env_file=path,
            environment={"DEEPSEEK_API_KEY": "secret"},
        )
    assert caught.value.missing == ("OPENAI_API_KEY",)
    with pytest.raises(ConfigError, match="could not be validated") as caught:
        check_provider_environment(["unknownvendor/secret-model"], environment={}, env_file=path)
    assert "secret-model" not in str(caught.value)


def test_startup_uses_three_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    check_startup_environment()


@pytest.mark.parametrize(
    "failure",
    [
        OSError("secret process detail"),
        subprocess.TimeoutExpired("secret command", 30),
    ],
)
def test_startup_process_failures_are_safe(
    failure: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(*args: object, **kwargs: object) -> object:
        raise failure

    monkeypatch.setattr("statlab.llm.routing.subprocess.run", failed)
    with pytest.raises(ConfigError, match="could not be validated") as caught:
        check_provider_environment(
            [MODELS[Role.DEFAULT]], environment={}, env_file=tmp_path / "absent.env"
        )
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_sdk_unverified_provider_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="validation unavailable") as caught:
        check_provider_environment(
            ["databricks/secret-model"], environment={}, env_file=tmp_path / "absent.env"
        )
    assert "secret-model" not in str(caught.value)
    assert caught.value.__cause__ is None
