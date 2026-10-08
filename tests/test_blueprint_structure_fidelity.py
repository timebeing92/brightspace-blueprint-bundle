"""Structure fidelity of the export-to-blueprint path.

Covers the source structure the reader must carry into the blueprint model as
native structure (list items, numbered lists, line breaks, deep headings) and
how the Markdown and DOCX writers render it. Every input here is a small
synthetic page or export.
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

SCHEMA_PATH = REPO_ROOT / "schemas" / "blueprint_schema.json"  # the bundle's shipped copy of the Workbench schema


def block_text(block: dict) -> str:
    return "".join(run["text"] for run in block["runs"])


def shape(blocks: list[dict]) -> list[tuple]:
    """(kind, level, list meta, text) per block, for compact comparisons."""
    return [
        (block["kind"], block["level"], block.get("meta", {}).get("list", ""), block_text(block))
        for block in blocks
    ]


def docx_writer():
    pytest.importorskip("docx")
    import blueprint_to_docx

    return blueprint_to_docx


def paragraph_block(text: str) -> dict:
    return {"kind": "p", "level": 0, "runs": [{"text": text, "href": ""}]}


def list_block(text: str, level: int = 1, *, ordered: bool = False) -> dict:
    block = {"kind": "li", "level": level, "runs": [{"text": text, "href": ""}]}
    if ordered:
        block["meta"] = {"list": "ordered"}
    return block


def label_block(text: str, level: int = 0) -> dict:
    return {"kind": "label", "level": level, "runs": [{"text": text, "href": ""}]}


# --------------------------------------------------------------------------- #
# Reader: list items
# --------------------------------------------------------------------------- #
def test_list_item_wrapping_a_paragraph_stays_a_list_item() -> None:
    raw = """
    <ul>
      <li><p>First item</p></li>
      <li><div><p>Second item</p></div><p>More detail on the second item.</p></li>
      <li>Third item
        <ul>
          <li><p>Nested item</p></li>
        </ul>
      </li>
    </ul>
    <p>After the list.</p>
    """

    blocks = structure.html_fragment_to_blocks(raw)

    assert shape(blocks) == [
        ("li", 1, "", "First item"),
        ("li", 1, "", "Second item"),
        ("p", 0, "", "More detail on the second item."),
        ("li", 1, "", "Third item"),
        ("li", 2, "", "Nested item"),
        ("p", 0, "", "After the list."),
    ]


def test_empty_or_unclosed_list_items_do_not_leak_list_kind() -> None:
    empty_item = structure.html_fragment_to_blocks("<ul><li></li></ul><p>Plain paragraph.</p>")
    assert shape(empty_item) == [("p", 0, "", "Plain paragraph.")]

    unclosed = structure.html_fragment_to_blocks("<ul><li>One<li><p>Two</p></ul><p>Plain paragraph.</p>")
    assert shape(unclosed) == [
        ("li", 1, "", "One"),
        ("li", 1, "", "Two"),
        ("p", 0, "", "Plain paragraph."),
    ]


def test_ordered_list_items_are_marked_and_nested_lists_follow_nearest_list() -> None:
    raw = """
    <ol>
      <li>Step one</li>
      <li><p>Step two</p>
        <ul>
          <li>Bullet under step two
            <ol><li>Numbered under the bullet</li></ol>
          </li>
        </ul>
      </li>
      <li>Step three</li>
    </ol>
    <ul>
      <li>Plain bullet
        <ol><li>Numbered under a bullet</li></ol>
      </li>
    </ul>
    """

    blocks = structure.html_fragment_to_blocks(raw)

    assert shape(blocks) == [
        ("li", 1, "ordered", "Step one"),
        ("li", 1, "ordered", "Step two"),
        ("li", 2, "", "Bullet under step two"),
        ("li", 3, "ordered", "Numbered under the bullet"),
        ("li", 1, "ordered", "Step three"),
        ("li", 1, "", "Plain bullet"),
        ("li", 2, "ordered", "Numbered under a bullet"),
    ]
    # Unordered items carry no list marker at all, so existing models keep their shape.
    assert "meta" not in blocks[2]
    assert blocks[0]["meta"] == {"list": "ordered"}


# --------------------------------------------------------------------------- #
# Reader: line breaks
# --------------------------------------------------------------------------- #
def test_br_stays_inside_its_block_as_a_line_break() -> None:
    raw = """<p>
      Line one <br> Line
      two<br/>Line three<br>
    </p>"""

    blocks = structure.html_fragment_to_blocks(raw)

    assert shape(blocks) == [("p", 0, "", "Line one\nLine two\nLine three")]
    assert structure.blocks_to_text(blocks) == "Line one Line two Line three"


def test_leading_trailing_and_standalone_breaks_are_trimmed() -> None:
    blocks = structure.html_fragment_to_blocks(
        "<p><br>Only line<br></p><br><p>Next paragraph</p><p><br></p>"
    )

    assert shape(blocks) == [
        ("p", 0, "", "Only line"),
        ("p", 0, "", "Next paragraph"),
    ]


def test_br_inside_list_item_and_link_keeps_kind_and_link() -> None:
    blocks = structure.html_fragment_to_blocks(
        '<ol><li>Read <a href="https://example.org/a">the first<br>resource</a><br>before class</li></ol>'
    )

    assert len(blocks) == 1
    assert (blocks[0]["kind"], blocks[0]["level"], blocks[0]["meta"]) == ("li", 1, {"list": "ordered"})
    assert blocks[0]["runs"] == [
        {"text": "Read ", "href": ""},
        {"text": "the first", "href": "https://example.org/a"},
        {"text": "\n", "href": ""},
        {"text": "resource", "href": "https://example.org/a"},
        {"text": "\n", "href": ""},
        {"text": "before class", "href": ""},
    ]


# --------------------------------------------------------------------------- #
# Reader: headings
# --------------------------------------------------------------------------- #
def test_h5_and_h6_are_segment_headings() -> None:
    raw = """
    <h2>Main Section</h2><p>Main text.</p>
    <h5>Smaller Section</h5><p>Smaller text.</p>
    <h6>Smallest Section</h6><p>Smallest text.</p>
    """

    segments = structure.html_to_segments(raw)

    assert [(row["heading"], row["level"], row["text"]) for row in segments] == [
        ("Main Section", 2, "Main text."),
        ("Smaller Section", 5, "Smaller text."),
        ("Smallest Section", 6, "Smallest text."),
    ]


def test_fragment_headings_are_label_blocks_with_their_level() -> None:
    """Loss 17: a description that opens with headings used to run their words
    into the first paragraph ("Week 1 Discussion: Opening The Situation")."""
    raw = ('<div style="max-width: 48rem"><h2 style="color: #134f5c">Week 1 Discussion: Opening</h2>'
           "<h3>The Situation</h3><p>Para one.</p><ul><li>a</li></ul><h4>Step <em>two</em></h4><p>More.</p></div>")

    blocks = structure.html_fragment_to_blocks(raw)

    assert [(b["kind"], b["level"], block_text(b)) for b in blocks] == [
        ("label", 2, "Week 1 Discussion: Opening"),
        ("label", 3, "The Situation"),
        ("p", 0, "Para one."),
        ("li", 1, "a"),
        ("label", 4, "Step two"),
        ("p", 0, "More."),
    ]
    assert blocks[4]["runs"][1].get("italic") is True  # emphasis survives inside a heading


def test_a_description_heading_that_repeats_the_title_is_written_once() -> None:
    topic = {
        "title": "Week 1 Discussion: Opening",
        "score_out_of": "4",
        "description_blocks": structure.html_fragment_to_blocks(
            "<h2>Week 1 Discussion: Opening</h2><h3>The Situation</h3><p>Text.</p>"
        ),
    }
    model = {"weeks": [{"assignments": [], "discussions": [blueprint.format_discussion(topic)]}], "unplaced_activities": {}}

    notes = blueprint.drop_repeated_title_headings(model)

    section = model["weeks"][0]["discussions"][0]
    assert [(b["kind"], b["level"]) for b in section["blocks"]] == [("label", 0), ("label", 3), ("p", 0)]
    assert notes == [
        "Activity headings: 1 description opened with a heading repeating the activity's title; "
        "the title is the activity's label line, so that heading is written once."
    ]
    # a heading that is not the title stays, and nothing is reported
    other = {"title": "Other", "description_blocks": structure.html_fragment_to_blocks("<h3>The Situation</h3><p>Text.</p>")}
    model = {"weeks": [{"assignments": [], "discussions": [blueprint.format_discussion(other)]}], "unplaced_activities": {}}
    assert blueprint.drop_repeated_title_headings(model) == []
    assert [b["kind"] for b in model["weeks"][0]["discussions"][0]["blocks"]] == ["label", "p"]


def test_internal_links_become_bracket_runs_and_unmatched_ones_stay_links() -> None:
    """Links to this course's own objects are written as the packager's bracket form.

    The MBA 620 export carries no quicklinks, so the case is synthetic: a dropbox
    quicklink by rCode, a relative page link by file name, a content quicklink by
    the page's resource code, and a quicklink to a code the export does not have."""
    structure = {"tree": [{"kind": "module", "title": "Week 1", "href": "", "resource_code": "M1", "children": [
        {"kind": "html_topic", "title": "Sample Help", "href": "pages/help.html", "resource_code": "CODE-P0", "children": []}]}]}
    activities = {"dropbox_folders": [{"name": "Sample Memo", "resource_code": "CODE-F1"}],
                  "discussions": [{"kind": "topic", "title": "Opening", "resource_code": "CODE-D1"}],
                  "quizzes": [], "checklists": []}
    raw = ('<p>Start with <a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=dropbox&amp;rCode=CODE-F1">the <b>memo</b></a>, '
           'post in <a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=discuss&amp;rCode=CODE-D1">Opening</a>, '
           'read <a href="../pages/help.html#top">Sample Help</a>, '
           'see <a href="https://example.org/x">the web</a>, and ignore '
           '<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=dropbox&amp;rCode=NOPE">a dead link</a>.</p>')
    model = {"weeks": [{"overview": structure_blocks(raw), "learning_objectives": [], "resources": [], "assignments": [],
                        "discussions": [], "checklist": [], "other_sections": []}]}

    notes = blueprint.bracket_internal_links(model, structure, activities)

    runs = model["weeks"][0]["overview"][0]["runs"]
    assert [(r["text"], r["href"], r.get("link_kind", ""), r.get("link_title", "")) for r in runs] == [
        ("Start with ", "", "", ""),
        ("[link to assignment: Sample Memo | the memo]", "", "assignment", "Sample Memo"),
        (", post in ", "", "", ""),
        ("[link to discussion: Opening]", "", "discussion", "Opening"),
        (", read ", "", "", ""),
        ("[link to page: Sample Help]", "", "page", "Sample Help"),
        (", see ", "", "", ""),
        ("the web", "https://example.org/x", "", ""),
        (", and ignore ", "", "", ""),
        ("a dead link", "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=dropbox&rCode=NOPE", "", ""),
        (".", "", "", ""),
    ]
    assert notes == ["Internal links: 1 link(s) to course objects match nothing in this export and stay as hyperlinks "
                     "(/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=dropbox&rCode=NOPE)."]
    # a cue block's href is a reference, not a link in prose: it is left alone
    cue = {"kind": "hidden", "level": 0, "runs": [{"text": "Object: Sample Help; hidden", "href": "pages/help.html"}]}
    model = {"weeks": [{"overview": [cue], "learning_objectives": [], "resources": [], "assignments": [], "discussions": [], "checklist": [], "other_sections": []}]}
    assert blueprint.bracket_internal_links(model, structure, activities) == []
    assert cue["runs"][0]["href"] == "pages/help.html" and "link_kind" not in cue["runs"][0]
    # every bracket the wizard writes is one the packager reads
    for run in runs:
        if run.get("link_kind"):
            assert PACKAGER_INTERNAL_LINK_RE.match(run["text"]), run["text"]


