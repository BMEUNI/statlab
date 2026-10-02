"""Chunk 20 §4 (reproducibility): `export_pipeline_script`, `snapshot_run` and their helpers.

The slice, render and run-hash helpers are tested as pure functions over synthetic `LoggedCall`
logs; the two tools are tested as direct body calls (the pattern `test_tools_reporting.py` uses).
The injection test parses the rendered value back with `ast.literal_eval` and inspects the script
AST for calls outside the fixed `_call`/`main` set. The M7b end-to-end gate lives in
`test_spine_reporting.py`.
"""

from __future__ import annotations

import ast
import hashlib
import json
from typing import cast

import pytest
import statlab_tools.tools  # noqa: F401 -- registers the catalogue so names validate
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths
from statlab_contracts.reporting import LoggedCall
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.reproducibility import (
    _normalised_calls,
    _run_hash,
    _served_tool_names,
    export_pipeline_script,
    slice_calls,
    snapshot_run,
)


def _ref() -> str:
    return str(new_artifact_ref())


def _write_contract(run_paths: RunPaths, *, seed: int = 7) -> None:
    """A minimal contract snapshot so the tools can embed/parse it. The exact shape does not
    matter to the run hash beyond being JSON; `seed` lets a test change the contract content."""
    payload = {"target": {"column": "y"}, "constraints": {"random_seed": seed}}
    run_paths.contract_snapshot_json.write_text(json.dumps(payload), encoding="utf-8")


# --- slice (D-c) -----------------------------------------------------------------------------


def _call(tool: str, outputs: list[str], **arguments: JsonValue) -> LoggedCall:
    return LoggedCall(tool=tool, arguments=arguments, outputs=tuple(outputs))


def test_slice_keeps_champion_branch_and_shared_prefix() -> None:
    raw, train, a_model, b_model, a_score, b_score = (_ref() for _ in range(6))
    calls = (
        _call("load_csv", [raw], dataset="d"),
        _call("split_stratified", [train], frame=raw),  # shared prefix
        _call("train_estimator", [a_model], train=train, name="a"),  # candidate A branch
        _call("train_estimator", [b_model], train=train, name="b"),  # candidate B branch
        _call("predict_proba", [a_score], model=a_model, frame=train),  # A branch
        _call("score_classification", [b_score], probabilities=b_model, truth=train),  # champion B
    )
    kept = slice_calls(calls, b_score)
    # champion B's branch plus the shared prefix, in original order; A's branch dropped.
    assert kept == [0, 1, 3, 5]


def test_slice_drops_profiling_and_node6_calls() -> None:
    raw, train, model, score = (_ref() for _ in range(4))
    calls = (
        _call("load_csv", [raw], dataset="d"),
        _call("summarize_numeric", [_ref()], frame=raw),  # profiling: not on the champion path
        _call("compare_models", [_ref()], candidates={"a": score}),  # node 6
        _call("split_stratified", [train], frame=raw),
        _call("train_estimator", [model], train=train, name="a"),
        _call("predict_proba", [score], model=model, frame=train),
    )
    kept = slice_calls(calls, score)
    tools = [calls[i].tool for i in kept]
    assert "summarize_numeric" not in tools
    assert "compare_models" not in tools
    assert tools == ["load_csv", "split_stratified", "train_estimator", "predict_proba"]


def test_slice_follows_ref_inside_dict_value() -> None:
    raw, a_score, ranking = (_ref() for _ in range(3))
    calls = (
        _call("load_csv", [raw], dataset="d"),
        _call("score_classification", [a_score], truth=raw),
        _call("rank_models", [ranking], candidates={"a": a_score}),  # ref inside a dict value
    )
    kept = slice_calls(calls, ranking)
    assert kept == [0, 1, 2]


def test_slice_target_not_produced_is_tool_param() -> None:
    calls = (_call("load_csv", [_ref()], dataset="d"),)
    with pytest.raises(ToolParamError, match="produced the target"):
        slice_calls(calls, _ref())


def test_slice_consumed_ref_with_no_earlier_producer_is_tool_param() -> None:
    orphan, score = _ref(), _ref()
    # the score consumes `orphan`, which no earlier call produced (the log is incomplete).
    calls = (_call("score_classification", [score], truth=orphan),)
    with pytest.raises(ToolParamError, match="no earlier call produced"):
        slice_calls(calls, score)


