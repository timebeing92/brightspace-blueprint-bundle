"""Heading ownership and the panel vocabulary (2026-10-13).

The Blueprint Wizard's 1.4.0 output cut every panel that opened with a heading,
because pages were segmented by a raw-HTML heading regex before the block
reader ran. These tests pin the replacement: segmentation on the parsed block
tree with the heading-ownership invariant, the conservative inline-panel
classifier, the syllabus heading index, and the display dedupe every label
writer shares. Fixtures are synthetic; the shapes come from the MBA 620,
BUMG 660 and EDU 740 exports reviewed on 2026-10-12.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_blueprint_bundle as blueprint  # noqa: E402
import reconstruct_course_structure as structure  # noqa: E402


def text_of(block: dict) -> str:
    return structure._runs_text(block)


def outline(blocks: list[dict]) -> list:
    """(kind, label text, [children]) per block, for compact comparisons."""
    out = []
    for block in blocks:
        entry = [block["kind"], text_of(block)]
        if block.get("blocks"):
            entry.append(outline(block["blocks"]))
        out.append(tuple(entry))
    return out


def segments_of(raw: str) -> list[tuple]:
    return [
        (seg["heading"], seg["level"], seg.get("heading_owner", ""), outline(seg["blocks"]))
        for seg in structure.html_to_segments(raw)
    ]


# --------------------------------------------------------------------------- #
# Segmentation: the heading-ownership invariant
# --------------------------------------------------------------------------- #
def test_a_panel_opening_with_a_heading_starts_a_segment_and_stays_whole() -> None:
    """The MBA 620 defect: a callout whose first child is an h2 was emitted as an
    empty pair with its contents outside, because the regex split at the h2."""
    raw = (
        "<p>Intro.</p>"
        '<div class="callout"><h2>Course Project</h2><p>Body.</p><h3>Milestones</h3><p>More.</p></div>'
        "<p>After the panel.</p>"
        "<h2>Next Section</h2><p>N.</p>"
    )
    assert segments_of(raw) == [
        ("", 0, "", [("p", "Intro.")]),
        ("Course Project", 2, "panel", [
            ("visual", "Callout", [("label", "Course Project"), ("p", "Body."), ("label", "Milestones"), ("p", "More.")]),
            ("p", "After the panel."),
        ]),
        ("Next Section", 2, "", [("p", "N.")]),
    ]


def test_a_panel_with_leading_prose_or_no_owned_heading_joins_the_current_segment() -> None:
    raw = (
        "<h2>Reading</h2><p>Lead.</p>"
        '<div class="callout"><p>Note first.</p><h3>Then a heading</h3><p>Tail.</p></div>'
        '<div class="callout"><p>No heading at all.</p></div>'
    )
    assert segments_of(raw) == [
        ("Reading", 2, "", [
            ("p", "Lead."),
            ("visual", "Callout", [("p", "Note first."), ("label", "Then a heading"), ("p", "Tail.")]),
            ("visual", "Callout", [("p", "No heading at all.")]),
        ]),
    ]


def test_a_heading_with_nothing_of_its_own_governs_the_panel_below_it() -> None:
    """MBA 620 Week 5: <h2>Assignment – …</h2> directly followed by a callout that
    opens with an h3. The h2 must not be lost; the panel is its content."""
    raw = (
        "<h2>Assignment – Sponsorship ROI Case</h2>"
        '<div class="callout"><h3>This Week’s Deliverable</h3><p>Write the case.</p></div>'
        "<h2>Learning Objectives</h2><ul><li>Explain X.</li></ul>"
        '<h3>Empty heading</h3><div class="callout"><h2>Peer heading</h2><p>Own section.</p></div>'
    )
    assert segments_of(raw) == [
        ("Assignment – Sponsorship ROI Case", 2, "", [
            ("visual", "Callout", [("label", "This Week’s Deliverable"), ("p", "Write the case.")]),
        ]),
        ("Learning Objectives", 2, "", [("li", "Explain X.")]),
        ("Empty heading", 3, "", []),  # an equal-or-higher heading in the panel is its own section
        ("Peer heading", 2, "panel", [("visual", "Callout", [("label", "Peer heading"), ("p", "Own section.")])]),
    ]
    # the page's own h1 does not govern: a panel-owned h2 right after it is its own section
    page = '<h1>Week 1 Learning Materials</h1><section class="resources-section"><h2>Assigned Reading</h2><ul><li>Ch. 1</li></ul></section>'
    assert [(s["heading"], s["level"], s.get("heading_owner", "")) for s in structure.html_to_segments(page)] == [
        ("Week 1 Learning Materials", 1, ""), ("Assigned Reading", 2, "panel"),
    ]


def test_a_timeline_with_heading_stages_is_one_segment() -> None:
    raw = (
        '<section class="timeline"><h3>Stage 1</h3><p>One.</p><h3>Stage 2</h3><p>Two.</p><h3>Stage 3</h3><p>Three.</p></section>'
    )
    segments = structure.html_to_segments(raw)
    assert [(s["heading"], s["level"], s.get("heading_owner")) for s in segments] == [("Stage 1", 3, "panel")]
    assert outline(segments[0]["blocks"]) == [
        ("visual", "Timeline", [("label", "Stage 1"), ("p", "One."), ("label", "Stage 2"), ("p", "Two."), ("label", "Stage 3"), ("p", "Three.")]),
    ]


def test_a_details_grid_holding_dropdowns_keeps_every_level() -> None:
    raw = (
        '<div class="details-grid"><h2>Course Details</h2>'
        "<details><summary>Dates</summary><h4>Term</h4><p>Fall B.</p></details>"
        "<details><summary>Contact</summary><p>Email.</p></details>"
        "</div>"
    )
    segments = structure.html_to_segments(raw)
    assert [(s["heading"], s.get("heading_owner")) for s in segments] == [("Course Details", "panel")]
    assert outline(segments[0]["blocks"]) == [
        ("visual", "Details grid", [
            ("label", "Course Details"),
            ("dropdown", "Dates", [("label", "Term"), ("p", "Fall B.")]),
            ("dropdown", "Contact", [("p", "Email.")]),
        ]),
    ]
    # a heading owned by a nested dropdown does not count for the outer panel
    nested_only = '<div class="callout"><details><summary>Open</summary><h3>Inside</h3><p>x</p></details></div><p>y</p>'
    assert segments_of(nested_only) == [("", 0, "", [
        ("visual", "Callout", [("dropdown", "Open", [("label", "Inside"), ("p", "x")])]),
        ("p", "y"),
    ])]


def test_a_dropdown_opening_with_a_heading_owns_it() -> None:
    raw = "<p>Lead.</p><details><summary>Materials</summary><h3>Required Texts</h3><ul><li>Book.</li></ul></details>"
    assert segments_of(raw) == [
        ("", 0, "", [("p", "Lead.")]),
        ("Required Texts", 3, "panel", [("dropdown", "Materials", [("label", "Required Texts"), ("li", "Book.")])]),
    ]


def test_headings_inside_table_cells_no_longer_cut_the_table() -> None:
    raw = "<h2>Plan</h2><table><tr><td><h3>Column heading</h3><p>Cell text.</p></td><td>B</td></tr></table><p>After.</p>"
    segments = structure.html_to_segments(raw)
    assert [s["heading"] for s in segments] == ["Plan"]
    assert [b["kind"] for b in segments[0]["blocks"]] == ["table", "p"]


# --------------------------------------------------------------------------- #
# The conservative inline-panel classifier
# --------------------------------------------------------------------------- #
def panels(raw: str) -> list[tuple]:
    blocks = structure.html_fragment_to_blocks(raw)
    return [
        (text_of(b), b.get("meta", {}).get("color", ""), b.get("meta", {}).get("inferred", ""), [text_of(c) for c in b.get("blocks", [])])
        for b in blocks if b["kind"] == "visual"
    ]


def test_an_inline_tint_with_no_left_edge_is_a_callout() -> None:
    """BUMG 660 tips in editor fields: a tinted div, no border-left."""
    raw = '<div style="background:#eef6ff; padding:1rem; border-radius: 6px"><p><strong>Tip:</strong> read the case first.</p></div>'
    assert panels(raw) == [("Callout", "", "inline-style", ["Tip: read the case first."])]


def test_an_inline_left_edge_is_a_callout_with_its_colour() -> None:
    raw = '<div style="border-left: 4px solid #134f5c; padding: .5rem 1rem; background: #fff"><p>Noted.</p></div>'
    assert panels(raw) == [("Callout", "#134f5c", "inline-style", ["Noted."])]
    # three-digit hex and longhands read the same way
    raw = '<div style="border-left-width: 4px; border-left-style: solid; border-left-color: #36c"><p>Noted.</p></div>'
    assert panels(raw) == [("Callout", "#3366cc", "inline-style", ["Noted."])]
    # an accent on a framed box is still a left edge
    raw = '<div style="border: 1px solid #ddd; border-left: 5px solid #c00"><p>Accent.</p></div>'
    assert panels(raw) == [("Callout", "#cc0000", "inline-style", ["Accent."])]


def test_a_named_card_keeps_its_name() -> None:
    raw = '<div class="case-card" style="background:#fff"><h3>The Case</h3><p>Facts.</p></div>'
    blocks = structure.html_fragment_to_blocks(raw)
    assert text_of(blocks[0]) == "Case card" and "inferred" not in blocks[0]["meta"]
    assert blocks[0]["meta"]["classes"] == "case-card" and blocks[0]["meta"]["source_style"] == "background:#fff"
    assert blueprint.md_panel_lines("visual", blocks[0]["runs"], blocks[0]["meta"]) == ("[box: Case card]", "[/box]")


def test_a_white_padded_wrapper_inside_a_dropdown_is_not_a_panel() -> None:
    raw = '<details><summary>More</summary><div style="background: #ffffff; padding: 1rem; margin-top: .5rem"><p>Inside.</p><ul><li>Point.</li></ul></div></details>'
    blocks = structure.html_fragment_to_blocks(raw)
    assert outline(blocks) == [("dropdown", "More", [("p", "Inside."), ("li", "Point.")])]


def test_a_white_framed_panel_is_an_honestly_labelled_framed_section() -> None:
    raw = (
        '<div style="border:1px solid #ccc; background:#fff; padding:1rem; margin:1rem 0; border-radius:8px; '
        'box-shadow:0 1px 2px rgba(0,0,0,.1); max-width:48rem"><p>Boxed.</p><p>Twice.</p></div>'
    )
    assert panels(raw) == [("Framed section", "", "inline-style", ["Boxed.", "Twice."])]
    blocks = structure.html_fragment_to_blocks(raw)
    assert blueprint.md_panel_lines("visual", blocks[0]["runs"], blocks[0]["meta"]) == ("[box: Framed section]", "[/box]")


def test_the_edu_740_badge_is_decoration_and_its_text_stays_in_place() -> None:
    """A 2 rem absolutely positioned numbered circle: never a callout."""
    raw = (
        '<div style="position: absolute; top: -1rem; left: 1rem; width: 2rem; height: 2rem; border-radius: 50%; '
        'background: #134f5c; color: #fff; font-weight: bold; display: flex; align-items: center; justify-content: center;">1</div>'
        "<p>Step text.</p>"
    )
    assert outline(structure.html_fragment_to_blocks(raw)) == [("p", "1"), ("p", "Step text.")]
    # each guard alone is enough
    for style in ("display: inline-block; background: #eee", "float: left; background: #eee",
                  "width: 48px; background: #eee", "height: 3rem; background: #eee", "border-radius: 50%; background: #eee"):
        assert panels(f'<div style="{style}"><p>Chip text here.</p></div>') == [], style
    # a tinted wrapper around one short token is decoration too; around a sentence it is a callout
    assert outline(structure.html_fragment_to_blocks('<div style="background:#eee"><span>NEW</span></div>')) == [("p", "NEW")]
    assert panels('<div style="background:#eee"><p>Noted.</p></div>') == [("Callout", "", "inline-style", ["Noted."])]


def test_white_absent_or_rule_only_styling_is_not_a_panel() -> None:
    for style in ("background: white", "background-color: transparent; padding: 1rem", "background: none",
                  "border: 0; background: #fff", "border: none", "border-top: 1px solid #ddd; padding-top: 1rem",
                  "border-bottom: 2px solid #134f5c", "max-width: 48rem; margin: 0 auto", "padding: 1rem"):
        assert panels(f'<div style="{style}"><p>Plain paragraph.</p><p>Second.</p></div>') == [], style


def test_the_packagers_styled_box_page_component_is_a_styled_section() -> None:
    raw = '<div class="styled-box"><h3>Boxed heading</h3><p>Page component.</p></div>'
    blocks = structure.html_fragment_to_blocks(raw)
    assert text_of(blocks[0]) == "Styled section"
    assert blueprint.md_panel_lines("visual", blocks[0]["runs"], blocks[0]["meta"]) == ("[box: Styled section]", "[/box]")
    pytest.importorskip("docx")
    import blueprint_to_docx as writer
    assert writer.panel_lines("visual", blocks[0]["runs"], blocks[0]["meta"]) == ("box", "[box: Styled section]", "[/box]")


def test_class_panels_carry_tone_or_colour_and_boxes_carry_them_too() -> None:
    raw = '<div class="box box--gold"><p>Gold box.</p></div><div class="panel" style="border-left: 3px solid #abc"><p>Edged.</p></div>'
    blocks = [b for b in structure.html_fragment_to_blocks(raw) if b["kind"] == "visual"]
    assert blueprint.md_panel_lines("visual", blocks[0]["runs"], blocks[0]["meta"]) == ("[box tone=gold: Box]", "[/box]")
    assert blueprint.md_panel_lines("visual", blocks[1]["runs"], blocks[1]["meta"]) == ("[box color=#aabbcc: Panel]", "[/box]")


# --------------------------------------------------------------------------- #
# Syllabus discovery through the heading index
# --------------------------------------------------------------------------- #
def test_three_syllabus_headings_in_one_callout_yield_three_fields() -> None:
    raw = (
        "<h1>ABC 123 Sample Course</h1>"
        '<div class="callout"><h2>Course Description</h2><p>What the course is.</p>'
        "<h2>Required Materials</h2><ul><li>The book.</li></ul>"
        "<h2>Learning Outcomes</h2><ol><li>Explain it.</li></ol></div>"
        "<h2>Schedule</h2><p>Weekly.</p>"
    )
    segments = structure.html_to_segments(raw)
    assert [(s["heading"], s.get("heading_owner", "")) for s in segments] == [
        ("ABC 123 Sample Course", ""), ("Course Description", "panel"), ("Schedule", ""),
    ]
    fields = structure.extract_syllabus_front_matter(segments, source_url="u", source_sha256="s", source_artifact="a")
    assert {k: (v["heading"], v["heading_level"], v["text"]) for k, v in fields.items()} == {
        "course_description": ("Course Description", 2, "What the course is."),
        "required_materials": ("Required Materials", 2, "The book."),
        "course_learning_outcomes": ("Learning Outcomes", 2, "Explain it."),
    }
    assert structure.extract_syllabus_title_candidates(segments)[0]["course_code"] == "ABC 123"


def test_a_top_level_field_still_folds_its_subsections() -> None:
    raw = "<h2>Course Description</h2><p>Lead.</p><h3>Format</h3><p>Online.</p><h2>Required Materials</h2><p>Book.</p>"
    segments = structure.html_to_segments(raw)
    fields = structure.extract_syllabus_front_matter(segments, source_url="u", source_sha256="s", source_artifact="a")
    assert fields["course_description"]["text"] == "Lead. Format: Online."
    assert fields["required_materials"]["text"] == "Book."


# --------------------------------------------------------------------------- #
# Display dedupe: one heading, written once
# --------------------------------------------------------------------------- #
def owned_panel(heading: str, body: str = "Body.") -> dict:
    return {"kind": "visual", "level": 0, "runs": [{"text": "Callout", "href": ""}], "blocks": [
        {"kind": "label", "level": 2, "runs": [{"text": heading, "href": ""}]},
        {"kind": "p", "level": 0, "runs": [{"text": body, "href": ""}]},
    ]}


def test_the_dedupe_helper_inspects_the_first_block_only() -> None:
    blocks = [owned_panel("Assigned Reading"), {"kind": "p", "level": 0, "runs": [{"text": "x", "href": ""}]}]
    assert structure.panel_owns_label(blocks, "Assigned Reading")
    assert structure.panel_owns_label(blocks, "assigned reading:")  # case and the writer's colon do not matter
    assert not structure.panel_owns_label(blocks, "Other")
    assert not structure.panel_owns_label(list(reversed(blocks)), "Assigned Reading")
    assert not structure.panel_owns_label([], "Assigned Reading")
    assert blueprint._heading_label_block("Assigned Reading", 2, blocks=blocks) is None
    assert blueprint._heading_label_block("Assigned Reading", 2)["runs"][0]["text"] == "Assigned Reading:"


def test_markdown_and_docx_labeled_sections_suppress_a_label_the_panel_carries(tmp_path: Path) -> None:
    sections = [
        {"label": "Assigned Reading", "blocks": [owned_panel("Assigned Reading")], "source_page": "P", "level": 2},
        {"label": "Multimedia", "blocks": [{"kind": "p", "level": 0, "runs": [{"text": "Watch.", "href": ""}]}], "source_page": "P", "level": 2},
    ]
    markdown = blueprint.md_labeled(sections)
    assert markdown.count("Assigned Reading") == 1 and "**Assigned Reading:**" not in markdown
    assert "**[callout]**<br><br>**Assigned Reading**<br><br>Body.<br><br>**[/callout]**" in markdown
    assert "**Multimedia:**<br>Watch." in markdown

    docx = pytest.importorskip("docx")
    import blueprint_to_docx as writer
    doc = docx.Document()
    writer.clear_body(doc)
    writer.write_value_labeled(doc, sections, missing="(none)")
    texts = [p.text for p in doc.paragraphs if p.text]
    assert texts == ["[callout]", "Assigned Reading", "Body.", "[/callout]", "Multimedia:", "Watch."]
    assert doc.paragraphs[1].style.name == "Heading 2"


# --------------------------------------------------------------------------- #
# Routing: the week model names what a whole panel keeps together
# --------------------------------------------------------------------------- #
def test_mixed_bucket_headings_are_named_not_silently_routed() -> None:
    entry = {"bucket": "resources", "label": "Assigned Reading", "source_page": "Week 1 Overview", "level": 2, "blocks": [
        {"kind": "visual", "level": 0, "runs": [{"text": "Callout", "href": ""}], "blocks": [
            {"kind": "label", "level": 2, "runs": [{"text": "Assigned Reading", "href": ""}]},
            {"kind": "li", "level": 1, "runs": [{"text": "Chapter 1", "href": ""}]},
            {"kind": "label", "level": 3, "runs": [{"text": "Learning Objectives", "href": ""}]},
            {"kind": "li", "level": 1, "runs": [{"text": "Explain X.", "href": ""}]},
            {"kind": "label", "level": 3, "runs": [{"text": "Multimedia", "href": ""}]},
        ]},
    ]}
    assert blueprint.mixed_bucket_headings(entry, "overview") == [("Learning Objectives", "objectives")]
    entry["blocks"][0]["blocks"].pop(0)  # a panel without an owned heading is scanned the same way
    assert blueprint.mixed_bucket_headings(entry, "overview") == [("Learning Objectives", "objectives")]
    entry["blocks"][0]["blocks"] = [{"kind": "p", "level": 0, "runs": [{"text": "x", "href": ""}]}]
    assert blueprint.mixed_bucket_headings(entry, "overview") == []


MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0" identifier="M1">
  <organizations>
    <organization identifier="ORG1">
      <item identifier="M1" identifierref="RES_MOD1">
        <title>Week 1</title>
        <item identifier="P1" identifierref="RES_OVERVIEW"><title>Week 1 Overview</title></item>
        <item identifier="P2" identifierref="RES_MATERIALS"><title>Week 1 Learning Materials</title></item>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="RES_MOD1" type="webcontent" d2l_2p0:material_type="contentmodule" />
    <resource identifier="RES_OVERVIEW" type="webcontent" d2l_2p0:material_type="content" href="pages/overview.html" />
    <resource identifier="RES_MATERIALS" type="webcontent" d2l_2p0:material_type="content" href="pages/materials.html" />
  </resources>
</manifest>
"""