def structure_blocks(raw: str) -> list[dict]:
    return structure.html_fragment_to_blocks(raw)


PACKAGER_INTERNAL_LINK_RE = __import__("re").compile(
    r"\[\s*link to (page|assignment|discussion|quiz|checklist)\s*:\s*([^\]|]+?)\s*(?:\|\s*([^\]]+?)\s*)?\]", __import__("re").I
)


def test_html_title_is_not_page_content() -> None:
    raw = (
        "<html><head><meta charset='utf-8'><title>Unit 3 - Sample Topic</title></head>"
        "<body><h1>Sample Topic</h1><p>Body paragraph.</p></body></html>"
    )

    assert shape(structure.html_fragment_to_blocks(raw)) == [
        ("label", 1, "", "Sample Topic"),  # the page's own h1 is a heading label; the <title> is not content
        ("p", 0, "", "Body paragraph."),
    ]
    segments = structure.html_to_segments(raw)
    assert [(row["heading"], row["level"], row["text"]) for row in segments] == [
        ("Sample Topic", 1, "Body paragraph."),
    ]

    no_headings = "<html><head><title>Unit 3 - Sample Topic</title></head><body><p>Body paragraph.</p></body></html>"
    assert [row["text"] for row in structure.html_to_segments(no_headings)] == ["Body paragraph."]


# --------------------------------------------------------------------------- #
# Markdown renderer
# --------------------------------------------------------------------------- #
def test_markdown_numbers_ordered_items_and_restarts_each_list() -> None:
    blocks = [
        list_block("Open the form.", ordered=True),
        list_block("Fill in every field.", ordered=True),
        list_block("A nested note", level=2),
        list_block("Submit the form.", ordered=True),
        paragraph_block("Then wait for a reply."),
        list_block("Read the reply.", ordered=True),
        list_block("First sub-step", level=2, ordered=True),
        list_block("Second sub-step", level=2, ordered=True),
        list_block("Archive the reply.", ordered=True),
        list_block("Only sub-step", level=2, ordered=True),
        list_block("A plain bullet"),
    ]

    rendered = blueprint.md_blocks(blocks, "")

    assert rendered == (
        "1. Open the form."
        "<br>2. Fill in every field."
        "<br>&nbsp;&nbsp;&nbsp;• A nested note"
        "<br>3. Submit the form."
        "<br><br>Then wait for a reply."
        "<br><br>1. Read the reply."
        "<br>&nbsp;&nbsp;&nbsp;1. First sub-step"
        "<br>&nbsp;&nbsp;&nbsp;2. Second sub-step"
        "<br>2. Archive the reply."
        "<br>&nbsp;&nbsp;&nbsp;1. Only sub-step"
        "<br>• A plain bullet"
    )


def test_markdown_renders_line_break_without_leaving_the_table_cell() -> None:
    blocks = [
        {
            "kind": "p",
            "level": 0,
            "runs": [
                {"text": "Line one", "href": ""},
                {"text": "\n", "href": ""},
                {"text": "the link", "href": "https://example.org/a"},
                {"text": "\n", "href": ""},
                {"text": "Line | three", "href": ""},
            ],
        }
    ]

    rendered = blueprint.md_blocks(blocks, "")

    assert rendered == "Line one<br>[the link](https://example.org/a)<br>Line \\| three"
    assert "\n" not in rendered


def test_markdown_heading_labels_are_unchanged() -> None:
    rendered = blueprint.md_blocks(
        [label_block("Key Concepts:", level=2), paragraph_block("Body."), label_block("Points: 5 pts")],
        "",
    )

    assert rendered == "**Key Concepts:**<br><br>Body.<br><br>**Points: 5 pts**"


# --------------------------------------------------------------------------- #
# DOCX writer
# --------------------------------------------------------------------------- #
def render_blocks_to_docx(tmp_path: Path, blocks: list[dict], *, prepare=None):
    """Write blocks with the DOCX writer, save, and reopen the saved file."""
    writer = docx_writer()
    import docx

    doc = docx.Document()
    writer.clear_body(doc)
    if prepare is not None:
        prepare(doc)
    writer.write_blocks(doc, blocks, missing="(missing)")
    path = tmp_path / "blocks.docx"
    doc.save(str(path))
    return docx.Document(str(path))


def num_pr(paragraph) -> tuple[str, str] | None:
    """(numId, ilvl) set directly on a paragraph, or None."""
    from docx.oxml.ns import qn

    ppr = paragraph._p.pPr
    node = ppr.find(qn("w:numPr")) if ppr is not None else None
    if node is None:
        return None
    return node.find(qn("w:numId")).get(qn("w:val")), node.find(qn("w:ilvl")).get(qn("w:val"))


def numbering_instances(doc) -> dict[str, dict]:
    """numId -> {abstract, starts: {ilvl: start}} for every numbering instance."""
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml.ns import qn

    root = doc.part.part_related_by(RELATIONSHIP_TYPE.NUMBERING).element
    instances = {}
    for num in root.findall(qn("w:num")):
        starts = {}
        for override in num.findall(qn("w:lvlOverride")):
            start = override.find(qn("w:startOverride"))
            if start is not None:
                starts[override.get(qn("w:ilvl"))] = start.get(qn("w:val"))
        instances[num.get(qn("w:numId"))] = {
            "abstract": num.find(qn("w:abstractNumId")).get(qn("w:val")),
            "starts": starts,
        }
    return instances


