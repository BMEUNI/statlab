from __future__ import annotations

import pytest
from pydantic import JsonValue
from statlab.domains.grounding import ground_sections
from statlab_contracts.reporting import ReportSection


def test_grounded_numbers_keep_the_section_unchanged() -> None:
    sources: tuple[JsonValue, ...] = ({"metrics": {"auc": 0.8234}}, "rows 1,200 kept")
    section = ReportSection(
        heading="Why c_01",
        body="AUC was 0.82 on validation. About 82% of rows.\n\n"
        "1,200 rows were used. c_01 beat c_02 on F1.",
    )
    result = ground_sections((section,), sources)
    assert result.sections == (section,)
    assert result.sections[0] is section
    assert result.removed == ()


def test_ungrounded_sentence_is_cut_from_its_line() -> None:
    section = ReportSection(heading="Results", body="- AUC was 0.82. Test AUC was 0.91. Done.")
    result = ground_sections((section,), ({"auc": 0.8234},))
    assert result.sections[0].body == "- AUC was 0.82. Done."
    assert result.removed == ("Test AUC was 0.91.",)


def test_small_integers_and_list_markers_pass_and_unmatched_lines_drop() -> None:
    section = ReportSection(
        heading="Results",
        body="- 3 candidates ran.\n\n11. Rank eleven claim.\n12 folds were used.",
    )
    result = ground_sections((section,), ())
    assert result.sections[0].body == "- 3 candidates ran.\n\n11. Rank eleven claim."
    assert result.removed == ("12 folds were used.",)


def test_table_rows_are_cut_whole() -> None:
    section = ReportSection(heading="Table", body="| a | 0.5 |\n| b | 0.7 |")
    result = ground_sections((section,), ({"v": 0.5},))
    assert result.sections[0].body == "| a | 0.5 |"
    assert result.removed == ("| b | 0.7 |",)


def test_exponents_match_within_half_a_percent() -> None:
    section = ReportSection(heading="Tests", body="p was 1e-4. q was 3e-2.")
    result = ground_sections((section,), ({"p": 0.0001},))
    assert result.sections[0].body == "p was 1e-4."
    assert result.removed == ("q was 3e-2.",)


def test_heading_or_emptied_body_drops_the_section() -> None:
    sections = (
        ReportSection(heading="Gain of 98.7%", body="x"),
        ReportSection(heading="Score", body="Score 0.99."),
        ReportSection(heading="Kept", body="No numbers here."),
    )
    result = ground_sections(sections, ())
    assert [section.heading for section in result.sections] == ["Kept"]
    assert result.removed == ("heading: Gain of 98.7%", "Score 0.99.")


@pytest.mark.parametrize(
    ("source", "body", "kept"),
    [
        ({"flag": True}, "1.0 was seen.", False),
        ({"a": [None, {"b": 2.5}]}, "2.5 was seen.", True),
        ("12% of rows", "12.0 rows.", True),
        ({"coef": -0.05}, "Coefficient fell by 0.05.", True),
    ],
)
def test_source_walk_ignores_bools_and_none_and_signs(
    source: JsonValue, body: str, kept: bool
) -> None:
    result = ground_sections((ReportSection(heading="h", body=body),), (source,))
    assert bool(result.removed) is not kept
