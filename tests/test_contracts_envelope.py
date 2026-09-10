"""Tests for statlab_contracts.envelope: E1-E25 per plan/02_core_types.md §4.2."""

from __future__ import annotations

import subprocess
import sys
import typing
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef
from statlab_contracts.envelope import (
    RETRY_BUDGET,
    TOOL_RESULT_ADAPTER,
    ErrorClass,
    Failure,
    ToolFailure,
    ToolSuccess,
    is_retryable,
    retry_budget,
)

# --- ToolSuccess ---------------------------------------------------------------------------


def test_tool_success_empty_defaults() -> None:
    """E1."""
    result = ToolSuccess()
    assert result.ok is True
    assert result.summary == {}
    assert result.artifacts == ()
    assert result.warnings == ()


def test_tool_success_nested_summary_preserved() -> None:
    """E2."""
    summary: dict[str, JsonValue] = {"a": {"b": [1, 2.5, "x", None, True]}}
    result = ToolSuccess(summary=summary)
    assert result.summary == summary


def test_tool_success_artifacts_list_coerced_to_tuple(ref: ArtifactRef) -> None:
    """E3: asserts a wire behaviour — a JSON array is what actually arrives in production,
    passing a Python list by kwarg never happens."""
    meta = ArtifactMeta(ref=ref, kind="dataframe")
    result = ToolSuccess.model_validate({"artifacts": [meta.model_dump()]})
    assert isinstance(result.artifacts, tuple)
    assert result.artifacts == (meta,)


def test_tool_success_warnings_list_coerced_to_tuple() -> None:
    """E4: same rationale as E3."""
    result = ToolSuccess.model_validate({"warnings": ["careful"]})
    assert isinstance(result.warnings, tuple)
    assert result.warnings == ("careful",)


def test_tool_success_ok_false_rejected() -> None:
    """E5."""
    with pytest.raises(ValidationError):
        ToolSuccess.model_validate({"ok": False})


def test_tool_success_extra_field_rejected() -> None:
    """E6."""
    with pytest.raises(ValidationError):
        ToolSuccess(bogus=1)  # type: ignore[call-arg]  # deliberately invalid extra field under test


def test_tool_success_mutation_rejected() -> None:
    """E7."""
    result = ToolSuccess()
    with pytest.raises(ValidationError):
        result.summary = {"x": 1}  # type: ignore[misc]  # deliberately mutating a frozen model under test


def test_tool_success_artifacts_immutable() -> None:
    """E8."""
    result = ToolSuccess()
    assert isinstance(result.artifacts, tuple)
    with pytest.raises(AttributeError):
        result.artifacts.append(None)  # type: ignore[attr-defined]  # tuples have no append; asserting that fact


# --- Failure.field_errors rule -----------------------------------------------------------

_FIELD_ERRORS_MATRIX: list[tuple[ErrorClass, dict[str, str] | None, bool]] = [
    ("tool_param", {"k": "v"}, True),
    ("tool_param", None, False),
    ("tool_param", {}, False),
    ("transient", None, True),
    ("transient", {"k": "v"}, False),
    ("data", None, True),
    ("data", {"k": "v"}, False),
    ("bug", None, True),
]


@pytest.mark.parametrize(("error_class", "field_errors", "valid"), _FIELD_ERRORS_MATRIX)
def test_failure_field_errors_rule(
    error_class: ErrorClass, field_errors: dict[str, str] | None, valid: bool
) -> None:
    if valid:
        failure = Failure(error_class=error_class, message="m", field_errors=field_errors)
        assert failure.field_errors == field_errors
    else:
        with pytest.raises(ValidationError, match="field_errors"):
            Failure(error_class=error_class, message="m", field_errors=field_errors)


# --- No `retryable` field ------------------------------------------------------------------


def test_failure_retryable_kwarg_rejected() -> None:
    """E8a: `extra="forbid"` rejects the removed field, so a stale producer fails loudly
    rather than being silently ignored."""
    with pytest.raises(ValidationError):
        Failure(error_class="data", message="m", retryable=True)  # type: ignore[call-arg]  # deliberately passing the removed field under test