def style_num_id(doc, style_name: str) -> str:
    from docx.oxml.ns import qn

    return doc.styles[style_name].element.find(qn("w:pPr")).find(qn("w:numPr")).find(qn("w:numId")).get(qn("w:val"))


ORDERED_DEMO_BLOCKS = [
    list_block("Open the form.", ordered=True),
    list_block("Fill in every field.", ordered=True),
    list_block("A nested bullet", level=2),
    list_block("Submit the form.", ordered=True),
    paragraph_block("Then wait for a reply."),
    list_block("Read the reply.", ordered=True),
    list_block("First sub-step", level=2, ordered=True),
    list_block("Archive the reply.", ordered=True),
    list_block("Other sub-step", level=2, ordered=True),
    list_block("A plain bullet"),
]


def test_docx_ordered_items_use_list_number_styles_and_restart_per_list(tmp_path: Path) -> None:
    doc = render_blocks_to_docx(tmp_path, ORDERED_DEMO_BLOCKS)
    paragraphs = doc.paragraphs

    assert [p.text for p in paragraphs] == [block_text(block) for block in ORDERED_DEMO_BLOCKS]
    assert [p.style.name for p in paragraphs] == [
        "List Number",
        "List Number",
        "List Bullet 2",
        "List Number",
        "Normal",
        "List Number",
        "List Number 2",
        "List Number",
        "List Number 2",
        "List Bullet",
    ]
    # No typed digits: the numbers come from Word numbering, not from run text.
    assert not any(p.text[:1].isdigit() for p in paragraphs)

    refs = [num_pr(p) for p in paragraphs]
    first_list, second_list = refs[0], refs[5]
    first_sub, second_sub = refs[6], refs[8]
    assert first_list is not None and second_list is not None
    # One numbering instance per list: the nested bullet does not restart its parent.
    assert refs[1] == first_list and refs[3] == first_list
    assert refs[7] == second_list
    assert len({first_list[0], second_list[0], first_sub[0], second_sub[0]}) == 4
    # Bullets and the paragraph carry no direct numbering of their own.
    assert refs[2] is None and refs[4] is None and refs[9] is None

    instances = numbering_instances(doc)
    level_1_abstract = instances[style_num_id(doc, "List Number")]["abstract"]
    level_2_abstract = instances[style_num_id(doc, "List Number 2")]["abstract"]
    for num_id, ilvl in (first_list, second_list):
        assert instances[num_id] == {"abstract": level_1_abstract, "starts": {ilvl: "1"}}
    for num_id, ilvl in (first_sub, second_sub):
        assert instances[num_id] == {"abstract": level_2_abstract, "starts": {ilvl: "1"}}
    # The style's own numbering instance is left alone for other users of the style.
    assert first_list[0] != style_num_id(doc, "List Number")


def test_docx_ordered_items_fall_back_to_template_numbering_without_list_number_styles(
    tmp_path: Path,
) -> None:
    def drop_list_number_styles(doc) -> None:
        for name in ("List Number", "List Number 2", "List Number 3"):
            doc.styles[name].delete()

    blocks = [
        list_block("Step one", ordered=True),
        list_block("Step two", ordered=True),
        paragraph_block("Between the lists."),
        list_block("Step one again", ordered=True),
    ]
    doc = render_blocks_to_docx(tmp_path, blocks, prepare=drop_list_number_styles)
    paragraphs = doc.paragraphs

    assert [p.text for p in paragraphs] == [block_text(block) for block in blocks]
    assert [p.style.name for p in paragraphs] == ["Normal"] * 4
    refs = [num_pr(p) for p in paragraphs]
    assert refs[0] is not None and refs[0] == refs[1]
    assert refs[3] is not None and refs[3][0] != refs[0][0]
    assert refs[2] is None
    instances = numbering_instances(doc)
    assert instances[refs[0][0]]["starts"] == {refs[0][1]: "1"}
    assert instances[refs[3][0]]["starts"] == {refs[3][1]: "1"}

    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml.ns import qn

    root = doc.part.part_related_by(RELATIONSHIP_TYPE.NUMBERING).element
    formats = {
        abstract.get(qn("w:abstractNumId")): abstract.find(qn("w:lvl")).find(qn("w:numFmt")).get(qn("w:val"))
        for abstract in root.findall(qn("w:abstractNum"))
    }
    assert formats[instances[refs[0][0]]["abstract"]] == "decimal"


def test_docx_ordered_items_without_any_numbering_part_use_counted_text(
    tmp_path: Path, monkeypatch
) -> None:
    writer = docx_writer()
    monkeypatch.setattr(writer, "_numbering_root", lambda part: None)
    blocks = [
        list_block("Step one", ordered=True),
        list_block("Step two", ordered=True),
        paragraph_block("Between the lists."),
        list_block("Step one again", ordered=True),
    ]

    doc = render_blocks_to_docx(tmp_path, blocks)

    assert [p.text for p in doc.paragraphs] == [
        "1. Step one",
        "2. Step two",
        "Between the lists.",
        "1. Step one again",
    ]
    # Typed numbers never sit on a numbering style, so they cannot double up.
    assert [p.style.name for p in doc.paragraphs] == ["Normal"] * 4
    assert all(num_pr(p) is None for p in doc.paragraphs)


def test_docx_line_break_is_a_real_break_inside_one_paragraph(tmp_path: Path) -> None:
    from docx.oxml.ns import qn

    blocks = [
        {
            "kind": "p",
            "level": 0,
            "runs": [
                {"text": "Line one", "href": ""},
                {"text": "\n", "href": ""},
                {"text": "linked\nline", "href": "https://example.org/a"},
                {"text": "\n", "href": ""},
                {"text": "Line four", "href": ""},
            ],
        }
    ]

    doc = render_blocks_to_docx(tmp_path, blocks)

    assert len(doc.paragraphs) == 1
    paragraph = doc.paragraphs[0]
    assert len(paragraph._p.findall(f".//{qn('w:br')}")) == 3
    # No line break survives as a raw newline inside a text element.
    assert all("\n" not in (node.text or "") for node in paragraph._p.iter(qn("w:t")))
    hyperlinks = paragraph._p.findall(qn("w:hyperlink"))
    assert len(hyperlinks) == 1
    assert [node.text for node in hyperlinks[0].iter(qn("w:t"))] == ["linked", "line"]
    assert len(hyperlinks[0].findall(f".//{qn('w:br')}")) == 1


def test_docx_heading_labels_use_heading_styles_and_metadata_labels_do_not(tmp_path: Path) -> None:
    blocks = [
        label_block("Top Section:", level=1),
        paragraph_block("Top text."),
        label_block("Key Concepts:", level=2),
        label_block("Details:", level=3),
        label_block("Finer Details:", level=4),
        label_block("Fine Print:", level=5),
        label_block("Finer Print:", level=6),
        label_block("Points: 5 pts"),
        label_block("Weekly Tasks:"),
    ]

    doc = render_blocks_to_docx(tmp_path, blocks)
    paragraphs = doc.paragraphs

    # One to one with the source: h2 is Heading 2, h3 is Heading 3, and so on.
    # A source h1 inside page content is Heading 2 because the page heading
    # line is the page's h1; Heading 1 belongs to the frame.
    assert [(p.style.name, p.text) for p in paragraphs] == [
        ("Heading 2", "Top Section"),
        ("Normal", "Top text."),
        ("Heading 2", "Key Concepts"),
        ("Heading 3", "Details"),
        ("Heading 4", "Finer Details"),
        ("Heading 5", "Fine Print"),
        ("Heading 6", "Finer Print"),
        ("Normal", "Points: 5 pts"),
        ("Normal", "Weekly Tasks:"),
    ]
    # Level-0 labels (activity metadata, checklist categories) stay bold lines.
    assert all(run.bold for run in paragraphs[7].runs)
    assert all(run.bold for run in paragraphs[8].runs)


