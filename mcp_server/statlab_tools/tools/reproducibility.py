"""Node 13's reproducibility tools (chunk 20, M7b): `export_pipeline_script` and `snapshot_run`.

`export_pipeline_script` writes a standalone `pipeline.py` that replays the champion's path over
the live MCP envelope -- the same argument validation and envelope as the server, without HTTP or
auth (P1) -- and prints the champion's test summary. `snapshot_run` writes `manifest.json` with a
content run hash (D-e), the Python/platform/package versions and a sha256 of every run file (D-f).

The champion's path is a backward slice over refs (D-c): start from the call that produced the
target (the champion's test score table) and pull in, repeatedly, the latest earlier call that
produced each ref an included call consumes. That excludes other candidates' branches, node 6 and
`compare_models` with no node-execution join. The slice is deterministic and its bytes carry no
timestamp or absolute path (D-d), so the same log always yields the same script and the same hash.

Generated-code safety (D-d): tool names are validated against the served registry, argument names
against each tool's input schema, every value is rendered with `repr` over JSON types only (refs
swapped for `r{i}.artifacts[k].ref` expressions), and the whole source is `compile`d before it is
written. No value is ever `eval`'d in the generated script.

stdlib only: `hashlib`, `json`, `platform`, `importlib.metadata`, `ast`/`compile`.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from typing import Annotated, Final, cast

from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ARTIFACT_ID_PATTERN, ArtifactRef
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.reporting import LoggedCall

from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolParamError, server, tool

_REPRODUCIBILITY_PHASES: Final = ("artifact_persistence",)

# D-f: the packages the manifest records, with `null` for any not installed. The run folder must
# never capture environment variables (PLAN.md §11) -- the bearer token never reaches it.
_MANIFEST_PACKAGES: Final = (
    "numpy",
    "pandas",
    "scikit-learn",
    "scipy",
    "statsmodels",
    "pyarrow",
    "matplotlib",
    "joblib",
    "pydantic",
    "mcp",
    "statlab-tools",
)

_INJECTED_ARGS: Final = ("project", "run")
"""Dropped from both the script's `_call` arguments and the run-hash normalisation (D-d, D-e):
they are orchestrator identity, not part of the reproducible computation."""


# --- ref recognition (D-c) -------------------------------------------------------------------


def _refs_in(value: JsonValue) -> list[str]:
    """Every artifact ref reachable in `value`, recursively through lists and dict values -- a
    ref inside a `candidates` dict is followed (D-c). A string matches only if it is a full
    artifact id (`ARTIFACT_ID_PATTERN`), so a label that merely contains hex is not mistaken for
    one."""
    if isinstance(value, str):
        return [value] if ARTIFACT_ID_PATTERN.match(value) else []
    if isinstance(value, dict):
        return [ref for v in value.values() for ref in _refs_in(v)]
    if isinstance(value, list):
        return [ref for v in value for ref in _refs_in(v)]
    return []


# --- slice (D-c) -----------------------------------------------------------------------------


def _producer_before(calls: tuple[LoggedCall, ...], ref: str, before: int) -> int:
    """The index of the latest call strictly before `before` that produced `ref`, or `-1` if
    none did. "Latest earlier" (D-c): a ref re-produced later resolves to the most recent
    producer that still precedes the consumer, so interleaving does not matter (D29)."""
    for i in range(before - 1, -1, -1):
        if ref in calls[i].outputs:
            return i
    return -1


def slice_calls(calls: tuple[LoggedCall, ...], target: str) -> list[int]:
    """The champion's dependency slice (D-c): the indices of the kept calls, in original order.

    Start from the call that produced `target`; repeatedly add, for every ref an included call
    consumes, the latest earlier call that produced it. A `target` no call produced, or a
    consumed ref with no earlier producer, is `tool_param` ("the log is incomplete").
    """
    producers = [i for i, call in enumerate(calls) if target in call.outputs]
    if not producers:
        raise ToolParamError(
            f"no call in the log produced the target {target!r}",
            field_errors={"target": f"not produced by any call: {target!r}"},
        )
    kept: set[int] = set()
    worklist = [producers[-1]]
    while worklist:
        index = worklist.pop()
        if index in kept:
            continue
        kept.add(index)
        for name, value in calls[index].arguments.items():
            if name in _INJECTED_ARGS:
                continue
            for ref in _refs_in(value):
                if ref in calls[index].outputs:
                    continue
                producer = _producer_before(calls, ref, index)
                if producer == -1:
                    raise ToolParamError(
                        f"call {index} ({calls[index].tool}) consumes ref {ref!r} that no "
                        f"earlier call produced; the log is incomplete",
                        field_errors={"calls": f"no earlier producer for {ref!r}"},
                    )
                worklist.append(producer)
    return sorted(kept)


# --- render (D-d) ----------------------------------------------------------------------------


def _served_tool_names() -> set[str]:
    """The registered tool names, read off the one MCP server registry -- a tool the renderer
    emits must be a real served tool (D-d)."""
    return {t.name for t in server._tool_manager.list_tools()}


def _served_schema_props(name: str) -> set[str]:
    """The property names of a served tool's input schema (D-d): an argument name not among them
    is `tool_param`."""
    tool_obj = server._tool_manager.get_tool(name)
    assert tool_obj is not None  # noqa: S101 -- caller validated the name against the registry
    schema = cast("dict[str, JsonValue]", tool_obj.parameters)
    props = schema.get("properties", {})
    return set(cast("dict[str, JsonValue]", props))


def _ref_expr(ref: str, ref_source: dict[str, str]) -> str:
    """The `r{i}.artifacts[k].ref` expression a kept call's ref resolves to (D-d). A ref produced
    by no kept call cannot appear in a kept call's arguments after slicing, so a miss is a bug in
    the slice, not a tool_param."""
    expr = ref_source.get(ref)
    assert expr is not None, f"ref {ref!r} has no producing expression"  # noqa: S101
    return expr


def _render_value(value: JsonValue, ref_source: dict[str, str]) -> str:
    """A JSON value rendered as a Python expression (D-d): refs become `r{i}.artifacts[k].ref`
    expressions, every other scalar/container is rendered with `repr` over JSON types only, so a
    string carrying `"); import os ..."` becomes a quoted literal, never executable code.

    `value` is `JsonValue`, whose only runtime shapes are `str`, `bool`, `int`, `float`, `None`,
    `list` and `dict` -- the two containers recurse, every scalar falls through to `repr`, so the
    function is total with no unreachable branch."""
    if isinstance(value, str) and ARTIFACT_ID_PATTERN.match(value):
        return _ref_expr(value, ref_source)
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(v, ref_source) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{key!r}: {_render_value(v, ref_source)}" for key, v in value.items())
        return "{" + items + "}"
    # str (non-ref), bool, int, float and None are all rendered as JSON-typed literals.
    return repr(value)


def _validate_call(call: LoggedCall, served: set[str]) -> None:
    """A kept call's tool must be served and every argument name an identifier in its schema,
    with `project`/`run` dropped -- each otherwise `tool_param` (D-d)."""
    if call.tool not in served:
        raise ToolParamError(
            f"tool {call.tool!r} is not a registered tool",
            field_errors={"calls": f"unknown tool: {call.tool!r}"},
        )
    props = _served_schema_props(call.tool)
    for name in call.arguments:
        if name in _INJECTED_ARGS:
            continue
        if not name.isidentifier():
            raise ToolParamError(
                f"argument name {name!r} of {call.tool!r} is not an identifier",
                field_errors={"calls": f"non-identifier argument: {name!r}"},
            )
        if name not in props:
            raise ToolParamError(
                f"argument {name!r} is not in {call.tool!r}'s input schema",
                field_errors={"calls": f"unknown argument {name!r} for {call.tool!r}"},
            )


_SCRIPT_HEADER: Final = '''"""Standalone replay of a modelling run's champion path.