def test_failure_retryable_absent_from_serialised_forms() -> None:
    """E8b: `retryable` must not appear in either serialisation."""
    failure = Failure(error_class="data", message="m")
    assert "retryable" not in failure.model_dump()
    assert b"retryable" not in failure.model_dump_json().encode()


def test_failure_blank_message_rejected() -> None:
    """E9."""
    with pytest.raises(ValidationError):
        Failure(error_class="data", message="")


def test_failure_unknown_error_class_rejected() -> None:
    """E10."""
    with pytest.raises(ValidationError):
        Failure.model_validate({"error_class": "oops", "message": "m"})


def test_failure_empty_field_errors_on_tool_param_rejected() -> None:
    """E11."""
    with pytest.raises(ValidationError):
        Failure(error_class="tool_param", message="m", field_errors={})


def test_failure_field_errors_non_string_value_rejected() -> None:
    """E12."""
    with pytest.raises(ValidationError):
        Failure.model_validate(
            {"error_class": "tool_param", "message": "m", "field_errors": {"k": 1}}
        )


# --- RETRY_BUDGET / retry_budget / is_retryable --------------------------------------------


def test_retry_budget_exhaustive_over_error_class() -> None:
    """E13."""
    classes = set(typing.get_args(ErrorClass))
    assert set(RETRY_BUDGET.keys()) == classes


def test_retry_budget_values() -> None:
    """E14."""
    assert RETRY_BUDGET["transient"] == 3
    assert RETRY_BUDGET["tool_param"] == 2
    assert RETRY_BUDGET["data"] == 0
    assert RETRY_BUDGET["bug"] == 0


@pytest.mark.parametrize("error_class", typing.get_args(ErrorClass))
def test_retry_budget_function_delegates(error_class: ErrorClass) -> None:
    """E15."""
    assert retry_budget(error_class) == RETRY_BUDGET[error_class]


def test_is_retryable_values() -> None:
    """E15a."""
    assert is_retryable("transient") is True
    assert is_retryable("tool_param") is True
    assert is_retryable("data") is False
    assert is_retryable("bug") is False


@pytest.mark.parametrize("error_class", typing.get_args(ErrorClass))
def test_is_retryable_agrees_with_budget(error_class: ErrorClass) -> None:
    """E15b."""
    assert is_retryable(error_class) == (retry_budget(error_class) > 0)


def test_retry_budget_unknown_class_raises_key_error() -> None:
    """E15c: must not be silently defaulted to 0, which would make an unmapped class look
    non-retryable instead of failing."""
    with pytest.raises(KeyError):
        retry_budget("nonsense")  # type: ignore[arg-type]  # deliberately invalid literal under test


# --- TOOL_RESULT_ADAPTER discrimination ----------------------------------------------------


def test_tool_result_adapter_discriminates_success() -> None:
    """E16."""
    result = TOOL_RESULT_ADAPTER.validate_python({"ok": True, "summary": {"n": 1}})
    assert isinstance(result, ToolSuccess)


def test_tool_result_adapter_discriminates_failure() -> None:
    """E17."""
    result = TOOL_RESULT_ADAPTER.validate_python(
        {"ok": False, "error_class": "data", "message": "m"}
    )
    assert isinstance(result, ToolFailure)


def test_tool_result_adapter_missing_discriminator_rejected() -> None:
    """E18."""
    with pytest.raises(ValidationError, match="ok"):
        TOOL_RESULT_ADAPTER.validate_python({"summary": {}})


def test_tool_result_adapter_non_bool_discriminator_rejected() -> None:
    """E19."""
    with pytest.raises(ValidationError):
        TOOL_RESULT_ADAPTER.validate_python({"ok": "yes"})


def test_tool_result_adapter_success_round_trip() -> None:
    """E20."""
    original = ToolSuccess(summary={"rows": 10})
    dumped = TOOL_RESULT_ADAPTER.dump_json(original)
    restored = TOOL_RESULT_ADAPTER.validate_json(dumped)
    assert restored == original


