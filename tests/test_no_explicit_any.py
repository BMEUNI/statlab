"""Source-level replacement for `disallow_any_explicit` (removed in chunk 02 §3.0 — it is
incompatible with the pydantic mypy plugin, which synthesizes `Any` in every BaseModel's
`__init__` regardless of what the model actually declares).

This scans our own source text for the token `Any`, which is stronger than the mypy flag was:
it judges code we wrote, not signatures the plugin synthesizes, and it cannot be defeated by a
class-line `# type: ignore`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ANY_TOKEN = re.compile(r"(?<![\w.])Any(?![\w])")
_PACKAGES = ("backend/statlab", "mcp_server/statlab_tools", "contracts/statlab_contracts")

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for package in _PACKAGES:
        files.extend(sorted((_REPO_ROOT / package).rglob("*.py")))
    return files


def test_no_any_token_in_package_source() -> None:
    """Y1: walk every *.py under _PACKAGES; assert no line matches _ANY_TOKEN."""
    violations: list[str] = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ANY_TOKEN.search(line):
                violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, "explicit Any found:\n" + "\n".join(violations)


def test_any_token_detector_actually_matches() -> None:
    """Y2: a guard test that never silently passes because the regex broke."""
    assert _ANY_TOKEN.search("def f(x: Any) -> Any: ...")


@pytest.mark.parametrize(
    "line",
    [
        "def f(x: Anything) -> None: ...",
        "anyway, that is fine",
        "x: typing.Anyx = 1",
    ],
)
def test_any_token_detector_does_not_false_positive(line: str) -> None:
    """Y3 (non-comment cases): `Anything`, `anyway`, `Anyx` must not match — the word-boundary
    lookaround must not fire on a bare substring.
    """
    assert not _ANY_TOKEN.search(line)


def test_any_token_detector_matches_inside_a_comment_too() -> None:
    """Y3 (comment case), pinned explicitly: `# Any` in a comment DOES count as a violation.

    Decision: the detector is a plain text scan, not a parser, so it cannot distinguish "a
    comment explaining Any" from "a comment hiding an annotation that reads as Any at a
    glance". Carving out an exception for comments would also make the guarantee gameable —
    moving a real `x: Any` into a trailing comment would silently escape the scan. Treating
    every bare `Any` token as a violation, including in comments, is the simpler and harder to
    defeat rule; a legitimate discussion of pydantic's Any usage belongs in this test file's
    own docstrings (which are not scanned — only backend/mcp_server/contracts source is) or can
    reference the concept by a different token, e.g. "the Any type".
    """
    assert _ANY_TOKEN.search("# Any")


def test_any_token_scan_found_files() -> None:
    """Y4: assert the number of scanned files is > 0, so a wrong path cannot make Y1 vacuously
    pass.
    """
    files = _iter_source_files()
    assert len(files) > 0