Run id: {run_id}
Champion target: {target}

Usage: python pipeline.py DATA.csv

Replays the champion's tool calls through statlab-tools {version} over the same MCP envelope and
argument validation as the live server (no HTTP, no auth), then prints the champion's test score
summary as JSON on the last stdout line. Reproduces the recorded test metric within +/-0.005.

This file is generated: it carries no timestamp and no absolute path, so the same run always
produces identical bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile

DATASET_SHA256 = {dataset_sha256!r}
CONTRACT_SNAPSHOT = {contract_snapshot!r}


def _call(_name, **arguments):
    import anyio
    from statlab_contracts.envelope import TOOL_RESULT_ADAPTER
    from statlab_tools.tool import server

    result = anyio.run(
        server.call_tool, _name, {{**arguments, "project": _PROJECT, "run": _RUN}}
    )
    assert result.structured_content is not None, f"{{_name}} returned no structured_content"
    validated = TOOL_RESULT_ADAPTER.validate_python(result.structured_content["result"])
    if validated.ok is not True:
        raise SystemExit(f"tool {{_name}} failed: {{validated.message}}")
    return validated


_PROJECT = "pipeline"
_RUN = "r_pipeline00000000"


def main(csv_path: str) -> None:
    with open(csv_path, "rb") as handle:
        data_bytes = handle.read()
    digest = hashlib.sha256(data_bytes).hexdigest()
    if digest != DATASET_SHA256:
        raise SystemExit(
            f"dataset sha256 {{digest}} does not match the recorded {{DATASET_SHA256}}"
        )

    data_dir = tempfile.mkdtemp(prefix="pipeline_")
    os.environ["STATLAB_DATA_DIR"] = data_dir
    os.environ["STATLAB_MCP_TOKEN"] = "pipeline-replay-token"

    from pathlib import Path
    from statlab_contracts.layout import ProjectPaths, RunPaths

    data_root = Path(data_dir)
    paths = RunPaths(data_dir=data_root, slug=_PROJECT, run_id=_RUN)
    paths.artifacts.mkdir(parents=True, exist_ok=True)
    project = ProjectPaths(data_dir=data_root, slug=_PROJECT)
    project.datasets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(csv_path, project.dataset_csv(digest))
    paths.contract_snapshot_json.write_text(CONTRACT_SNAPSHOT, encoding="utf-8")

    import statlab_tools.tools  # noqa: F401 -- registers every tool on the server