def test_docx_heading_label_falls_back_when_heading_styles_are_missing(tmp_path: Path) -> None:
    def drop_deep_heading_styles(doc) -> None:
        for name in ("Heading 3", "Heading 4", "Heading 5", "Heading 6"):
            doc.styles[name].delete()

    doc = render_blocks_to_docx(
        tmp_path,
        [label_block("Details:", level=3), label_block("Fine Print:", level=5)],
        prepare=drop_deep_heading_styles,
    )
    # The nearest shallower page-heading style still makes a real heading,
    # and never Heading 1, which is the frame's.
    assert [(p.style.name, p.text) for p in doc.paragraphs] == [
        ("Heading 2", "Details"),
        ("Heading 2", "Fine Print"),
    ]

    def drop_all_page_heading_styles(doc) -> None:
        for name in ("Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6"):
            doc.styles[name].delete()

    plain = render_blocks_to_docx(
        tmp_path,
        [label_block("Details:", level=3)],
        prepare=drop_all_page_heading_styles,
    )
    # With no heading style to use, the label renders as the bold line it was before.
    assert [(p.style.name, p.text) for p in plain.paragraphs] == [("Normal", "Details:")]
    assert all(run.bold for run in plain.paragraphs[0].runs)


# --------------------------------------------------------------------------- #
# Whole pipeline on a tiny synthetic export
# --------------------------------------------------------------------------- #
OVERVIEW_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Week 1 - Sample Overview Title</title></head>
<body>
<h1>Sample Week Frame</h1>
<p>Opening paragraph for the sample week.</p>
<ul><li><p>Wrapped bullet</p></li></ul>
<h2>Sample Steps</h2>
<ol>
  <li>Open the sample form.</li>
  <li><p>Fill in every field.</p></li>
  <li>Submit the form.</li>
</ol>
<p>Between the two lists.</p>
<ol><li>Start the second list.</li></ol>
<p>Contact line one<br>Contact line two</p>
<p>Read the <strong>bold guide</strong> and the <em>italic <a href="https://example.org/note">note</a></em>, then the <span style="font-weight: 700">weighted span</span>.</p>
<table>
  <thead><tr><th>Day</th><th>Task</th></tr></thead>
  <tbody>
    <tr><td>Monday</td><td><p>Read the brief.</p><ul><li>Skim <b>first</b></li><li>Then read</li></ul></td></tr>
    <tr><td>Friday</td><td>Submit the memo.</td></tr>
  </tbody>
</table>
<h5>Sample Fine Print</h5>
<p>Text under a level five heading.</p>
<h6>Sample Finer Print</h6>
<p>Text under a level six heading.</p>
<p>Start with <a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=dropbox&amp;rCode=CODE-F1">the <b>memo</b></a>,
read <a href="help.html">Sample Help</a> and <a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=content&amp;rCode=CODE-P0">the help page</a>,
and ignore <a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=dropbox&amp;rCode=NOPE">a dead link</a>.</p>
</body></html>
"""

HELP_PAGE = """<html><head><title>Sample Help Title</title></head><body>
<h1>Sample Help</h1>
<p>Help intro paragraph.</p>
<h3>Sample Help Steps</h3>
<ol><li>Ask a sample question.</li></ol>
</body></html>
"""

POLICY_PAGE = """<html><head><title>Sample Policies</title></head><body>
<p>Policy intro paragraph.</p>
</body></html>
"""

CONTACT_PAGE = """<html><head><title>Sample Contacts</title></head><body>
<p>Contact intro paragraph.</p>
</body></html>
"""

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns:d2l_2p0="http://desire2learn.com/xsd/d2lcp_v2p0" identifier="M1">
  <organizations>
    <organization identifier="ORG1">
      <item identifier="M0" identifierref="RES_MOD0">
        <title>Getting Started</title>
        <item identifier="P0" identifierref="RES_HELP" resource_code="CODE-P0"><title>Sample Help</title></item>
        <item identifier="P0B" identifierref="RES_POLICY"><title>Sample Policies</title></item>
      </item>
      <item identifier="M0C" identifierref="RES_MOD0C">
        <title>Faculty Corner</title>
        <item identifier="P0C" identifierref="RES_CONTACT"><title>Sample Contacts</title></item>
      </item>
      <item identifier="M1" identifierref="RES_MOD1">
        <title>Week 1</title>
        <item identifier="P1" identifierref="RES_OVERVIEW"><title>Week 1 Overview</title></item>
        <item identifier="A1" identifierref="RES_DROPBOX"><title>Sample Assignment</title></item>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="RES_MOD0" type="webcontent" d2l_2p0:material_type="contentmodule" />
    <resource identifier="RES_MOD1" type="webcontent" d2l_2p0:material_type="contentmodule" />
    <resource identifier="RES_MOD0C" type="webcontent" d2l_2p0:material_type="contentmodule" />
    <resource identifier="RES_HELP" type="webcontent" d2l_2p0:material_type="content" href="pages/help.html" />
    <resource identifier="RES_POLICY" type="webcontent" d2l_2p0:material_type="content" href="pages/policy.html" />
    <resource identifier="RES_CONTACT" type="webcontent" d2l_2p0:material_type="content" href="pages/contact.html" />
    <resource identifier="RES_OVERVIEW" type="webcontent" d2l_2p0:material_type="content" href="pages/overview.html" />
    <resource identifier="RES_DROPBOX" type="webcontent" d2l_2p0:material_type="contentlink"
      href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=dropbox&amp;rcode=CODE-F1" />
  </resources>
</manifest>
"""

DROPBOX = """<?xml version="1.0" encoding="UTF-8"?>
<dropbox>
  <folder name="Sample Memo" id="1" out_of="5" resource_code="CODE-F1">
    <instructions text_type="text/html"><text>&lt;div style="max-width: 48rem"&gt;&lt;h2&gt;Sample Memo&lt;/h2&gt;&lt;h3&gt;The Situation&lt;/h3&gt;&lt;p&gt;Write the memo.&lt;/p&gt;&lt;ol&gt;&lt;li&gt;Draft it.&lt;/li&gt;&lt;li&gt;&lt;p&gt;Revise it.&lt;/p&gt;&lt;/li&gt;&lt;/ol&gt;&lt;h4&gt;Format&lt;/h4&gt;&lt;p&gt;One page.&lt;/p&gt;&lt;/div&gt;</text></instructions>
  </folder>
</dropbox>
"""