OVERVIEW_PAGE = """<html><head><title>Week 1 Overview</title></head><body>
<a class="skip" href="#main">Skip to content</a>
<h1>Week 1 Overview</h1>
<p>Intro.</p>
<div class="callout"><h2>Welcome</h2><p>Welcome text.</p><h3>Learning Objectives</h3><ul><li>Explain X.</li></ul></div>
<p>After the panel.</p>
<div style="background:#eef6ff; padding:1rem"><p><strong>Tip:</strong> read first.</p></div>
</body></html>
"""

MATERIALS_PAGE = """<html><head><title>Week 1 Learning Materials</title></head><body>
<h1>Week 1 Learning Materials</h1>
<section class="resources-section"><h2>Assigned Reading</h2><ul><li>Chapter 1</li></ul></section>
<h2>Multimedia</h2><p>Watch the clip.</p>
</body></html>
"""


@pytest.fixture(scope="module")
def ownership_bundle(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("heading_ownership")
    export = root / "export"
    (export / "pages").mkdir(parents=True)
    (export / "imsmanifest.xml").write_text(MANIFEST, encoding="utf-8")
    (export / "pages" / "overview.html").write_text(OVERVIEW_PAGE, encoding="utf-8")
    (export / "pages" / "materials.html").write_text(MATERIALS_PAGE, encoding="utf-8")
    output_dir = root / "review"
    result = subprocess.run(
        [sys.executable, "scripts/build_blueprint_bundle.py", str(export), "--label", "owned", "--course-title",
         "Owned", "--output-dir", str(output_dir), "--no-syllabus-fetch", "--quiet"],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bundle = output_dir / "owned__blueprint_bundle"
    return {
        "bundle": bundle,
        "model": json.loads((bundle / "owned__blueprint.json").read_text(encoding="utf-8")),
        "structure": json.loads((bundle / "owned__course_structure.json").read_text(encoding="utf-8")),
        "markdown": (bundle / "owned__blueprint.md").read_text(encoding="utf-8"),
        "qa": json.loads((bundle / "owned__course_qa.json").read_text(encoding="utf-8")),
    }


def test_the_structure_contract_marks_panel_owned_segments(ownership_bundle: dict) -> None:
    topics = {t["manifest_title"]: t for t in ownership_bundle["structure"]["html_topics"]}
    overview = topics["Week 1 Overview"]["body_segments"]
    assert [(s["heading"], s["level"], s.get("heading_owner")) for s in overview] == [
        ("Week 1 Overview", 1, None), ("Welcome", 2, "panel"),
    ]
    assert [b["kind"] for b in overview[1]["blocks"]] == ["visual", "p", "visual"]
    assert overview[1]["blocks"][2]["meta"]["inferred"] == "inline-style"


def test_the_week_model_keeps_the_panel_whole_and_reports_the_held_heading(ownership_bundle: dict) -> None:
    week = ownership_bundle["model"]["weeks"][0]
    kinds = [(b["kind"], text_of(b)) for b in week["overview"]]
    # the page title label, the intro, the whole panel (heading inside, no second "Welcome:" label), the tail
    assert kinds == [("label", "Week 1 Overview:"), ("p", "Intro."), ("visual", "Callout"), ("p", "After the panel."), ("visual", "Callout")]
    assert outline([week["overview"][2]]) == [("visual", "Callout", [
        ("label", "Welcome"), ("p", "Welcome text."), ("label", "Learning Objectives"), ("li", "Explain X."),
    ])]
    assert week["learning_objectives"] == []  # the objectives heading stayed inside its panel ...
    diagnostics = ownership_bundle["model"]["diagnostics"]
    assert any(  # ... and the model says so
        "Routing: Week 1: a panel under 'Welcome' on page 'Week 1 Overview' is kept whole under overview; "
        "it also holds 'Learning Objectives' (objectives), which would route there on its own." == d
        for d in diagnostics
    ), diagnostics


def test_resources_whose_heading_sits_in_its_panel_are_written_once(ownership_bundle: dict) -> None:
    week = ownership_bundle["model"]["weeks"][0]
    assert [s["label"] for s in week["resources"]] == ["Assigned Reading", "Multimedia"]  # labels stay in the data
    assert structure.panel_owns_label(week["resources"][0]["blocks"], "Assigned Reading")
    markdown = ownership_bundle["markdown"]
    assert "**Assigned Reading:**" not in markdown
    assert "**[box: Resources section]**<br><br>**Assigned Reading**<br><br>• Chapter 1<br><br>**[/box]**" in markdown
    assert "**Multimedia:**<br>Watch the clip." in markdown
    docx = pytest.importorskip("docx")
    doc = docx.Document(str(ownership_bundle["bundle"] / "owned__blueprint.docx"))
    texts = [p.text for table in doc.tables for row in table.rows for cell in row.cells for p in cell.paragraphs if p.text]
    start = texts.index("[box: Resources section]")
    assert texts[start:start + 6] == ["[box: Resources section]", "Assigned Reading", "Chapter 1", "[/box]", "Multimedia:", "Watch the clip."]
    assert texts.count("Assigned Reading") == 1


def test_page_chrome_is_a_note_in_the_qa_report(ownership_bundle: dict) -> None:
    qa = ownership_bundle["qa"]
    assert any(n.startswith("Page chrome: 1 element(s)") for n in qa["notes"])
    assert not any("Page chrome" in b for b in qa["breaks"]) and not any("Page chrome" in w for w in qa["warnings"])


# --------------------------------------------------------------------------- #
# DOCX page flow: a heading that opens a cell may break from the row's next paragraph
# --------------------------------------------------------------------------- #
def test_a_heading_that_opens_a_cell_does_not_keep_with_next(tmp_path: Path) -> None:
    """Controlled comparison of 2026-10-13 (MBA 620 August export, Word and
    LibreOffice): keep-with-next on the heading that opens a week's value cell
    made both engines start the row on a fresh page and leave the previous one
    half empty; seven under-filled pages, 81 pages instead of 76. Only the
    cell-opening heading is relaxed; a heading further down keeps its attachment."""
    docx = pytest.importorskip("docx")
    import blueprint_to_docx as writer
    week = {
        "title": "Week 1", "overview": [
            {"kind": "label", "level": 1, "runs": [{"text": "Week 1 Overview:", "href": ""}]},
            {"kind": "p", "level": 0, "runs": [{"text": "Intro.", "href": ""}]},
            {"kind": "label", "level": 2, "runs": [{"text": "Later heading", "href": ""}]},
            {"kind": "p", "level": 0, "runs": [{"text": "Body.", "href": ""}]},
        ],
        "learning_objectives": [], "resources": [], "assignments": [], "discussions": [], "checklist": [], "other_sections": [],
    }
    model = {"course_title": "T", "front_matter": {}, "before_week_1": [], "weeks": [week], "unplaced_activities": {}, "diagnostics": []}
    doc = docx.Document()
    writer.render(model, doc)
    path = tmp_path / "flow.docx"
    doc.save(str(path))
    cell_paragraphs = [p for table in docx.Document(str(path)).tables for row in table.rows for cell in row.cells for p in cell.paragraphs if p.text]
    by_text = {p.text: p for p in cell_paragraphs}
    assert by_text["Week 1 Overview"].style.name == "Heading 2"
    assert by_text["Week 1 Overview"].paragraph_format.keep_with_next is False  # explicit override of the style's keep
    assert by_text["Later heading"].paragraph_format.keep_with_next is None  # inherits the heading style's keep-with-next
    assert writer.relax_cell_opening_headings(docx.Document(str(path))) == 1