{body}

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python pipeline.py DATA.csv")
    main(sys.argv[1])
'''


def _render_body(
    calls: tuple[LoggedCall, ...], kept: list[int], target: str
) -> tuple[str, list[str]]:
    """The script body: one `_call` statement per kept call (indented into `main`), each preceded
    by a comment naming its position in the original log, and a final line printing the target
    producer's summary. Returns `(body, kept tool names in order)`."""
    ref_source: dict[str, str] = {}
    lines: list[str] = []
    tools: list[str] = []
    target_var: str | None = None
    for new_index, original in enumerate(kept):
        call = calls[original]
        tools.append(call.tool)
        var = f"r{new_index}"
        args = ", ".join(
            f"{name}={_render_value(value, ref_source)}"
            for name, value in call.arguments.items()
            if name not in _INJECTED_ARGS
        )
        rendered = f"{var} = _call({call.tool!r}" + (f", {args}" if args else "") + ")"
        lines.append(f"    # original call #{original}")
        lines.append(f"    {rendered}")
        for output_index, ref in enumerate(call.outputs):
            ref_source[ref] = f"{var}.artifacts[{output_index}].ref"
        if target in call.outputs:
            target_var = var
    assert target_var is not None, "the target's producer is always the last kept call"  # noqa: S101
    lines.append(f"    print(json.dumps({target_var}.summary, sort_keys=True))")
    return "\n".join(lines), tools


def _dataset_sha256(calls: tuple[LoggedCall, ...], kept: list[int]) -> str:
    """The dataset sha256 from the slice's single `load_csv` call (D-d): zero or several is
    `tool_param`."""
    datasets = [
        str(calls[i].arguments["dataset"])
        for i in kept
        if calls[i].tool == "load_csv" and "dataset" in calls[i].arguments
    ]
    if len(datasets) != 1:
        raise ToolParamError(
            f"the slice has {len(datasets)} load_csv dataset(s); exactly one is required",
            field_errors={"calls": f"{len(datasets)} load_csv dataset sha256 in the slice"},
        )
    return datasets[0]


def _contract_snapshot(paths: RunPaths) -> str:
    """The run's contract snapshot JSON text, embedded verbatim in the script. A missing snapshot
    is `tool_param`: the replay cannot validate the contract without it."""
    path = paths.contract_snapshot_json
    if not path.exists():
        raise ToolParamError(
            "the run has no contract snapshot to embed in the script",
            field_errors={"calls": "contract.snapshot.json is missing"},
        )
    return path.read_text(encoding="utf-8")


def _statlab_version() -> str:
    """The installed `statlab-tools` version, or a sentinel when running from a source tree with
    no distribution metadata."""
    try:
        return importlib.metadata.version("statlab-tools")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+source"


@tool(phase="reproducibility", allow_in=_REPRODUCIBILITY_PHASES)
def export_pipeline_script(
    paths: RunPaths,
    calls: Annotated[
        tuple[LoggedCall, ...],
        Field(min_length=1, description=ORCHESTRATOR_SUPPLIED),
    ],
    target: Annotated[
        ArtifactRef,
        Field(description=ORCHESTRATOR_SUPPLIED),
    ],
) -> ToolSuccess:
    """Write a standalone `pipeline.py` replaying the champion's path and printing its test score.

    `calls` is the orchestrator-supplied tool log (D-b); `target` is the champion's test score
    table. The champion's path is a backward dependency slice over refs (D-c): other candidates'
    branches, node 6 and `compare_models` fall away. A target no call produced, a consumed ref
    with no earlier producer, an unknown tool, an argument absent from the tool's input schema or
    not an identifier, and a slice without exactly one `load_csv` dataset are each `tool_param`.

    The script replays each kept call through the live MCP envelope (same validation as the
    server, no HTTP) and prints the target producer's summary as JSON. Values are rendered with
    `repr` over JSON types only, refs replaced by `r{i}.artifacts[k].ref` expressions, and the
    source is `compile`d before it is written -- no value is ever `eval`'d (D-d). It carries no
    timestamp or absolute path, so the same log yields byte-identical scripts. The summary holds
    the relative `path`, `n_calls`, `n_kept`, the kept `tools` in order, the `dataset_sha256` and
    the script's `sha256`.
    """
    served = _served_tool_names()
    kept = slice_calls(calls, target)
    for index in kept:
        _validate_call(calls[index], served)
    dataset_sha256 = _dataset_sha256(calls, kept)
    body, tools = _render_body(calls, kept, target)
    source = _SCRIPT_HEADER.format(
        run_id=paths.run_id,
        target=target,
        version=_statlab_version(),
        dataset_sha256=dataset_sha256,
        contract_snapshot=_contract_snapshot(paths),
        body=body,
    )
    try:
        compile(source, "pipeline.py", "exec")
    except SyntaxError as exc:
        raise ToolParamError(
            f"the rendered script does not compile: {exc}",
            field_errors={"calls": f"rendered script is not valid Python: {exc}"},
        ) from exc

    script_bytes = source.encode("utf-8")
    paths.pipeline_py.write_text(source, encoding="utf-8")
    summary: dict[str, JsonValue] = {
        "path": str(paths.pipeline_py.relative_to(paths.root)),
        "n_calls": len(calls),
        "n_kept": len(kept),
        "tools": list(tools),
        "dataset_sha256": dataset_sha256,
        "sha256": hashlib.sha256(script_bytes).hexdigest(),
    }
    return ToolSuccess(summary=summary)


# --- 3.2 snapshot_run ------------------------------------------------------------------------


def _normalised_calls(calls: tuple[LoggedCall, ...]) -> list[JsonValue]:
    """Every call with `project`/`run` dropped and each ref replaced by `"@{call}.{output}"`
    (D-e), so two runs with the same data, contract and calls hash alike even though their refs
    differ. The producer map is built left to right; a consumed ref with no known producer keeps
    its literal value (the run hash is content, not a lineage check -- the slice enforces lineage
    for the script)."""
    producer: dict[str, str] = {}
    out: list[JsonValue] = []
    for call in calls:
        arguments = {
            name: _normalise_refs(value, producer)
            for name, value in call.arguments.items()
            if name not in _INJECTED_ARGS
        }
        out.append({"tool": call.tool, "arguments": arguments})
        for output_index, ref in enumerate(call.outputs):
            producer[ref] = f"@{len(out) - 1}.{output_index}"
    return out


def _normalise_refs(value: JsonValue, producer: dict[str, str]) -> JsonValue:
    """`value` with every artifact ref replaced by its `"@{call}.{output}"` token (D-e),
    recursively through lists and dict values."""
    if isinstance(value, str):
        if ARTIFACT_ID_PATTERN.match(value):
            return producer.get(value, value)
        return value
    if isinstance(value, list):
        return [_normalise_refs(v, producer) for v in value]
    if isinstance(value, dict):
        return {key: _normalise_refs(v, producer) for key, v in value.items()}
    return value


def _run_hash(calls: tuple[LoggedCall, ...], dataset_sha256: str, contract_snapshot: str) -> str:
    """The content run hash (D-e): sha256 of canonical JSON over the dataset sha256, the parsed
    contract snapshot and the normalised calls. Sorted keys, no whitespace, so the same content
    always hashes alike."""
    payload = {
        "dataset_sha256": dataset_sha256,
        "contract": json.loads(contract_snapshot),
        "calls": _normalised_calls(calls),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _package_versions() -> dict[str, JsonValue]:
    """Each manifest package's installed version, `null` when it is not installed (D-f)."""
    versions: dict[str, JsonValue] = {}
    for name in _MANIFEST_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _file_digests(paths: RunPaths) -> list[JsonValue]:
    """A sha256 and size for every file under the run root except `manifest.json` itself, sorted
    by relative path (D-f). No environment variables are ever read."""
    root = paths.root
    manifest = paths.manifest_json
    entries: list[JsonValue] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p != manifest):
        data = path.read_bytes()
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return entries


@tool(phase="reproducibility", allow_in=_REPRODUCIBILITY_PHASES)
def snapshot_run(
    paths: RunPaths,
    calls: Annotated[
        tuple[LoggedCall, ...],
        Field(min_length=1, description=ORCHESTRATOR_SUPPLIED),
    ],
) -> ToolSuccess:
    """Write `manifest.json` with the content run hash, versions and per-file digests (D-e, D-f).

    `calls` is the orchestrator-supplied tool log (D-b). The run hash (D-e) is the sha256 of
    canonical JSON over the dataset sha256, the parsed contract snapshot and every call with
    `project`/`run` dropped and each ref replaced by `"@{call}.{output}"`, so two runs with the
    same data, contract and calls hash alike. A log with no `load_csv` is `tool_param`: it has no
    data identity to hash.

    The manifest records the Python version, `platform.platform()`, the versions of the modelling
    packages (`null` for any not installed) and a sha256 and size for every file under the run
    root except `manifest.json` itself (D-f). It never captures environment variables -- the
    bearer token must never reach a run folder (PLAN.md §11). The summary carries `run_hash`,
    `n_files` and `packages`.
    """
    datasets = [
        str(call.arguments["dataset"])
        for call in calls
        if call.tool == "load_csv" and "dataset" in call.arguments
    ]
    if len(datasets) != 1:
        raise ToolParamError(
            f"the log has {len(datasets)} load_csv dataset(s); the run hash needs exactly one",
            field_errors={"calls": f"{len(datasets)} load_csv dataset sha256 in the log"},
        )
    contract_snapshot = _contract_snapshot(paths)
    run_hash = _run_hash(calls, datasets[0], contract_snapshot)
    packages = _package_versions()
    files = _file_digests(paths)
    manifest: dict[str, JsonValue] = {
        "run_id": paths.run_id,
        "run_hash": run_hash,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "files": files,
    }
    paths.manifest_json.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    summary: dict[str, JsonValue] = {
        "run_hash": run_hash,
        "n_files": len(files),
        "packages": packages,
    }
    return ToolSuccess(summary=summary)