@pytest.fixture(scope="module")
def synthetic_bundle(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("structure_fidelity")
    export = root / "export"
    (export / "pages").mkdir(parents=True)
    (export / "imsmanifest.xml").write_text(MANIFEST, encoding="utf-8")
    (export / "dropbox_d2l.xml").write_text(DROPBOX, encoding="utf-8")
    (export / "pages" / "overview.html").write_text(OVERVIEW_PAGE, encoding="utf-8")
    (export / "pages" / "help.html").write_text(HELP_PAGE, encoding="utf-8")
    (export / "pages" / "policy.html").write_text(POLICY_PAGE, encoding="utf-8")
    (export / "pages" / "contact.html").write_text(CONTACT_PAGE, encoding="utf-8")
    output_dir = root / "review"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_blueprint_bundle.py",
            str(export),
            "--label",
            "fidelity",
            "--course-title",
            "Synthetic Fidelity Course",
            "--output-dir",
            str(output_dir),
            "--no-syllabus-fetch",
            "--skip-qa",
            "--quiet",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bundle = output_dir / "fidelity__blueprint_bundle"
    return {
        "result": result,
        "bundle": bundle,
        "model": json.loads((bundle / "fidelity__blueprint.json").read_text(encoding="utf-8")),
        "structure": json.loads((bundle / "fidelity__course_structure.json").read_text(encoding="utf-8")),
        "markdown": (bundle / "fidelity__blueprint.md").read_text(encoding="utf-8"),
    }


def iter_model_blocks(model: dict):
    def walk(blocks):
        for block in blocks or []:
            yield block
            yield from walk(block.get("blocks"))

    for blocks in model["front_matter"].values():
        yield from walk(blocks)
    for section in model["before_week_1"]:
        yield from walk(section["blocks"])
    for week in model["weeks"]:
        yield from walk(week.get("overview"))
        yield from walk(week.get("learning_objectives"))
        for key in ("resources", "assignments", "discussions", "checklist", "other_sections"):
            for section in week.get(key, []):
                yield from walk(section["blocks"])


def test_bundle_model_with_new_structure_validates_against_blueprint_schema(synthetic_bundle: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from course_artifact_contracts import validate_contract

    model = synthetic_bundle["model"]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "coursecraft.blueprint/5"
    assert model["schema"] == "coursecraft.blueprint/5"
    assert list(jsonschema.Draft7Validator(schema).iter_errors(model)) == []
    assert validate_contract(model) == []
    assert "failed schema validation" not in synthetic_bundle["result"].stderr

    # The model really exercises every new case, so the validation is not vacuous.
    blocks = list(iter_model_blocks(model))
    assert any(block["kind"] == "li" and block.get("meta") == {"list": "ordered"} for block in blocks)
    assert any(block["kind"] == "li" and "meta" not in block for block in blocks)
    assert any(run["text"] == "\n" for block in blocks for run in block["runs"])
    assert any(block["kind"] == "label" and block["level"] in (5, 6) for block in blocks)
    assert any(block["kind"] == "label" and block["level"] == 0 for block in blocks)
    # Revision 5: emphasis flags, a table block, and module names, all exercised.
    runs = [run for block in blocks for run in block["runs"]]
    assert any(run.get("bold") for run in runs) and any(run.get("italic") for run in runs)
    assert all(set(run) <= {"text", "href", "bold", "italic", "underline", "link_kind", "link_title"} for run in runs)
    # Internal links in the overview page resolved to bracket runs; the dead one stayed a hyperlink.
    assert sorted(run["link_kind"] for run in runs if run.get("link_kind")) == ["assignment", "page", "page"]
    assert any("rCode=NOPE" in run["href"] for run in runs)
    assert any(d.startswith("Internal links: 1 link(s)") for d in model["diagnostics"])
    assert all(run[flag] is True for run in runs for flag in ("bold", "italic", "underline") if flag in run)
    assert any(block["kind"] == "table" and block["header_rows"] == 1 for block in blocks)
    assert [section["module"] for section in model["before_week_1"]] == ["Getting Started", "Getting Started", "Faculty Corner"]
    # A revision-4 model still validates through the contracts registry.
    four = json.loads(json.dumps(model))
    four["schema"] = "coursecraft.blueprint/4"
    strip = lambda block: [{"text": r["text"], "href": r["href"]} for r in block["runs"]]  # noqa: E731
    for block in iter_model_blocks(four):
        block["runs"] = strip(block)
        block.pop("rows", None)
        block.pop("header_rows", None)
        if block["kind"] == "table":
            block["kind"] = "p"
    for section in four["before_week_1"]:
        section.pop("module")
    assert validate_contract(four) == []


def test_bundle_keeps_html_title_as_metadata_only(synthetic_bundle: dict) -> None:
    topics = {topic["manifest_title"]: topic for topic in synthetic_bundle["structure"]["html_topics"]}

    assert topics["Week 1 Overview"]["html_title"] == "Week 1 - Sample Overview Title"
    assert topics["Sample Help"]["html_title"] == "Sample Help Title"
    for topic in topics.values():
        segment_text = json.dumps(topic["body_segments"])
        assert topic["html_title"] not in segment_text
    model_blocks_text = json.dumps(
        [synthetic_bundle["model"]["weeks"], synthetic_bundle["model"]["before_week_1"]]
    )
    assert "Sample Overview Title" not in model_blocks_text
    assert "Sample Help Title" not in model_blocks_text
    content = synthetic_bundle["markdown"].split("## Extraction Notes")[0]
    assert "Sample Overview Title" not in content
    assert "Sample Help Title" not in content


def test_bundle_carries_lists_breaks_and_deep_headings_into_the_week(synthetic_bundle: dict) -> None:
    overview = synthetic_bundle["model"]["weeks"][0]["overview"]

    assert shape(overview) == [
        ("label", 1, "", "Sample Week Frame:"),
        ("p", 0, "", "Opening paragraph for the sample week."),
        ("li", 1, "", "Wrapped bullet"),
        ("label", 2, "", "Sample Steps:"),
        ("li", 1, "ordered", "Open the sample form."),
        ("li", 1, "ordered", "Fill in every field."),
        ("li", 1, "ordered", "Submit the form."),
        ("p", 0, "", "Between the two lists."),
        ("li", 1, "ordered", "Start the second list."),
        ("p", 0, "", "Contact line one\nContact line two"),
        ("p", 0, "", "Read the bold guide and the italic note, then the weighted span."),
        ("table", 0, "", ""),
        ("label", 5, "", "Sample Fine Print:"),
        ("p", 0, "", "Text under a level five heading."),
        ("label", 6, "", "Sample Finer Print:"),
        ("p", 0, "", "Text under a level six heading."),
        ("p", 0, "", "Start with [link to assignment: Sample Memo | the memo], read [link to page: Sample Help] and "
                     "[link to page: Sample Help | the help page], and ignore a dead link."),
    ]
    # Activity instructions go through the same reader: headings inside a
    # description are heading labels with their level; the one that repeats
    # the activity's title is dropped, since the title is the label line.
    assignment = synthetic_bundle["model"]["weeks"][0]["assignments"][0]
    assert shape(assignment["blocks"]) == [
        ("label", 0, "", "Points: 5 pts"),
        ("label", 3, "", "The Situation"),
        ("p", 0, "", "Write the memo."),
        ("li", 1, "ordered", "Draft it."),
        ("li", 1, "ordered", "Revise it."),
        ("label", 4, "", "Format"),
        ("p", 0, "", "One page."),
    ]
    assert any(d.startswith("Activity headings: 1 description opened with a heading repeating") for d in synthetic_bundle["model"]["diagnostics"])
    # Before-week pages label their own headings with the source heading level.
    help_section = synthetic_bundle["model"]["before_week_1"][0]
    assert shape(help_section["blocks"]) == [
        ("p", 0, "", "Help intro paragraph."),
        ("label", 3, "", "Sample Help Steps:"),
        ("li", 1, "ordered", "Ask a sample question."),
    ]


def test_bundle_markdown_keeps_table_rows_on_one_line(synthetic_bundle: dict) -> None:
    markdown = synthetic_bundle["markdown"]

    assert (
        "**Sample Steps:**<br><br>1. Open the sample form.<br>2. Fill in every field.<br>3. Submit the form."
        "<br><br>Between the two lists.<br><br>1. Start the second list."
        "<br><br>Contact line one<br>Contact line two"
    ) in markdown
    assert "**Sample Fine Print:**" in markdown
    assert "**Points: 5 pts**" in markdown
    week_table = markdown[markdown.index("### Week 1"):markdown.index("## Extraction Notes")]
    rows = [line for line in week_table.splitlines() if line.startswith("|")]
    assert rows and all(line.endswith("|") for line in rows)


def test_bundle_docx_uses_native_structure_inside_week_table(synthetic_bundle: dict) -> None:
    docx = pytest.importorskip("docx")
    from docx.oxml.ns import qn

    doc = docx.Document(str(synthetic_bundle["bundle"] / "fidelity__blueprint.docx"))
    cell_paragraphs = [
        paragraph
        for table in doc.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    ]
    by_text = {paragraph.text: paragraph for paragraph in cell_paragraphs}

    assert by_text["Sample Week Frame"].style.name == "Heading 2"  # a source h1: see the diagnostic
    assert by_text["Sample Steps"].style.name == "Heading 2"
    assert by_text["Sample Help Steps"].style.name == "Heading 3"
    assert by_text["Sample Fine Print"].style.name == "Heading 5"
    assert by_text["Sample Finer Print"].style.name == "Heading 6"
    assert by_text["Points: 5 pts"].style.name == "Normal"
    assert by_text["Wrapped bullet"].style.name == "List Bullet"

    first = [by_text[text] for text in ("Open the sample form.", "Fill in every field.", "Submit the form.")]
    second = by_text["Start the second list."]
    assert all(paragraph.style.name == "List Number" for paragraph in [*first, second])
    assert len({num_pr(paragraph) for paragraph in first}) == 1
    assert num_pr(second)[0] != num_pr(first[0])[0]
    instances = numbering_instances(doc)
    assert instances[num_pr(first[0])[0]]["starts"] == {"0": "1"}
    assert instances[num_pr(second)[0]]["starts"] == {"0": "1"}

    contact = by_text["Contact line one\nContact line two"]
    assert len(contact._p.findall(f".//{qn('w:br')}")) == 1
    # The frame is Heading 1: the week title and the other top-level sections.
    assert [p.style.name for p in doc.paragraphs if p.text == "Week 1"] == ["Heading 1"]
    frame = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert frame[:3] == ["Course Introduction", blueprint.BEFORE_WEEK_LABEL, "Week 1"]
    assert "Extraction Notes" in frame
    assert not any(p.style.name == "Heading 1" for p in cell_paragraphs)


def test_bundle_explains_the_one_heading_level_that_is_not_one_to_one(synthetic_bundle: dict) -> None:
    model = synthetic_bundle["model"]
    h1_labels = [
        block for block in iter_model_blocks(model) if block["kind"] == "label" and block["level"] == 1
    ]
    # The overview opens with an <h1> that is not its title. (The help page's
    # <h1> repeats the page title, so the before-week builder drops it.)
    assert [block_text(block) for block in h1_labels] == ["Sample Week Frame:"]
    notes = [line for line in model["diagnostics"] if line.startswith("Heading levels:")]
    assert notes == [
        "Heading levels: 1 h1 heading inside page content is written as Heading 2 in the Word blueprint, "
        "because the page heading line is the page's h1. Every other heading level is one to one."
    ]
    two = {"weeks": [{"overview": [label_block("One:", level=1)], "resources": [{"blocks": [label_block("Two:", level=1)]}]}]}
    assert blueprint.heading_level_diagnostics(two)[0].startswith("Heading levels: 2 h1 headings inside page content are")
    assert blueprint.heading_level_diagnostics({"weeks": [{"overview": [label_block("Plain:", level=2)]}]}) == []


# --------------------------------------------------------------------------- #
# Revision 5: emphasis, tables, modules
# --------------------------------------------------------------------------- #
def flags(run: dict) -> tuple:
    return (run["text"], run["href"], run.get("bold", False), run.get("italic", False), run.get("underline", False))


def test_emphasis_tags_and_inline_styles_mark_runs() -> None:
    raw = (
        "<p>Plain <strong>bold</strong> and <b>b</b>, <em>italic</em> and <i>i</i>, <u>under</u>, "
        '<span style="font-weight:bold">styled bold</span>, <span style="font-weight: 600">weighted</span>, '
        '<span style="font-style: italic">styled italic</span>, <span style="text-decoration: underline red">styled under</span>, '
        "<strong><em><a href=\"https://example.org\">bold italic link</a></em></strong> end.</p>"
    )

    blocks = structure.html_fragment_to_blocks(raw)

    assert len(blocks) == 1
    assert [flags(run) for run in blocks[0]["runs"]] == [
        ("Plain ", "", False, False, False),
        ("bold", "", True, False, False),
        (" and ", "", False, False, False),
        ("b", "", True, False, False),
        (", ", "", False, False, False),
        ("italic", "", False, True, False),
        (" and ", "", False, False, False),
        ("i", "", False, True, False),
        (", ", "", False, False, False),
        ("under", "", False, False, True),
        (", ", "", False, False, False),
        ("styled bold", "", True, False, False),
        (", ", "", False, False, False),
        ("weighted", "", True, False, False),
        (", ", "", False, False, False),
        ("styled italic", "", False, True, False),
        (", ", "", False, False, False),
        ("styled under", "", False, False, True),
        (", ", "", False, False, False),
        ("bold italic link", "https://example.org", True, True, False),
        (" end.", "", False, False, False),
    ]
    # Flags are present only when true.
    assert all(run.get(flag, True) is True for run in blocks[0]["runs"] for flag in ("bold", "italic", "underline"))


def test_adjacent_runs_with_the_same_formatting_merge() -> None:
    blocks = structure.html_fragment_to_blocks("<p>One <span>two</span> three <span class=\"x\">four</span>.</p>")
    assert [run["text"] for run in blocks[0]["runs"]] == ["One two three four."]

    kept = structure.html_fragment_to_blocks("<p>A <b>bold</b><b> run</b> B</p>")
    assert [flags(run) for run in kept[0]["runs"]] == [
        ("A ", "", False, False, False),
        ("bold run", "", True, False, False),
        (" B", "", False, False, False),
    ]


def test_tables_become_table_blocks_with_header_rows_and_flattened_cells() -> None:
    raw = """
    <p>Before.</p>
    <table>
      <thead><tr><th>Day</th><th>Task</th></tr></thead>
      <tbody>
        <tr><td>Monday</td><td><p>Read the brief.</p><ul><li>Skim <b>first</b></li><li>Then read</li></ul></td></tr>
        <tr><td>Friday</td><td>Submit the <a href="https://example.org/memo">memo</a>.<br>Late work loses a point.</td></tr>
      </tbody>
    </table>
    <p>After.</p>
    <table><tr><th>Only header</th></tr><tr><th>Also th, but after a body? no: still leading</th></tr><tr><td>Body</td></tr></table>
    <table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>
    <table><tr><td> </td></tr></table>
    """

    blocks = structure.html_fragment_to_blocks(raw)

    assert [block["kind"] for block in blocks] == ["p", "table", "p", "table", "table"]
    table = blocks[1]
    assert table["level"] == 0 and table["runs"] == [] and table["header_rows"] == 1
    cell_text = lambda cell: "".join(run["text"] for run in cell)  # noqa: E731
    assert [[cell_text(cell) for cell in row] for row in table["rows"]] == [
        ["Day", "Task"],
        ["Monday", "Read the brief.\n• Skim first\n• Then read"],
        ["Friday", "Submit the memo.\nLate work loses a point."],
    ]
    monday_task = table["rows"][1][1]
    assert [flags(run) for run in monday_task if run.get("bold")] == [("first", "", True, False, False)]
    assert [run["href"] for run in table["rows"][2][1] if run["href"]] == ["https://example.org/memo"]
    # Leading th rows count as header rows; a ragged row is padded to the width.
    assert blocks[3]["header_rows"] == 2 and len(blocks[3]["rows"]) == 3
    assert [[cell_text(cell) for cell in row] for row in blocks[4]["rows"]] == [["A", "B"], ["C", ""]]
    assert blocks[4]["header_rows"] == 0
    # A table with only blank cells is dropped; the flattened text includes every cell.
    assert " ".join(structure.blocks_to_text(blocks).split()) == (
        "Before. Day Task Monday Read the brief. • Skim first • Then read Friday Submit the memo . "
        "Late work loses a point. After. Only header Also th, but after a body? no: still leading Body A B C"
    )


def test_table_inside_a_callout_and_callout_inside_a_cell_keep_their_places() -> None:
    raw = """
    <div class="callout"><p>Note:</p><table><tr><td>In callout</td></tr></table></div>
    <table><tr><td><div class="callout"><p>In cell</p><ul><li>bullet</li></ul></div><p>after</p></td></tr></table>
    """

    blocks = structure.html_fragment_to_blocks(raw)

    assert [block["kind"] for block in blocks] == ["visual", "table"]
    assert [child["kind"] for child in blocks[0]["blocks"]] == ["p", "table"]
    assert "".join(run["text"] for run in blocks[1]["rows"][0][0]) == "Callout\nIn cell\n• bullet\nafter"


def test_markdown_renders_emphasis_and_tables() -> None:
    runs = [
        {"text": "Read the ", "href": ""},
        {"text": "bold guide", "href": "", "bold": True},
        {"text": " and the ", "href": ""},
        {"text": "note", "href": "https://example.org/note", "italic": True},
        {"text": " ", "href": ""},
        {"text": "under", "href": "", "underline": True},
        {"text": ".", "href": ""},
    ]
    assert blueprint.md_inline(runs) == "Read the **bold guide** and the *[note](https://example.org/note)* <u>under</u> ."

    table = {
        "kind": "table", "level": 0, "runs": [], "header_rows": 1,
        "rows": [
            [[{"text": "Day", "href": ""}], [{"text": "Task", "href": ""}]],
            [[{"text": "Mon", "href": ""}], [{"text": "Read", "href": "", "bold": True}, {"text": "\n", "href": ""}, {"text": "Skim", "href": ""}]],
        ],
    }
    assert blueprint.md_blocks([paragraph_block("Before."), table], "") == (
        "Before.<br><br><table><tr><th>Day</th><th>Task</th></tr><tr><td>Mon</td><td>**Read**<br>Skim</td></tr></table>"
    )
    assert blueprint.md_blocks([{"kind": "table", "level": 0, "runs": [], "rows": []}], "fallback") == "fallback"


def test_docx_runs_carry_emphasis_and_tables_are_real_tables(tmp_path: Path) -> None:
    from docx.oxml.ns import qn

    blocks = [
        {"kind": "p", "level": 0, "runs": [
            {"text": "Read the ", "href": ""},
            {"text": "bold guide", "href": "", "bold": True},
            {"text": " and ", "href": ""},
            {"text": "note", "href": "https://example.org/note", "italic": True, "bold": True},
            {"text": " ", "href": ""},
            {"text": "under", "href": "", "underline": True},
        ]},
        {"kind": "table", "level": 0, "runs": [], "header_rows": 1, "rows": [
            [[{"text": "Day", "href": ""}], [{"text": "Task", "href": ""}]],
            [[{"text": "Mon", "href": ""}], [{"text": "Read", "href": ""}, {"text": "\n", "href": ""}, {"text": "Skim", "href": "", "italic": True}]],
        ]},
        paragraph_block("After the table."),
    ]

    doc = render_blocks_to_docx(tmp_path, blocks)

    first = doc.paragraphs[0]
    assert [(run.text, run.bold, run.italic, run.underline) for run in first.runs] == [
        ("Read the ", None, None, None),
        ("bold guide", True, None, None),
        (" and ", None, None, None),
        (" ", None, None, None),
        ("under", None, None, True),
    ]
    link = first._p.findall(qn("w:hyperlink"))[0]
    rpr = link.find(f"{qn('w:r')}/{qn('w:rPr')}")
    assert rpr.find(qn("w:b")) is not None and rpr.find(qn("w:i")) is not None

    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert [[cell.text for cell in row.cells] for row in table.rows] == [["Day", "Task"], ["Mon", "Read\nSkim"]]
    assert all(run.bold for cell in table.rows[0].cells for run in cell.paragraphs[0].runs)
    skim = [run for run in table.rows[1].cells[1].paragraphs[0].runs if run.text == "Skim"][0]
    assert skim.italic is True and skim.bold is None
    assert doc.paragraphs[-1].text == "After the table."


def test_before_week_area_groups_pages_by_module() -> None:
    sections = [
        {"label": "Help", "blocks": [paragraph_block("h")], "module": "Getting Started"},
        {"label": "Policies", "blocks": [paragraph_block("p")], "module": "Getting Started"},
        {"label": "Contacts", "blocks": [paragraph_block("c")], "module": "Faculty Corner"},
        {"label": "Loose", "blocks": [paragraph_block("l")]},
    ]
    assert blueprint.group_before_week_by_module(sections) == [
        ("Getting Started", sections[:2]),
        ("Faculty Corner", [sections[2]]),
        ("", [sections[3]]),
    ]
    rendered = blueprint.md_before_week_group("Getting Started", sections[:2])
    assert rendered == "**Module: Getting Started**<br><br>**Page: Help**<br>h<br><br>**Page: Policies**<br>p"
    assert blueprint.md_before_week_group("", [sections[3]]) == "**Loose:**<br>l"


def test_bundle_before_week_rows_are_one_module_each(synthetic_bundle: dict) -> None:
    markdown = synthetic_bundle["markdown"]
    area = markdown[markdown.index("### " + blueprint.BEFORE_WEEK_LABEL):markdown.index("Course Content:")]
    rows = [line for line in area.splitlines() if line.startswith("| **Module:")]
    assert [row.split("**")[1] for row in rows] == ["Module: Getting Started", "Module: Faculty Corner"]
    assert "**Page: Sample Help**" in rows[0] and "**Page: Sample Policies**" in rows[0]
    assert "**Page: Sample Contacts**" in rows[1]

    docx = pytest.importorskip("docx")
    doc = docx.Document(str(synthetic_bundle["bundle"] / "fidelity__blueprint.docx"))
    before = doc.tables[3]  # three front-matter tables come first
    assert [row.cells[0].text for row in before.rows][::2] == ["Getting Started", "Faculty Corner"]
    value_lines = [p.text for p in before.rows[1].cells[0].paragraphs if p.text]
    assert value_lines[:2] == ["Module: Getting Started", "Page: Sample Help"]
    assert "Page: Sample Policies" in value_lines and "Policy intro paragraph." in value_lines
    assert [p.text for p in before.rows[3].cells[0].paragraphs if p.text][:2] == ["Module: Faculty Corner", "Page: Sample Contacts"]


def test_bundle_carries_emphasis_and_the_table_into_the_week(synthetic_bundle: dict) -> None:
    model = synthetic_bundle["model"]
    overview = model["weeks"][0]["overview"]
    bold = [run["text"] for block in overview for run in block["runs"] if run.get("bold")]
    italic = [run["text"] for block in overview for run in block["runs"] if run.get("italic")]
    assert bold == ["bold guide", "weighted span"]
    assert italic == ["italic ", "note"]
    tables = [block for block in overview if block["kind"] == "table"]
    assert len(tables) == 1 and tables[0]["header_rows"] == 1
    assert [["".join(r["text"] for r in cell) for cell in row] for row in tables[0]["rows"]] == [
        ["Day", "Task"],
        ["Monday", "Read the brief.\n• Skim first\n• Then read"],
        ["Friday", "Submit the memo."],
    ]
    markdown = synthetic_bundle["markdown"]
    # Runs are joined by spaces in the Markdown cell, as they were before the flags.
    assert "Read the **bold guide** and the *italic* *[note](https://example.org/note)* , then the **weighted span** ." in markdown
    assert "<table><tr><th>Day</th><th>Task</th></tr><tr><td>Monday</td><td>Read the brief.<br>• Skim **first**<br>• Then read</td></tr>" in markdown

    docx = pytest.importorskip("docx")
    doc = docx.Document(str(synthetic_bundle["bundle"] / "fidelity__blueprint.docx"))
    nested = [table for outer in doc.tables for row in outer.rows for cell in row.cells for table in cell.tables]
    assert len(nested) == 1
    assert [[cell.text for cell in row.cells] for row in nested[0].rows][0] == ["Day", "Task"]


# --------------------------------------------------------------------------- #
# Panels: extents survive as nested blocks and are written as bracket lines
# --------------------------------------------------------------------------- #
PACKAGER_BRACKET_RE = __import__("re").compile(r"^\[\s*(/?)\s*([A-Za-z]+)\s*(?::\s*(.*?))?\s*\]$")


def test_details_element_is_one_dropdown_with_its_summary_as_title() -> None:
    raw = """
    <p>Before.</p>
    <details><summary>Roadmap <b>dropdown</b></summary>
      <p>First line inside.</p>
      <ul><li>Bullet inside</li></ul>
      <details><summary>Nested</summary><p>Deep.</p></details>
    </details>
    <details><p>No summary here.</p></details>
    <p>After.</p>
    """

    blocks = structure.html_fragment_to_blocks(raw)

    assert [block["kind"] for block in blocks] == ["p", "dropdown", "dropdown", "p"]
    outer = blocks[1]
    assert block_text(outer) == "Roadmap dropdown"
    assert [run.get("bold", False) for run in outer["runs"]] == [False, True]
    assert shape(outer["blocks"]) == [
        ("p", 0, "", "First line inside."),
        ("li", 1, "", "Bullet inside"),
        ("dropdown", 0, "", "Nested"),
    ]
    assert shape(outer["blocks"][2]["blocks"]) == [("p", 0, "", "Deep.")]
    untitled = blocks[2]
    assert untitled["runs"] == [] and shape(untitled["blocks"]) == [("p", 0, "", "No summary here.")]
    assert " ".join(structure.blocks_to_text([outer]).split()) == "Roadmap dropdown First line inside. Bullet inside Nested Deep."


def test_panel_keywords_follow_the_packager_bracket_forms() -> None:
    writer = docx_writer()
    cases = [
        ("dropdown", "Roadmap dropdown", "dropdown", "[dropdown: Roadmap dropdown]", "[/dropdown]"),
        ("dropdown", "", "dropdown", "[dropdown]", "[/dropdown]"),
        ("visual", "Callout", "callout", "[callout]", "[/callout]"),
        ("visual", "Project callout", "callout", "[callout]", "[/callout]"),
        ("visual", "Discussion note", "callout", "[callout]", "[/callout]"),
        ("visual", "Timeline", "timeline", "[timeline]", "[/timeline]"),
        ("visual", "Styled section (background: #f5fafa)", "box", "[box]", "[/box]"),
        ("visual", "Accordion", "box", "[box]", "[/box]"),
    ]
    for kind, label, keyword, opening, closing in cases:
        runs = [{"text": label, "href": ""}] if label else []
        assert writer.panel_lines(kind, runs) == (keyword, opening, closing), (kind, label)
        assert blueprint.md_panel_lines(kind, runs) == (opening, closing), (kind, label)
        for line in (opening, closing):
            match = PACKAGER_BRACKET_RE.match(line)
            assert match and match.group(2) == keyword, line


def test_docx_panels_are_written_between_bracket_lines(tmp_path: Path) -> None:
    blocks = [
        {"kind": "dropdown", "level": 0, "runs": [{"text": "Roadmap dropdown", "href": ""}], "blocks": [
            paragraph_block("Inside the dropdown."),
            list_block("A bullet inside"),
            {"kind": "visual", "level": 0, "runs": [{"text": "Callout", "href": ""}], "blocks": [
                paragraph_block("Nested callout text."),
            ]},
        ]},
        {"kind": "visual", "level": 0, "runs": [{"text": "Timeline", "href": ""}], "blocks": [paragraph_block("Step one.")]},
        {"kind": "visual", "level": 0, "runs": [{"text": "Styled section (background: none)", "href": ""}], "blocks": [paragraph_block("Boxed.")]},
        # Without children a panel is the bracket pair on two adjacent lines.
        {"kind": "dropdown", "level": 0, "runs": [{"text": "Lone summary", "href": ""}]},
        {"kind": "visual", "level": 0, "runs": [{"text": "Callout", "href": ""}]},
        {"kind": "visual", "level": 0, "runs": [{"text": "Timeline", "href": ""}]},
        {"kind": "dropdown", "level": 0, "runs": []},
        paragraph_block("After."),
    ]

    doc = render_blocks_to_docx(tmp_path, blocks)

    assert [p.text for p in doc.paragraphs] == [
        "[dropdown: Roadmap dropdown]",
        "Inside the dropdown.",
        "A bullet inside",
        "[callout]",
        "Nested callout text.",
        "[/callout]",
        "[/dropdown]",
        "[timeline]",
        "Step one.",
        "[/timeline]",
        "[box]",
        "Boxed.",
        "[/box]",
        "[dropdown: Lone summary]",
        "[/dropdown]",
        "[callout]",
        "[/callout]",
        "[timeline]",
        "[/timeline]",
        "[dropdown]",
        "[/dropdown]",
        "After.",
    ]
    # Every opener has its end: the brackets pair up in order.
    stack = []
    for text in [p.text for p in doc.paragraphs if p.text.startswith("[")]:
        keyword = text.strip("[]").split(":")[0].lstrip("/")
        if text.startswith("[/"):
            assert stack.pop() == keyword
        else:
            stack.append(keyword)
    assert stack == []
    bracket_lines = [p for p in doc.paragraphs if p.text.startswith("[")]
    assert all(run.bold for p in bracket_lines for run in p.runs)
    assert doc.paragraphs[2].style.name == "List Bullet"


def test_markdown_panels_are_written_between_bracket_lines() -> None:
    blocks = [
        {"kind": "dropdown", "level": 0, "runs": [{"text": "Roadmap | dropdown", "href": ""}], "blocks": [
            paragraph_block("Inside."), list_block("Bullet"),
        ]},
        {"kind": "visual", "level": 0, "runs": [{"text": "Project callout", "href": ""}], "blocks": [paragraph_block("Noted.")]},
        {"kind": "dropdown", "level": 0, "runs": [], "blocks": [paragraph_block("Untitled body.")]},
        {"kind": "dropdown", "level": 0, "runs": [{"text": "Lone summary", "href": ""}]},
    ]
    rendered = blueprint.md_blocks(blocks, "")
    assert rendered == (
        "**[dropdown: Roadmap \\| dropdown]**<br><br>Inside.<br><br>• Bullet<br><br>**[/dropdown]**"
        "<br>"
        '<div style="background-color:#f2f6fa; border-left:4px solid #9bb3c9; padding:0.6rem 0.75rem;">'
        "**[callout]**<br><br>Noted.<br><br>**[/callout]**</div>"
        "<br>"
        "**[dropdown]**<br><br>Untitled body.<br><br>**[/dropdown]**"
        "<br>"
        "**[dropdown: Lone summary]**<br>**[/dropdown]**"
    )
    empty_visual = blueprint.md_blocks([{"kind": "visual", "level": 0, "runs": [{"text": "Timeline", "href": ""}]}], "")
    assert empty_visual.endswith("**[timeline]**<br>**[/timeline]**</div>")


def test_bundle_dropdowns_keep_their_extent_through_markdown_and_docx(tmp_path_factory) -> None:
    root = tmp_path_factory.mktemp("panels")
    export = root / "export"
    (export / "pages").mkdir(parents=True)
    page = """<html><head><title>Week 1 Overview</title></head><body>
    <p>Intro.</p>
    <details><summary>Three C's</summary><p>Company, customers, competition.</p>
    <ul><li>One</li><li>Two</li></ul></details>
    <div class="callout"><p>Remember the roadmap.</p></div>
    <p>Closing.</p>
    </body></html>"""
    manifest = MANIFEST.replace('<item identifier="A1" identifierref="RES_DROPBOX"><title>Sample Assignment</title></item>', "")
    (export / "imsmanifest.xml").write_text(manifest, encoding="utf-8")
    (export / "pages" / "overview.html").write_text(page, encoding="utf-8")
    for name, text in (("help.html", HELP_PAGE), ("policy.html", POLICY_PAGE), ("contact.html", CONTACT_PAGE)):
        (export / "pages" / name).write_text(text, encoding="utf-8")
    output_dir = root / "review"
    result = subprocess.run(
        [sys.executable, "scripts/build_blueprint_bundle.py", str(export), "--label", "panels", "--course-title",
         "Panels", "--output-dir", str(output_dir), "--no-syllabus-fetch", "--skip-qa", "--quiet"],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bundle = output_dir / "panels__blueprint_bundle"
    model = json.loads((bundle / "panels__blueprint.json").read_text(encoding="utf-8"))
    overview = model["weeks"][0]["overview"]
    kinds = [block["kind"] for block in overview]
    assert kinds[kinds.index("p"):] == ["p", "dropdown", "visual", "p"]
    dropdown = overview[kinds.index("dropdown")]
    assert block_text(dropdown) == "Three C's"
    assert shape(dropdown["blocks"]) == [
        ("p", 0, "", "Company, customers, competition."),
        ("li", 1, "", "One"),
        ("li", 1, "", "Two"),
    ]
    markdown = (bundle / "panels__blueprint.md").read_text(encoding="utf-8")
    assert "**[dropdown: Three C's]**<br><br>Company, customers, competition.<br><br>• One<br>• Two<br><br>**[/dropdown]**" in markdown
    assert "**[callout]**<br><br>Remember the roadmap.<br><br>**[/callout]**" in markdown
    assert "Dropdown / expandable section" not in markdown and "Visual cue" not in markdown

    docx = pytest.importorskip("docx")
    doc = docx.Document(str(bundle / "panels__blueprint.docx"))
    texts = [p.text for table in doc.tables for row in table.rows for cell in row.cells for p in cell.paragraphs if p.text]
    start = texts.index("[dropdown: Three C's]")
    assert texts[start:start + 9] == [
        "[dropdown: Three C's]", "Company, customers, competition.", "One", "Two", "[/dropdown]",
        "[callout]", "Remember the roadmap.", "[/callout]", "Closing.",
    ]
    # The structural QA agrees with the renderer about live links (none here) and tables.
    qa = subprocess.run(
        [sys.executable, "scripts/docx_structure_qa.py", str(bundle / "panels__blueprint.docx"), "--model",
         str(bundle / "panels__blueprint.json"), "--output-dir", str(root / "qa")],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )
    assert qa.returncode == 0, qa.stdout + qa.stderr


def test_panel_tone_and_page_chrome(tmp_path):
    """A callout's tone class becomes a bracket attribute; a template's skip link and course label are not content."""
    from reconstruct_course_structure import html_fragment_to_blocks
    from blueprint_to_docx import panel_lines
    from build_blueprint_bundle import md_panel_lines

    html = (
        '<a class="skip" href="#main">Skip to content</a><p class="kicker">MBA 620 · Marketing</p>'
        '<p>Lead.</p><div class="callout callout--gold"><p>Keep this in mind.</p></div>'
        '<div class="callout" style="border-left-color: #336699;"><p>Custom.</p></div>'
        '<div class="callout"><p>Plain.</p></div>'
    )
    diagnostics: list[str] = []
    blocks = html_fragment_to_blocks(html, package_root=tmp_path, html_dir=tmp_path, diagnostics=diagnostics)
    texts = [" ".join(r["text"] for r in b.get("runs", [])) for b in blocks]
    assert "Skip to content" not in " ".join(texts) and "MBA 620 · Marketing" not in " ".join(texts)
    assert any(d.startswith("Page chrome: 2 element(s)") for d in diagnostics)
    panels = [b for b in blocks if b["kind"] == "visual"]
    assert [p["meta"].get("tone") for p in panels] == ["gold", None, None]
    assert panels[1]["meta"].get("color") == "#336699"
    assert panel_lines("visual", panels[0]["runs"], panels[0]["meta"])[1] == "[callout tone=gold]"
    assert panel_lines("visual", panels[1]["runs"], panels[1]["meta"])[1] == "[callout color=#336699]"
    assert panel_lines("visual", panels[2]["runs"], panels[2]["meta"])[1] == "[callout]"
    assert md_panel_lines("visual", panels[0]["runs"], panels[0]["meta"])[0] == "[callout tone=gold]"

