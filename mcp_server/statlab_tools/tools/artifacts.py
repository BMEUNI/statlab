"""Diagnostic tools over a run's artifact directory. Both list the filesystem, not the
database -- the MCP server has no database access (it is stateless, D22/D30(b)) and the
boundary forbids importing `statlab.persistence`. The backend's registry is the authority on
*provenance*; these two report what is on disk.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactKind, ArtifactRef
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import EXTENSION_FOR_KIND, RunPaths

from statlab_tools.artifacts import load_dataframe
from statlab_tools.tool import ToolDataError, tool
from statlab_tools.tools._frame import json_scalar

_KIND_FOR_EXTENSION: dict[str, str] = {}
for _kind, _ext in EXTENSION_FOR_KIND.items():
    _KIND_FOR_EXTENSION.setdefault(_ext, _kind)
"""Best-effort reverse of `EXTENSION_FOR_KIND`. Several kinds share an extension (`.parquet`
for `dataframe`/`predictions`; `.joblib` for `model`/`transformer`/`inferential_fit`), and the
filesystem alone cannot disambiguate them -- `setdefault` picks the first kind declared for
each extension (`dataframe`, `model`), a deterministic guess, not a fact. `describe_artifact`
takes `kind` as an explicit parameter for exactly this reason; this module-level dict exists
only for `list_artifacts`' coarse listing, and its docstring says so."""


@tool(phase="artifacts", allow_in=["*"])
def list_artifacts(paths: RunPaths) -> ToolSuccess:
    """List every artifact file present in the run's artifact directory.

    Reads the filesystem, not the artifact registry -- the MCP server has no database access.
    The reported `kind` for each file is a best-effort guess from its extension: several kinds
    share an extension (parquet covers both `dataframe` and `predictions`; joblib covers
    `model`, `transformer` and `inferential_fit`), so this listing cannot always name the exact
    kind. Use `describe_artifact` with the real kind for anything that matters.
    """
    entries: list[JsonValue] = []
    for entry in sorted(paths.artifacts.iterdir()):
        if not entry.is_file():
            continue
        ref = entry.stem
        kind = _KIND_FOR_EXTENSION.get(entry.suffix, "unknown")
        entries.append({"ref": ref, "kind": kind})
    return ToolSuccess(summary={"count": len(entries), "artifacts": entries})


@tool(phase="artifacts", allow_in=["*"])
def describe_artifact(
    paths: RunPaths,
    artifact: Annotated[ArtifactRef, Field(description="The artifact to describe.")],
    kind: Annotated[
        ArtifactKind,
        Field(
            description=(
                "The artifact's kind. Needed because the file extension alone cannot "
                "distinguish, e.g., a dataframe from predictions."
            )
        ),
    ],
    head_rows: Annotated[
        int, Field(description="Number of leading rows to include for a frame.", ge=1, le=100)
    ] = 10,
) -> ToolSuccess:
    """Describe an artifact: shape, dtypes, and the first rows for a frame.

    Reads the filesystem, not the artifact registry -- the MCP server has no database access.
    """
    path = paths.artifact(artifact, kind)
    if kind not in ("dataframe", "predictions"):
        raise ToolDataError(f"describe_artifact does not yet support kind {kind!r}")
    frame = load_dataframe(path)
    dtypes: dict[str, JsonValue] = {
        str(column): str(dtype) for column, dtype in frame.dtypes.items()
    }
    # `to_dict` leaves integer cells as `np.int64` and datetime cells as `pandas.Timestamp`,
    # neither a valid JsonValue on its own (P1) -- `_frame.json_scalar` is the one place every
    # pandas-cell-to-JSON conversion in the project goes through, replacing the `isoformat`
    # duck-typing this used before chunk 09 introduced that shared helper.
    head: list[JsonValue] = [
        {str(column): json_scalar(value) for column, value in row.items()}
        for row in frame.head(head_rows).to_dict(orient="records")
    ]
    return ToolSuccess(
        summary={
            "rows": frame.shape[0],
            "cols": frame.shape[1],
            "dtypes": dtypes,
            "head": head,
        }
    )