def test_slice_binds_consumer_to_the_latest_earlier_producer() -> None:
    # the same ref is produced by two calls; a later consumer must bind to the LATEST earlier
    # producer (D-c), so taking the first producer instead would keep a different, earlier call.
    dup = _ref()
    raw = _ref()
    score = _ref()
    calls = (
        _call("load_csv", [raw], dataset="d"),  # 0: first producer of dup is NOT this
        _call("apply_scaler", [dup], frame=raw),  # 1: first producer of `dup`
        _call("apply_scaler", [dup], frame=raw),  # 2: latest producer of `dup`
        _call("score_classification", [score], truth=dup),  # 3: consumer -> must bind call 2
    )
    kept = slice_calls(calls, score)
    # call 2 (latest) is kept, not call 1 (first); both depend on raw via call 0.
    assert 2 in kept
    assert 1 not in kept
    assert kept == [0, 2, 3]


def test_slice_ignores_a_call_consuming_its_own_output() -> None:
    # a call that lists one of its own outputs among its arguments must not look for an earlier
    # producer of it (line: `if ref in calls[index].outputs: continue`).
    raw, score = _ref(), _ref()
    calls = (
        _call("load_csv", [raw], dataset="d"),
        # `score` is both an output and (defensively) referenced in an argument of the same call.
        _call("score_classification", [score], probabilities=raw, truth=raw, note=score),
    )
    kept = slice_calls(calls, score)
    assert kept == [0, 1]


def test_render_value_covers_every_json_shape() -> None:
    from statlab_tools.tools.reproducibility import _render_value

    ref = "a_0123456789abcdef"
    ref_source = {ref: "r0.artifacts[0].ref"}
    assert _render_value(ref, ref_source) == "r0.artifacts[0].ref"
    assert _render_value("plain", ref_source) == "'plain'"
    assert _render_value(True, ref_source) == "True"
    assert _render_value(None, ref_source) == "None"
    assert _render_value(3, ref_source) == "3"
    assert _render_value(1.5, ref_source) == "1.5"
    assert _render_value([1, "x"], ref_source) == "[1, 'x']"
    assert _render_value({"k": ref}, ref_source) == "{'k': r0.artifacts[0].ref}"


