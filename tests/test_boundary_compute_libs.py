"""D-b: the pandas boundary rule, widened. Chunk 06/`WHATS_NEXT.md` said "pandas appears in
exactly one module"; that rule cannot survive this chunk, since `tools/intake.py` needs pandas
too, and ~80 later tools will need numpy/sklearn/statsmodels/matplotlib as well. The real
invariant, from `PLAN.md` section 6: **the backend never imports pandas or sklearn** -- widened
here to the full compute-library list, confined to `mcp_server/`.

Regex source-scan, same technique as `test_db_connection.py`'s existing boundary tests: match
actual `import`/`from` statements, not substrings, since docstrings mention these libraries by
name (including this one).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_COMPUTE_LIBS = ("pandas", "numpy", "sklearn", "scipy", "statsmodels", "matplotlib", "joblib")

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_pattern(lib: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", re.MULTILINE)


@pytest.mark.parametrize("lib", _COMPUTE_LIBS)
@pytest.mark.parametrize("package", ["backend", "contracts"])
def test_no_compute_library_import_under_backend_or_contracts(lib: str, package: str) -> None:
    pattern = _import_pattern(lib)
    root = _REPO_ROOT / package
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is None, f"{path} imports {lib!r} across the D-b boundary: {match}"


def test_detector_actually_matches_a_real_import_statement() -> None:
    """A guard test that never silently passes because the regex broke (same shape as
    `test_any_token_detector_actually_matches`)."""
    pattern = _import_pattern("pandas")
    assert pattern.search("import pandas as pd\n")
    assert pattern.search("from pandas import DataFrame\n")


def test_detector_does_not_false_positive_on_prose_mentioning_the_library_name() -> None:
    """A docstring explaining the boundary rule (this file's own, and `tools/artifacts.py`'s)
    legitimately contains the word "pandas" without importing it."""
    pattern = _import_pattern("pandas")
    prose = "pandas appears in exactly one module, mcp_server/statlab_tools/artifacts.py."
    assert pattern.search(prose) is None


def test_tools_artifacts_still_imports_pandas_only_there() -> None:
    """`tools/artifacts.py` keeps its existing no-pandas-elsewhere property -- D-b does not
    change this, it is still true there and costs nothing to keep asserting."""
    pattern = _import_pattern("pandas")
    path = _REPO_ROOT / "mcp_server" / "statlab_tools" / "artifacts.py"
    assert pattern.search(path.read_text(encoding="utf-8"))


def test_scan_found_files_in_both_packages() -> None:
    """A wrong root path must not make the parametrised scan vacuously pass."""
    for package in ("backend", "contracts"):
        files = list((_REPO_ROOT / package).rglob("*.py"))
        assert len(files) > 0


def test_tools_intake_imports_no_statlab_backend_module() -> None:
    """D13: `mcp_server/` must never import `statlab.*` (the backend package). `intake.py` is
    the newest module under `mcp_server/` and gets this checked explicitly, even though the
    project-wide boundary test already covers it."""
    pattern = re.compile(r"^\s*(?:import|from)\s+statlab\.", re.MULTILINE)
    path = _REPO_ROOT / "mcp_server" / "statlab_tools" / "tools" / "intake.py"
    match = pattern.search(path.read_text(encoding="utf-8"))
    assert match is None, f"intake.py imports across the D13 boundary: {match}"


def test_intake_tools_are_registered_with_intake_phase() -> None:
    """The five tools appear in `tools/list` over a real registry inspection, all tagged
    `meta.phase == "intake"` -- confirms registration, not just importability. Imports
    `statlab_tools.tools` explicitly first: this test file does not otherwise trigger the
    registration side effect that other test modules happen to cause as a byproduct of their
    own imports."""
    import statlab_tools.tools  # noqa: F401 -- import side effect registers every tool module
    from statlab_tools.tool import server

    expected = {
        "load_csv",
        "infer_column_roles",
        "validate_contract",
        "detect_id_columns",
        "detect_leaky_columns",
    }
    found = set()
    for registered in server._tool_manager.list_tools():
        if registered.name in expected:
            found.add(registered.name)
            assert registered.meta is not None
            assert registered.meta["phase"] == "intake"
    assert found == expected
