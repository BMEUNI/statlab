"""Pre-run dataset profiling without run artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from statlab_contracts.contract import DataContract, DatasetOptions, TargetSpec
from statlab_contracts.layout import ProjectPaths
from statlab_tools.tools.intake import setup_dataset

pytestmark = pytest.mark.usefixtures("tool_settings")


def test_setup_profile_is_aggregate_only_and_has_no_run_artifacts(
    tool_settings: object, tmp_path: Path
) -> None:
    from statlab_tools.settings import ToolSettings

    settings = tool_settings
    assert isinstance(settings, ToolSettings)
    paths = ProjectPaths(settings.data_dir, "demo")
    paths.datasets.mkdir(parents=True)
    content = b"a,b\n1,\n2,\n"
    digest = hashlib.sha256(content).hexdigest()
    paths.dataset_csv(digest).write_bytes(content)

    result = setup_dataset("demo", digest, options=DatasetOptions())

    assert result.artifacts == ()
    assert result.summary["rows"] == 2
    assert result.summary["cols"] == 2
    assert result.summary["all_null"] == ["b"]
    assert result.summary["n_unique"] == {"a": 2, "b": 0}
    assert not paths.runs.exists()
    assert not list(tmp_path.glob("**/artifacts"))


def test_setup_rejects_dataset_symlink_escape(tool_settings: object, tmp_path: Path) -> None:
    from statlab_tools.settings import ToolSettings
    from statlab_tools.tool import ToolParamError

    settings = tool_settings
    assert isinstance(settings, ToolSettings)
    paths = ProjectPaths(settings.data_dir, "demo")
    paths.datasets.mkdir(parents=True)
    outside = tmp_path / "outside.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    digest = "a" * 64
    paths.dataset_csv(digest).symlink_to(outside)

    with pytest.raises(ToolParamError):
        setup_dataset("demo", digest, options=DatasetOptions())


@pytest.mark.parametrize(
    ("content", "options", "error"),
    [
        (b"a,b,a\n1,2,3\n", DatasetOptions(), "duplicate"),
        (b"a,b\n", DatasetOptions(), "zero data rows"),
        (b"a;b\n1;2\n", DatasetOptions(), "delimiter"),
        (b"a,b\n1,2\n", DatasetOptions(encoding="not-a-codec"), "encoding"),
    ],
)
def test_setup_csv_errors_are_classified(
    tool_settings: object,
    tmp_path: Path,
    content: bytes,
    options: DatasetOptions,
    error: str,
) -> None:
    from statlab_tools.settings import ToolSettings
    from statlab_tools.tool import ToolDataError, ToolParamError

    settings = tool_settings
    assert isinstance(settings, ToolSettings)
    paths = ProjectPaths(settings.data_dir, "demo")
    paths.datasets.mkdir(parents=True)
    digest = hashlib.sha256(content).hexdigest()
    paths.dataset_csv(digest).write_bytes(content)
    with pytest.raises((ToolDataError, ToolParamError), match=error):
        setup_dataset("demo", digest, options=options)


def test_setup_validation_reports_supplied_target_findings(tool_settings: object) -> None:
    from statlab_tools.settings import ToolSettings

    settings = tool_settings
    assert isinstance(settings, ToolSettings)
    content = b"x,y\n1,2\n3,4\n"
    paths = ProjectPaths(settings.data_dir, "demo")
    paths.datasets.mkdir(parents=True)
    digest = hashlib.sha256(content).hexdigest()
    paths.dataset_csv(digest).write_bytes(content)
    contract = DataContract(target=TargetSpec(column="missing"))
    result = setup_dataset("demo", digest, options=DatasetOptions(), contract=contract)
    assert result.summary["valid"] is False
    mismatches = result.summary["mismatches"]
    assert isinstance(mismatches, list)
    assert any(
        isinstance(item, dict) and item.get("field") == "target.column" for item in mismatches
    )