def test_tool_result_adapter_failure_round_trip() -> None:
    """E21."""
    original = ToolFailure(error_class="bug", message="boom")
    dumped = TOOL_RESULT_ADAPTER.dump_json(original)
    restored = TOOL_RESULT_ADAPTER.validate_json(dumped)
    assert restored == original


def test_tool_result_adapter_failure_json_contains_ok_false() -> None:
    """E22: assert on the serialised bytes — the wire shape is the contract."""
    original = ToolFailure(error_class="bug", message="boom")
    dumped = TOOL_RESULT_ADAPTER.dump_json(original)
    assert b'"ok":false' in dumped


def test_tool_result_adapter_unknown_field_on_success_rejected() -> None:
    """E23."""
    with pytest.raises(ValidationError):
        TOOL_RESULT_ADAPTER.validate_python({"ok": True, "bogus": 1})


def test_tool_result_adapter_unknown_field_on_failure_rejected() -> None:
    """E23 (failure branch)."""
    with pytest.raises(ValidationError):
        TOOL_RESULT_ADAPTER.validate_python(
            {
                "ok": False,
                "error_class": "data",
                "message": "m",
                "bogus": 1,
            }
        )


# --- Boundary isolation ---------------------------------------------------------------------


def test_contracts_imports_only_pydantic_and_stdlib() -> None:
    """E24, E25: statlab_contracts must stay dependency-free so both processes can import it.

    Extended in chunk 03 to also cover `vocabulary.py` and `contract.py` -- the contract
    schema must not import pandas or anything else beyond stdlib and pydantic, since it
    crosses the same process boundary `envelope.py` does.
    """
    import statlab_contracts.artifacts as artifacts_mod
    import statlab_contracts.contract as contract_mod
    import statlab_contracts.envelope as envelope_mod
    import statlab_contracts.vocabulary as vocabulary_mod

    stdlib_paths = {p for p in sys.path if "site-packages" not in p}

    for mod in (artifacts_mod, envelope_mod, vocabulary_mod, contract_mod):
        for name, value in vars(mod).items():
            if name.startswith("__"):
                continue
            module_name = getattr(value, "__module__", None)
            if module_name is None:
                continue
            if module_name == mod.__name__ or module_name.startswith("statlab_contracts."):
                # `value` is defined in statlab_contracts itself (this module, or a sibling
                # module within the same package, e.g. envelope.py importing ArtifactMeta from
                # artifacts.py) — not a dependency on anything outside the package.
                continue
            assert not module_name.startswith("statlab."), (
                f"{name} in {mod.__name__} resolves to {module_name}, crossing into the backend"
            )
            assert not module_name.startswith("statlab_tools"), (
                f"{name} in {mod.__name__} resolves to {module_name}, crossing into the MCP server"
            )
            top_level = module_name.split(".", 1)[0]
            if top_level in ("builtins",):
                continue
            module_obj = sys.modules.get(top_level)
            is_stdlib = module_obj is not None and (
                getattr(module_obj, "__file__", None) is None
                or any(str(Path(module_obj.__file__).parent) == p for p in stdlib_paths)  # type: ignore[arg-type]  # __file__ checked not-None above
                or top_level in sys.stdlib_module_names
            )
            assert is_stdlib or top_level == "pydantic", (
                f"{name} in {mod.__name__} resolves to third-party module {module_name!r}, "
                f"only pydantic is permitted"
            )


def test_contracts_contract_does_not_import_pandas() -> None:
    """Chunk 03: explicit, direct check that importing the contract schema never pulls
    pandas into `sys.modules`. Subprocess-isolated (X10's pattern) because the pytest session
    itself may already have pandas imported elsewhere, which would contaminate an in-process
    check.
    """
    script = (
        "import sys\n"
        "import statlab_contracts.contract\n"
        "import statlab_contracts.vocabulary\n"
        "print('pandas' in sys.modules)\n"
    )
    result = subprocess.run(  # noqa: S603  # hardcoded literal script authored in this test
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