def test_export_rejects_a_non_compiling_render(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: if the `compile()` guard were removed, a broken render would be written. The
    renderer is forced to emit invalid Python; the guard must turn that into a `tool_param`."""
    import statlab_tools.tools.reproducibility as repro

    calls, target = _two_call_log(run_paths)

    def broken_body(
        _calls: tuple[LoggedCall, ...], _kept: list[int], _target: str
    ) -> tuple[str, list[str]]:
        return "    this is = = not valid python(", ["load_csv"]

    monkeypatch.setattr(repro, "_render_body", broken_body)
    with pytest.raises(ToolParamError, match="does not compile"):
        export_pipeline_script(run_paths, calls, ArtifactRef(target))


# --- render (D-d) ----------------------------------------------------------------------------


def _two_call_log(run_paths: RunPaths) -> tuple[tuple[LoggedCall, ...], str]:
    """A load_csv -> score log whose score is the target; the contract snapshot is written."""
    _write_contract(run_paths)
    raw, score = _ref(), _ref()
    calls = (
        _call("load_csv", [raw], dataset="abc"),
        _call("score_classification", [score], probabilities=raw, truth=raw, target="y"),
    )
    return calls, score


def test_render_unknown_tool_is_tool_param(run_paths: RunPaths) -> None:
    _write_contract(run_paths)
    raw = _ref()
    calls = (
        _call("load_csv", [raw], dataset="abc"),
        _call("not_a_real_tool", [_ref()], frame=raw),
    )
    target = calls[1].outputs[0]
    with pytest.raises(ToolParamError, match="not a registered tool"):
        export_pipeline_script(run_paths, calls, ArtifactRef(target))


def test_render_argument_absent_from_schema_is_tool_param(run_paths: RunPaths) -> None:
    _write_contract(run_paths)
    raw = _ref()
    score = _ref()
    calls = (
        _call("load_csv", [raw], dataset="abc"),
        _call("score_classification", [score], not_a_param=raw, truth=raw),
    )
    with pytest.raises(ToolParamError, match=r"not in .*input schema"):
        export_pipeline_script(run_paths, calls, ArtifactRef(score))


def test_render_non_identifier_argument_is_tool_param(run_paths: RunPaths) -> None:
    _write_contract(run_paths)
    raw = _ref()
    score = _ref()
    calls = (
        _call("load_csv", [raw], dataset="abc"),
        LoggedCall(
            tool="score_classification",
            arguments={"truth": cast("JsonValue", raw), "not an id": 1},
            outputs=(score,),
        ),
    )
    with pytest.raises(ToolParamError, match="not an identifier"):
        export_pipeline_script(run_paths, calls, ArtifactRef(score))


def test_render_injection_string_is_a_literal_not_code(run_paths: RunPaths) -> None:
    """A string argument carrying a code-injection payload must render as a quoted literal that
    `ast.literal_eval` returns verbatim, and the script must contain no call outside the fixed
    `_call`/`main`/stdlib set (D-d)."""
    _write_contract(run_paths)
    payload = '"); import os; os.system("x") #'
    raw, model, score = (_ref() for _ in range(3))
    calls = (
        _call("load_csv", [raw], dataset="abc"),
        _call("train_estimator", [model], train=raw, name=payload),
        _call("score_classification", [score], probabilities=model, truth=raw),
    )
    export_pipeline_script(run_paths, calls, ArtifactRef(score))
    source = run_paths.pipeline_py.read_text(encoding="utf-8")

    # the rendered name= value parses back to exactly the original string.
    tree = ast.parse(source)
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "name":
            literals.append(ast.literal_eval(node.value))
    assert payload in literals

    # the payload only ever appears inside a string literal, never as parsed code: no attribute
    # call names `system`/`popen`, no name call is `eval`/`exec`/`compile`/`__import__`, and the
    # one `os`-module reference is the fixed `os.environ` assignment in the header, not a call.
    forbidden_attrs = {"system", "popen", "spawn", "spawnv", "exec", "eval"}
    forbidden_names = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden_attrs, f"dangerous call: {node.func.attr}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_names, f"dangerous call: {node.func.id}"


def test_render_same_log_is_byte_identical_and_has_no_tmp_path(run_paths: RunPaths) -> None:
    calls, target = _two_call_log(run_paths)
    r1 = export_pipeline_script(run_paths, calls, ArtifactRef(target))
    first = run_paths.pipeline_py.read_bytes()
    r2 = export_pipeline_script(run_paths, calls, ArtifactRef(target))
    second = run_paths.pipeline_py.read_bytes()
    assert first == second
    assert r1.summary["sha256"] == r2.summary["sha256"]
    # no absolute tmp_path from the test leaks into the source.
    source = run_paths.pipeline_py.read_text(encoding="utf-8")
    assert str(run_paths.root) not in source
    assert str(run_paths.data_dir) not in source


def test_export_summary_fields(run_paths: RunPaths) -> None:
    calls, target = _two_call_log(run_paths)
    res = export_pipeline_script(run_paths, calls, ArtifactRef(target))
    assert res.summary["path"] == "pipeline.py"
    assert res.summary["n_calls"] == 2
    assert res.summary["n_kept"] == 2
    assert res.summary["tools"] == ["load_csv", "score_classification"]
    assert res.summary["dataset_sha256"] == "abc"
    assert isinstance(res.summary["sha256"], str)


def test_export_zero_load_csv_is_tool_param(run_paths: RunPaths) -> None:
    _write_contract(run_paths)
    frame, score = _ref(), _ref()
    # the slice has no load_csv at all, yet every call passes schema validation.
    calls = (
        _call("drop_columns", [frame], columns=["x"]),  # not load_csv, no ref inputs
        _call("score_classification", [score], truth=frame),
    )
    with pytest.raises(ToolParamError, match="load_csv dataset"):
        export_pipeline_script(run_paths, calls, ArtifactRef(score))


def test_export_missing_contract_snapshot_is_tool_param(run_paths: RunPaths) -> None:
    # no _write_contract: the snapshot is absent.
    raw, score = _ref(), _ref()
    calls = (
        _call("load_csv", [raw], dataset="abc"),
        _call("score_classification", [score], truth=raw),
    )
    with pytest.raises(ToolParamError, match="contract snapshot"):
        export_pipeline_script(run_paths, calls, ArtifactRef(score))


# --- run hash (D-e) --------------------------------------------------------------------------


def test_run_hash_same_for_logs_differing_only_in_ref_values() -> None:
    def log(seed_refs: int) -> tuple[LoggedCall, ...]:
        raw = f"a_{seed_refs:016x}"
        model = f"a_{seed_refs + 1:016x}"
        return (
            _call("load_csv", [raw], dataset="abc"),
            _call("train_estimator", [model], train=raw, name="logit"),
        )

    contract = '{"target": {"column": "y"}}'
    h1 = _run_hash(log(0x1111111111111111), "abc", contract)
    h2 = _run_hash(log(0x2222222222222222), "abc", contract)
    assert h1 == h2


def test_run_hash_changes_with_seed_and_with_contract() -> None:
    raw = "a_0000000000000001"
    model = "a_0000000000000002"
    base = (
        _call("load_csv", [raw], dataset="abc"),
        _call("train_estimator", [model], train=raw, name="logit", seed=1),
    )
    seeded = (
        _call("load_csv", [raw], dataset="abc"),
        _call("train_estimator", [model], train=raw, name="logit", seed=2),
    )
    contract = '{"target": {"column": "y"}}'
    other_contract = '{"target": {"column": "z"}}'
    assert _run_hash(base, "abc", contract) != _run_hash(seeded, "abc", contract)
    assert _run_hash(base, "abc", contract) != _run_hash(base, "abc", other_contract)


def test_normalised_calls_drop_project_run_and_tokenise_refs() -> None:
    raw = "a_0000000000000001"
    model = "a_0000000000000002"
    calls = (
        _call("load_csv", [raw], dataset="abc", project="p", run="r"),
        _call("train_estimator", [model], train=raw, name="logit"),
    )
    normalised = _normalised_calls(calls)
    first = cast("dict[str, JsonValue]", normalised[0])
    assert "project" not in cast("dict[str, JsonValue]", first["arguments"])
    assert "run" not in cast("dict[str, JsonValue]", first["arguments"])
    second = cast("dict[str, JsonValue]", normalised[1])
    args = cast("dict[str, JsonValue]", second["arguments"])
    # the consumed `raw` ref became its producer token @0.0.
    assert args["train"] == "@0.0"


# --- manifest (D-f) --------------------------------------------------------------------------


def _load_csv_log(run_paths: RunPaths) -> tuple[LoggedCall, ...]:
    _write_contract(run_paths)
    raw = _ref()
    return (
        _call("load_csv", [raw], dataset="abc"),
        _call("train_estimator", [_ref()], train=raw, name="logit"),
    )


def test_manifest_omits_the_token_value(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "super-secret-token-value-12345"  # noqa: S105 -- a test value, not a real credential
    monkeypatch.setenv("STATLAB_MCP_TOKEN", token)
    calls = _load_csv_log(run_paths)
    snapshot_run(run_paths, calls)
    manifest = run_paths.manifest_json.read_text(encoding="utf-8")
    assert token not in manifest


def test_manifest_lists_every_file_with_correct_sha(run_paths: RunPaths) -> None:
    # a stray artifact file under the run root must appear with its true sha256.
    extra = run_paths.artifacts / "a_000000000000beef.json"
    extra.write_text('{"x": 1}', encoding="utf-8")
    calls = _load_csv_log(run_paths)
    snapshot_run(run_paths, calls)
    manifest = cast("dict[str, JsonValue]", json.loads(run_paths.manifest_json.read_text()))
    files = cast("list[dict[str, JsonValue]]", manifest["files"])
    by_path = {str(f["path"]): f for f in files}
    rel = str(extra.relative_to(run_paths.root))
    assert rel in by_path
    assert by_path[rel]["sha256"] == hashlib.sha256(b'{"x": 1}').hexdigest()
    # manifest.json itself is never listed.
    assert "manifest.json" not in by_path


def test_manifest_uninstalled_package_is_null(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.metadata

    real = importlib.metadata.version

    def fake_version(name: str) -> str:
        if name == "statsmodels":
            raise importlib.metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(importlib.metadata, "version", fake_version)
    calls = _load_csv_log(run_paths)
    res = snapshot_run(run_paths, calls)
    packages = cast("dict[str, JsonValue]", res.summary["packages"])
    assert packages["statsmodels"] is None
    assert packages["numpy"] is not None


def test_snapshot_summary_fields(run_paths: RunPaths) -> None:
    calls = _load_csv_log(run_paths)
    res = snapshot_run(run_paths, calls)
    run_hash = res.summary["run_hash"]
    assert isinstance(run_hash, str)
    assert len(run_hash) == 64
    assert isinstance(res.summary["n_files"], int)
    assert "numpy" in cast("dict[str, JsonValue]", res.summary["packages"])


def test_snapshot_no_load_csv_is_tool_param(run_paths: RunPaths) -> None:
    _write_contract(run_paths)
    calls = (_call("train_estimator", [_ref()], train=_ref(), name="logit"),)
    with pytest.raises(ToolParamError, match="load_csv dataset"):
        snapshot_run(run_paths, calls)


def test_snapshot_missing_contract_is_tool_param(run_paths: RunPaths) -> None:
    calls = (_call("load_csv", [_ref()], dataset="abc"),)
    with pytest.raises(ToolParamError, match="contract snapshot"):
        snapshot_run(run_paths, calls)


def test_served_names_include_both_tools() -> None:
    served = _served_tool_names()
    assert "export_pipeline_script" in served
    assert "snapshot_run" in served
    # D-a: the two removed tools are gone.
    assert "compute_run_hash" not in served
    assert "prune_intermediate_artifacts" not in served
