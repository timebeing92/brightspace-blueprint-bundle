#!/usr/bin/env python3
"""Render a structured blueprint model (JSON) to DOCX.

Input is the ``<label>__blueprint.json`` produced by build_blueprint_bundle.py.
The model contract is documented in ``schemas/blueprint_schema.json``. The output
uses a stable course-blueprint review structure:

- course header line + course title
- single-column front-matter tables (Description / Materials / Course Learning Outcomes)
- "Course Introduction" heading + intro text + "Course Content:"
- the area before Week 1 as one table row per module: a "Module:" line, then
  each page under a "Page:" line
- one full-width table per week, using either section labels above content
  rows (default) or section labels in a left column:
    Overview
    Learning Objectives
    Assigned Reading and Multimedia
    Assignment(s) and Instructions
    Discussion Board Prompts
    Checklist                        (only when present)
    Other course sections            (only when present)

In the default layout, scaffold labels sit in shaded rows immediately above
their extracted content. In the optional left-column layout, the same labels sit
in shaded left cells and the content remains in full-page-width tables.
Bullets use the document's native List Bullet styles (real hanging indents,
nesting by level) when the style base defines them. Items of an ordered list
use the List Number styles the same way, and every separate list gets its own
numbering instance so it counts from 1. A page's own headings (label blocks
with a source heading level) use real heading styles, one to one with the
source: h2 is Heading 2, h3 is Heading 3, and so on to Heading 6. Runs keep
their bold, italic, and underline; a source table is a real Word table with
its header rows bold. A dropdown, callout, timeline, or other styled container
is written between bracket lines, [dropdown: Title] … [/dropdown], [callout] …
[/callout], [timeline] … [/timeline], [box: Label] … [/box], so its extent is
explicit and a packager can read it back; an empty panel is the pair on adjacent
lines. A heading a panel owns is written once, inside the panel.

Word heading levels are one to one with the course page. The frame (Course
Introduction, Before Week 1, each week, Unplaced Activities, the Rubric
Appendix, Extraction Notes) is Heading 1 and the course title is a plain bold
line. A page's heading line is the page's h1, so a page's own headings start at
Heading 2; a source h1 inside page content is written as Heading 2 and the
model's diagnostics say so.

When a template DOCX is supplied with ``--template``, it is opened as a generic
style base so the output can inherit local fonts, heading styles, and page setup.
Its body content is cleared and regenerated from the model.

Requires python-docx (``pip install python-docx``). If it is missing this exits
with code 3 and a clear message; the Markdown blueprint is unaffected.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common_xml import clean_label, clean_text, should_divide_labeled_sections, xml_safe_text

try:
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
except ImportError:  # pragma: no cover - exercised only without the dependency
    sys.stderr.write(
        "python-docx is not installed; cannot render DOCX.\n"
        "Install it with:  pip install python-docx\n"
        "(The Markdown blueprint was still produced.)\n"
    )
    raise SystemExit(3)

from reconstruct_course_structure import panel_owns_label
from rubrics_to_docx import render_rubrics_section

LIVE_SCHEMES = ("http://", "https://", "mailto:")

NOT_FOUND_FIELD = "Needs review: not found in export extraction."
NOT_FOUND_LIST = "None found in export extraction."
LABEL_FALLBACK = "(blank in source - fill in during review)"

# Stable row labels for the blueprint review document.
OVERVIEW_LABEL = "Overview: (add an introduction to the week's topic and activities here, with references as needed)"
OBJECTIVES_LABEL = (
    "Learning Objectives: Must follow the guidelines in this Learning Objectives Guide.\n"
    "Students will be able to:"
)
ASSIGNMENTS_LABEL = "Assignment(s) and Instructions:"
DISCUSSIONS_LABEL = "Discussion Board Prompts:"
READING_LABEL = "Assigned Reading and Multimedia: (add links, articles, textbook readings, videos). Include style-correct citations."
CHECKLIST_LABEL = "Checklist (mirrored from the export)"
OTHER_LABEL = "Other course sections (mirrored from the export)"
BEFORE_WEEK_LABEL = "Before Week 1: Additional Resources and Information"

DESCRIPTION_LABEL = (
    "COURSE DESCRIPTION (keep in mind the Course Description must match the published catalog, "
    "any changes must be approved by the Program and planned in advance)"
)
MATERIALS_LABEL = "TEXTBOOK/S OR REQUIRED MATERIALS"
OUTCOMES_LABEL = "COURSE LEARNING OUTCOMES"


# --------------------------------------------------------------------------- #
# Style helpers
# --------------------------------------------------------------------------- #
def style_or_none(doc: "Document", name: str):
    """Return a style by name if the document defines it, else None."""
    try:
        return doc.styles[name]
    except KeyError:
        return None


def apply_heading(doc: "Document", paragraph, level_name: str) -> None:
    style = style_or_none(doc, level_name)
    if style is not None:
        paragraph.style = style


def _apply_para_style(paragraph, style_name: str) -> bool:
    """Apply a named paragraph style if the document defines it; report success."""
    try:
        paragraph.style = style_name
        return True
    except KeyError:
        return False


def _space(paragraph, *, before: int | None = None, after: int | None = None) -> None:
    """Set paragraph spacing in points (template Normal packs paragraphs tight)."""
    fmt = paragraph.paragraph_format
    if before is not None:
        fmt.space_before = Pt(before)
    if after is not None:
        fmt.space_after = Pt(after)


def _twips(inches: float) -> int:
    return int(round(inches * 1440))


def usable_width_inches(doc: "Document") -> float:
    """Return the writable page width for the document's first section."""
    section = doc.sections[0]
    return section.page_width.inches - section.left_margin.inches - section.right_margin.inches


def _replace_child(parent, tag: str, child) -> None:
    for existing in parent.findall(qn(tag)):
        parent.remove(existing)
    parent.append(child)


def set_column_widths(table, widths_inches: tuple[float, ...]) -> None:
    """Fix table, grid, and cell widths so Word expands it to the writable page width."""
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = OxmlElement("w:tblW")
    tbl_w.set(qn("w:w"), str(_twips(sum(widths_inches))))
    tbl_w.set(qn("w:type"), "dxa")
    _replace_child(tbl_pr, "w:tblW", tbl_w)
    tbl_ind = OxmlElement("w:tblInd")
    tbl_ind.set(qn("w:w"), "0")
    tbl_ind.set(qn("w:type"), "dxa")
    _replace_child(tbl_pr, "w:tblInd", tbl_ind)

    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths_inches:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(_twips(width)))
        grid.append(col)

    for row in table.rows:
        for cell, width in zip(row.cells, widths_inches):
            cell.width = Inches(width)
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = OxmlElement("w:tcW")
            tc_w.set(qn("w:w"), str(_twips(width)))
            tc_w.set(qn("w:type"), "dxa")
            _replace_child(tc_pr, "w:tcW", tc_w)


def set_cell_margins(table, *, top: int = 60, bottom: int = 60,
                     start: int = 110, end: int = 110) -> None:
    """Give every cell in the table a little padding (units: dxa, 20ths of a point)."""
    tbl_pr = table._tbl.tblPr
    margins = OxmlElement("w:tblCellMar")
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    tbl_pr.append(margins)


def _trim_leading_empty(cell) -> None:
    """Drop the cell's default empty first paragraph once content follows it."""
    paragraphs = cell.paragraphs
    if (
        len(paragraphs) > 1
        and not paragraphs[0].runs
        and not paragraphs[0]._p.findall(qn("w:hyperlink"))
    ):
        paragraphs[0]._p.getparent().remove(paragraphs[0]._p)


def set_cell_background(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def set_paragraph_background(paragraph, hex_color: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    p_pr.append(shd)


def add_table_borders(table) -> None:
    """Add single black borders to every edge/inside line of a table."""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "808080")
        borders.append(el)
    tbl_pr.append(borders)


# --------------------------------------------------------------------------- #
# Cell content helpers
# --------------------------------------------------------------------------- #
def first_paragraph(cell):
    """Return the cell's initial empty paragraph (reuse it, don't append a blank)."""
    return cell.paragraphs[0]


def write_label(cell, text: str) -> None:
    """Write a template scaffold label into its own shaded cell, in smaller
    bold type so the extracted content in the neighboring cell stands out."""
    para = first_paragraph(cell)
    for chunk in text.split("\n"):
        if para.runs:
            para = cell.add_paragraph()
        _space(para, before=0, after=2)
        run = para.add_run(chunk)
        run.bold = True
        run.font.size = Pt(9)
    set_cell_background(cell, "F2F2F2")


def add_hyperlink(paragraph, url: str, text: str, *, bold: bool = False, italic: bool = False) -> None:
    """Append a real, clickable hyperlink run to a paragraph (cell or body)."""
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    if bold:
        rpr.append(OxmlElement("w:b"))
    if italic:
        rpr.append(OxmlElement("w:i"))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rpr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(underline)
    run.append(rpr)
    # A newline is a line break kept from the source page; python-docx already
    # turns it into w:br for plain runs, so link text gets the same treatment.
    for index, line in enumerate(xml_safe_text(text).split("\n")):
        if index:
            run.append(OxmlElement("w:br"))
        if line or not index:
            text_el = OxmlElement("w:t")
            text_el.set(qn("xml:space"), "preserve")
            text_el.text = line
            run.append(text_el)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _apply_emphasis(word_run, run: dict) -> None:
    """Carry a run's emphasis flags (bold, italic, underline) onto a Word run."""
    if run.get("bold"):
        word_run.bold = True
    if run.get("italic"):
        word_run.italic = True
    if run.get("underline"):
        word_run.underline = True


def _emit_runs(paragraph, runs: list[dict]) -> None:
    for run in runs:
        text = xml_safe_text(run.get("text", ""))
        if not text:
            continue
        href = xml_safe_text(run.get("href") or "").strip()
        if href.startswith(LIVE_SCHEMES):
            add_hyperlink(paragraph, href, text, bold=bool(run.get("bold")), italic=bool(run.get("italic")))
        elif href:
            _apply_emphasis(paragraph.add_run(text), run)
            note = paragraph.add_run(f" ({href})")
            note.font.size = Pt(8)
            note.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        else:
            _apply_emphasis(paragraph.add_run(text), run)


def _emit_table(container, block: dict) -> None:
    """Write a table block as a real Word table; header rows are bold."""
    rows = block.get("rows") or []
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = container.add_table(rows=len(rows), cols=width)
    add_table_borders(table)
    header_rows = int(block.get("header_rows") or 0)
    for row_index, row in enumerate(rows):
        for col_index in range(width):
            cell = table.cell(row_index, col_index)
            runs = row[col_index] if col_index < len(row) else []
            para = first_paragraph(cell)
            _space(para, before=0, after=0)
            if row_index < header_rows:
                runs = [{**run, "bold": True} for run in runs]
            _emit_runs(para, runs)
    # A cell gets its trailing paragraph from python-docx; the body needs one so
    # the next table does not merge into this one.
    if hasattr(container, "sections"):
        container.add_paragraph()


def _write_missing(container, missing: str) -> None:
    para = container.add_paragraph()
    run = para.add_run(missing)
    run.italic = True
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)


BULLET_STYLE_BY_LEVEL = {1: "List Bullet", 2: "List Bullet 2", 3: "List Bullet 3"}

_BULLET_NUM_ID_CACHE: dict[int, str | None] = {}


def _find_bullet_num_id(doc_part) -> str | None:
    """Find a numbering definition whose first level is a bullet; None when the
    document has no numbering part."""
    try:
        numbering = doc_part.part_related_by(RELATIONSHIP_TYPE.NUMBERING).element
    except (KeyError, AttributeError):
        return None
    bullet_abstract_ids = set()
    for abstract in numbering.findall(qn("w:abstractNum")):
        lvl0 = next(
            (lvl for lvl in abstract.findall(qn("w:lvl")) if lvl.get(qn("w:ilvl")) == "0"),
            None,
        )
        fmt = lvl0.find(qn("w:numFmt")) if lvl0 is not None else None
        if fmt is not None and fmt.get(qn("w:val")) == "bullet":
            bullet_abstract_ids.add(abstract.get(qn("w:abstractNumId")))
    for num in numbering.findall(qn("w:num")):
        ref = num.find(qn("w:abstractNumId"))
        if ref is not None and ref.get(qn("w:val")) in bullet_abstract_ids:
            return num.get(qn("w:numId"))
    return None


def _bullet_num_id(paragraph) -> str | None:
    """Memoized bullet numId lookup for the paragraph's document."""
    key = id(paragraph.part)
    if key not in _BULLET_NUM_ID_CACHE:
        _BULLET_NUM_ID_CACHE[key] = _find_bullet_num_id(paragraph.part)
    return _BULLET_NUM_ID_CACHE[key]


def _apply_native_bullet(paragraph, num_id: str, level: int) -> None:
    """Attach real Word list numbering (numPr) to a fresh paragraph."""
    ppr = paragraph._p.get_or_add_pPr()
    numpr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), str(min(level, 9) - 1))
    numid = OxmlElement("w:numId")
    numid.set(qn("w:val"), num_id)
    numpr.append(ilvl)
    numpr.append(numid)
    ppr.insert(0, numpr)


NUMBER_STYLE_BY_LEVEL = {1: "List Number", 2: "List Number 2", 3: "List Number 3"}


class _OpenLists:
    """Ordered lists still open in a run of consecutive list items.

    Consecutive ordered items at one level belong to one list. A non-list block
    closes every open list, a shallower item closes the deeper ones, and a
    bullet closes a numbered list at its own level (a bullet nested under a
    numbered item leaves that list open, so its numbering carries on).
    """

    def __init__(self) -> None:
        self._open: dict[int, dict] = {}

    def close_all(self) -> None:
        self._open.clear()

    def enter(self, level: int, *, ordered: bool) -> dict | None:
        """Note one list item; return its list's state when the item is ordered."""
        for open_level in list(self._open):
            if open_level > level or (open_level == level and not ordered):
                del self._open[open_level]
        if not ordered:
            return None
        state = self._open.setdefault(level, {"count": 0, "numbering": None})
        state["count"] += 1
        return state


def _numbering_root(part):
    """The document's numbering definitions, or None without a numbering part."""
    try:
        return part.part_related_by(RELATIONSHIP_TYPE.NUMBERING).element
    except (KeyError, AttributeError):
        return None


def _style_numbering(paragraph) -> tuple[str, int] | None:
    """(numId, ilvl) the paragraph's style, or a style it is based on, links to."""
    style = paragraph.style
    while style is not None:
        ppr = style.element.find(qn("w:pPr"))
        num_pr = ppr.find(qn("w:numPr")) if ppr is not None else None
        num_id = num_pr.find(qn("w:numId")) if num_pr is not None else None
        if num_id is not None and num_id.get(qn("w:val")) not in (None, "", "0"):
            ilvl = num_pr.find(qn("w:ilvl"))
            return num_id.get(qn("w:val")), int(ilvl.get(qn("w:val")) or 0) if ilvl is not None else 0
        style = style.base_style
    return None


def _find_decimal_numbering(part) -> tuple[str, int] | None:
    """(numId, number of levels) of a numbering definition whose first level
    counts in decimal; None when the document has none."""
    numbering = _numbering_root(part)
    if numbering is None:
        return None
    level_counts: dict[str, int] = {}
    for abstract in numbering.findall(qn("w:abstractNum")):
        levels = abstract.findall(qn("w:lvl"))
        lvl0 = next((lvl for lvl in levels if lvl.get(qn("w:ilvl")) == "0"), None)
        fmt = lvl0.find(qn("w:numFmt")) if lvl0 is not None else None
        if fmt is not None and fmt.get(qn("w:val")) == "decimal":
            level_counts[abstract.get(qn("w:abstractNumId"))] = len(levels)
    for num in numbering.findall(qn("w:num")):
        ref = num.find(qn("w:abstractNumId"))
        if ref is not None and ref.get(qn("w:val")) in level_counts:
            return num.get(qn("w:numId")), level_counts[ref.get(qn("w:val"))]
    return None


def _add_restarted_num(part, base_num_id: str, ilvl: int) -> str | None:
    """Add a numbering instance that reuses ``base_num_id``'s definition but
    counts from 1 again at ``ilvl``; return its numId."""
    numbering = _numbering_root(part)
    if numbering is None:
        return None
    nums = numbering.findall(qn("w:num"))
    base = next((num for num in nums if num.get(qn("w:numId")) == base_num_id), None)
    ref = base.find(qn("w:abstractNumId")) if base is not None else None
    if ref is None:
        return None
    used = [int(num.get(qn("w:numId"))) for num in nums if (num.get(qn("w:numId")) or "").isdigit()]
    num_id = str(max(used) + 1)
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), num_id)
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), ref.get(qn("w:val")))
    num.append(abstract_ref)
    override = OxmlElement("w:lvlOverride")
    override.set(qn("w:ilvl"), str(ilvl))
    start = OxmlElement("w:startOverride")
    start.set(qn("w:val"), "1")
    override.append(start)
    num.append(override)
    # Instances stay together, after the abstract definitions they point to.
    nums[-1].addnext(num)
    return num_id


def _start_numbering(paragraph, level: int) -> dict | None:
    """Set up native numbering for a new ordered list starting at this paragraph.

    Prefers the numbering the List Number style links to, else the template's
    own decimal numbering. None when the document offers neither.
    """
    part = paragraph.part
    style_name = NUMBER_STYLE_BY_LEVEL.get(min(level, 3), "List Number")
    style = style_name if _apply_para_style(paragraph, style_name) else ""
    if style:
        linked = _style_numbering(paragraph)
        num_id = _add_restarted_num(part, linked[0], linked[1]) if linked else None
        if num_id:
            return {"style": style, "num_id": num_id, "ilvl": linked[1], "indent": False}
    base = _find_decimal_numbering(part)
    if base:
        ilvl = min(level, base[1], 9) - 1
        num_id = _add_restarted_num(part, base[0], ilvl)
        if num_id:
            # A definition with too few levels cannot show the nesting itself.
            return {"style": style, "num_id": num_id, "ilvl": ilvl, "indent": ilvl != level - 1}
    paragraph.style = None
    return None


def _apply_ordered_number(paragraph, level: int, state: dict) -> None:
    """Number one ordered list item as a native Word list paragraph.

    The first item of a list sets up a numbering instance of its own with a
    start override, so Word counts every separate list from 1. Only when the
    document has no usable numbering definition is the number typed with a
    hanging indent, the same last resort the bullets have.
    """
    if state["count"] == 1:
        state["numbering"] = _start_numbering(paragraph, level)
    numbering = state["numbering"]
    fmt = paragraph.paragraph_format
    if numbering is None:
        fmt.left_indent = Pt(18 * level)
        fmt.first_line_indent = Pt(-18)
        paragraph.add_run(f"{state['count']}. ")
        return
    if numbering["style"]:
        _apply_para_style(paragraph, numbering["style"])
    num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    num_pr.get_or_add_ilvl().val = numbering["ilvl"]
    num_pr.get_or_add_numId().val = int(numbering["num_id"])
    if numbering["indent"]:
        fmt.left_indent = Pt(18 * level)
        fmt.first_line_indent = Pt(-18)


# Word heading levels are one to one with the page. Heading 1 is the frame
# (week titles and the other top-level sections), so a page's own headings use
# Heading 2 to Heading 6 by their source level. A source h1 inside page content
# becomes Heading 2: the page heading line is already the page's h1.
FRAME_HEADING_STYLE = "Heading 1"
PAGE_HEADING_STYLES = ("Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6")


def page_heading_style(level: int) -> str:
    """The Word heading style for a page heading at source level ``level``."""
    return PAGE_HEADING_STYLES[min(max(int(level), 2), 6) - 2]


def _apply_page_heading_style(paragraph, level: int) -> bool:
    """Give a heading label the Word heading style for its source heading level.

    Tries the one-to-one style first, then each shallower page-heading style,
    so a style base that defines only some of them still yields a real heading.
    Never Heading 1, which is the frame's. False when it defines none.
    """
    deepest = min(max(int(level), 2), 6) - 2
    for name in reversed(PAGE_HEADING_STYLES[: deepest + 1]):
        if _apply_para_style(paragraph, name):
            return True
    return False


def _emit_divider(container, *, before: int = 6, after: int = 6) -> None:
    para = container.add_paragraph()
    _space(para, before=before, after=after)
    p_pr = para._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "D9D9D9")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


# Panels are written in the bracket form the downstream packager reads:
# [dropdown: Title] … [/dropdown], [callout] … [/callout], [timeline] …
# [/timeline], and [box: Label] … [/box] for any other styled container. The bracket
# lines carry the extent that a one-line cue lost; shading stays as a reading aid.
# A panel without content is the same pair on two adjacent lines, so the file
# says "an empty panel was here" in the grammar every other panel uses.
PANEL_FILLS = {"dropdown": "F4F2FA", "callout": "F2F6FA", "timeline": "F2F6FA", "box": "F2F6FA"}


def panel_keyword(kind: str, label: str) -> str:
    """The bracket keyword for a visual or dropdown block with children."""
    if kind == "dropdown":
        return "dropdown"
    lowered = clean_text(label).lower()
    if lowered == "callout" or lowered.endswith("callout") or lowered in {"note", "discussion note"}:
        return "callout"
    if lowered == "timeline":
        return "timeline"
    return "box"


def panel_attrs(meta: dict | None) -> str:
    """Bracket attributes a panel carries: tone=<name> from a tone class, else color=#hex from its border."""
    meta = meta or {}
    if meta.get("tone"):
        return f" tone={meta['tone']}"
    if meta.get("color"):
        return f" color={meta['color']}"
    return ""


def panel_lines(kind: str, runs: list[dict], meta: dict | None = None) -> tuple[str, str, str]:
    """(keyword, opening line, closing line) for a panel block.

    A dropdown carries its title, a box carries the source component's label
    (``[box: Accordion]``, ``[box: Framed section]``); a callout or timeline
    carries only its attributes.
    """
    label = clean_text(" ".join(str(run.get("text", "")) for run in runs))
    keyword = panel_keyword(kind, label)
    attrs = panel_attrs(meta) if keyword != "dropdown" else ""
    if keyword == "dropdown":
        opening = f"[dropdown: {label}]" if label else "[dropdown]"
    elif keyword == "box" and label:
        opening = f"[box{attrs}: {label}]"
    else:
        opening = f"[{keyword}{attrs}]"
    return keyword, opening, f"[/{keyword}]"


def _emit_bracket_line(container, text: str, fill: str, *, before: int, after: int) -> None:
    para = container.add_paragraph()
    _space(para, before=before, after=after)
    set_paragraph_background(para, fill)
    run = para.add_run(text)
    run.bold = True
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x4D, 0x4D, 0x4D)


def _emit_panel(container, block: dict, kind: str, runs: list[dict], *, wrapper_fill: str = "") -> None:
    keyword, opening, closing = panel_lines(kind, runs, block.get("meta"))
    fill = wrapper_fill or PANEL_FILLS[keyword]
    _emit_bracket_line(container, opening, fill, before=6, after=2)
    previous_child_kind = kind
    child_lists = _OpenLists()
    for child in block.get("blocks") or []:
        _emit_block(container, child, previous_kind=previous_child_kind, wrapper_fill=fill, open_lists=child_lists)
        previous_child_kind = child.get("kind", "")
    _emit_bracket_line(container, closing, fill, before=2, after=6)


def _emit_block(
    container,
    block: dict,
    *,
    previous_kind: str = "",
    wrapper_fill: str = "",
    open_lists: _OpenLists | None = None,
) -> None:
    """Add one block as a paragraph; only list items get a bullet, paragraphs
    stay prose. Bullets become native Word lists: List Bullet styles when the
    style base defines them, else a numPr reference to the template's own
    bullet numbering, else a manual hanging-indent bullet. Ordered items become
    native numbered lists the same way; ``open_lists`` carries the lists open
    across the caller's consecutive blocks so each list restarts at 1."""
    kind = block.get("kind")
    if open_lists is None:
        open_lists = _OpenLists()
    if kind != "li":
        open_lists.close_all()
    if kind == "divider":
        _emit_divider(container)
        return
    if kind == "table":
        _emit_table(container, block)
        return

    runs = [r for r in block.get("runs", []) if r.get("text")]
    if kind in {"visual", "dropdown"}:
        _emit_panel(container, block, kind, runs, wrapper_fill=wrapper_fill)
        return
    if not runs:
        return
    para = container.add_paragraph()
    if kind == "label":
        before = 2 if previous_kind in {"section_label", "label"} else 8
        _space(para, before=before, after=2)
        if wrapper_fill:
            set_paragraph_background(para, wrapper_fill)
        # A label with a source heading level is one of the page's own headings:
        # it gets a real heading style and no trailing colon. Level-0 labels
        # (activity metadata, checklist categories) stay bold lines.
        heading = clean_label(" ".join(str(run.get("text", "")) for run in runs))
        if int(block.get("level") or 0) > 0 and heading and _apply_page_heading_style(
            para, int(block["level"])
        ):
            para.add_run(heading)
            return
        for run in runs:
            text = clean_text(run.get("text", ""))
            if text:
                para.add_run(text).bold = True
        return
    if kind == "practice":
        _space(para, before=6, after=5)
        set_paragraph_background(para, wrapper_fill or "EEF7F2")
        title = clean_text(runs[0].get("text", ""))
        href = clean_text(runs[0].get("href", ""))
        title_run = para.add_run(title)
        title_run.bold = True
        title_run.font.size = Pt(9)
        if href:
            note = para.add_run(f" ({href})")
            note.font.size = Pt(8)
            note.font.color.rgb = RGBColor(0x70, 0x70, 0x70)
        summary = clean_text(block.get("meta", {}).get("summary", ""))
        if summary:
            para.add_run().add_break()
            summary_run = para.add_run(summary)
            summary_run.font.size = Pt(8)
            summary_run.font.color.rgb = RGBColor(0x4D, 0x4D, 0x4D)
        return
    if kind in {"embed", "image", "file", "hidden"}:
        label = {
            "embed": f"{clean_text(block.get('meta', {}).get('embed_type', '')) or 'Embedded media'}: ",
            "image": "Embedded image: ",
            "file": "Attached course file: ",
            "hidden": "Hidden manifest item: ",
        }[kind]
        _space(para, before=6, after=4)
        fill = {
            "embed": "F7F9FB",
            "image": "F7F9FB",
            "file": "F7F9FB",
            "hidden": "FFF4E5",
        }[kind]
        set_paragraph_background(para, wrapper_fill or fill)
        prefix = para.add_run(label)
        prefix.bold = True
        prefix.font.size = Pt(9)
        prefix.font.color.rgb = RGBColor(0x4D, 0x4D, 0x4D)
        _emit_runs(para, runs)
        return
    if kind == "li":
        level = max(1, int(block.get("level") or 1))
        ordered = (block.get("meta") or {}).get("list") == "ordered"
        numbered = open_lists.enter(level, ordered=ordered)
        if numbered is not None:
            _apply_ordered_number(para, level, numbered)
        elif not _apply_para_style(para, BULLET_STYLE_BY_LEVEL.get(min(level, 3), "List Bullet")):
            num_id = _bullet_num_id(para)
            if num_id is not None:
                _apply_native_bullet(para, num_id, level)
            else:
                fmt = para.paragraph_format
                fmt.left_indent = Pt(18 * level)
                fmt.first_line_indent = Pt(-9)
                para.add_run("• ")
        _space(para, before=0, after=2)
    else:
        _space(para, before=0, after=6)
    if wrapper_fill:
        set_paragraph_background(para, wrapper_fill)
    _emit_runs(para, runs)


def write_blocks(container, blocks: list[dict], *, missing: str) -> None:
    """Render blocks (paragraphs / bullets with link runs) into a cell or the body."""
    if not blocks:
        _write_missing(container, missing)
        return
    previous_kind = ""
    open_lists = _OpenLists()
    for block in blocks:
        _emit_block(container, block, previous_kind=previous_kind, open_lists=open_lists)
        previous_kind = block.get("kind", "")


def _add_section_divider(container) -> None:
    _emit_divider(container)


def write_value_labeled(container, sections: list[dict], *, missing: str, divider: bool | str = False) -> None:
    """Write labeled sections: a bold label line, then its blocks (kind-aware).
    Sections after the first get breathing room so entries don't run together."""
    if not sections:
        _write_missing(container, missing)
        return
    previous_section: dict | None = None
    for index, section in enumerate(sections):
        has_divider = should_divide_labeled_sections(previous_section, section, divider)
        if has_divider:
            _add_section_divider(container)
        label = clean_label(section.get("label", ""))
        if label and panel_owns_label(section.get("blocks", []), label):
            label = ""  # the heading is inside the section's panel and is written there, once
        if label:
            para = container.add_paragraph()
            _space(para, before=2 if has_divider else (8 if index else 0), after=2)
            para.add_run(f"{label}:").bold = True
        previous_kind = "section_label" if label else ""
        open_lists = _OpenLists()
        for block in section.get("blocks", []):
            _emit_block(container, block, previous_kind=previous_kind, open_lists=open_lists)
            previous_kind = block.get("kind", "")
        previous_section = section


# --------------------------------------------------------------------------- #
# Body construction
# --------------------------------------------------------------------------- #
def clear_body(doc: "Document") -> None:
    """Remove all paragraphs and tables, preserving the final sectPr."""
    body = doc.element.body
    sect_pr = body.find(qn("w:sectPr"))
    for child in list(body):
        if child is sect_pr:
            continue
        body.remove(child)


def add_front_matter_table(doc: "Document", label: str, blocks: list[dict]) -> None:
    table = doc.add_table(rows=2, cols=1)
    add_table_borders(table)
    set_column_widths(table, (usable_width_inches(doc),))
    set_cell_margins(table)
    write_label(table.cell(0, 0), label)
    value = table.cell(1, 0)
    write_blocks(value, blocks, missing=NOT_FOUND_FIELD)
    _trim_leading_empty(value)
    doc.add_paragraph()


def week_section_rows(week: dict):
    # (label, fill) section pairs. Due day-of-week rides along in the
    # assignment text; coded numeric dates are term-relative and not encoded.
    rows = [
        (OVERVIEW_LABEL,
         lambda cell: write_blocks(cell, week.get("overview", []), missing=NOT_FOUND_FIELD)),
        (OBJECTIVES_LABEL,
         lambda cell: write_blocks(cell, week.get("learning_objectives", []), missing=NOT_FOUND_FIELD)),
        (READING_LABEL,
         lambda cell: write_value_labeled(cell, week.get("resources", []), missing=NOT_FOUND_LIST)),
        (ASSIGNMENTS_LABEL,
         lambda cell: write_value_labeled(
             cell, week.get("assignments", []), missing=NOT_FOUND_LIST, divider="object"
         )),
        (DISCUSSIONS_LABEL,
         lambda cell: write_value_labeled(
             cell, week.get("discussions", []), missing=NOT_FOUND_LIST, divider="object"
         )),
    ]
    if week.get("checklist"):
        rows.append((CHECKLIST_LABEL,
                     lambda cell: write_value_labeled(cell, week["checklist"], missing=NOT_FOUND_LIST)))
    if week.get("other_sections"):
        rows.append((OTHER_LABEL,
                     lambda cell: write_value_labeled(
                         cell,
                         week["other_sections"],
                         missing=NOT_FOUND_LIST,
                         divider="page",
                     )))
    return rows


def add_week_table_top(doc: "Document", week: dict) -> None:
    rows = week_section_rows(week)

    table = doc.add_table(rows=len(rows) * 2, cols=1)
    add_table_borders(table)
    set_column_widths(table, (usable_width_inches(doc),))
    set_cell_margins(table)
    for index, (label, fill) in enumerate(rows):
        write_label(table.cell(index * 2, 0), label)
        value = table.cell(index * 2 + 1, 0)
        fill(value)
        _trim_leading_empty(value)

    doc.add_paragraph()


def add_week_table_left(doc: "Document", week: dict) -> None:
    rows = week_section_rows(week)
    full_width = usable_width_inches(doc)
    label_width = min(1.8, max(1.35, full_width * 0.28))
    value_width = full_width - label_width

    table = doc.add_table(rows=len(rows), cols=2)
    add_table_borders(table)
    set_column_widths(table, (label_width, value_width))
    set_cell_margins(table)
    for index, (label, fill) in enumerate(rows):
        write_label(table.cell(index, 0), label)
        value = table.cell(index, 1)
        fill(value)
        _trim_leading_empty(value)

    doc.add_paragraph()


def add_week_table(doc: "Document", week: dict, *, section_layout: str = "top") -> None:
    apply_heading(doc, doc.add_paragraph(week.get("title", "Course Module")), FRAME_HEADING_STYLE)
    if section_layout == "left":
        add_week_table_left(doc, week)
    else:
        add_week_table_top(doc, week)


def group_before_week_by_module(sections: list[dict]) -> list[tuple[str, list[dict]]]:
    """Consecutive before-week sections with the same module, in order.

    Sections without a module (a revision-4 model) each form their own group
    with an empty module name and render as before: one row per page.
    """
    groups: list[tuple[str, list[dict]]] = []
    for section in sections:
        module = clean_label(section.get("module", ""))
        if groups and module and groups[-1][0] == module:
            groups[-1][1].append(section)
        else:
            groups.append((module, [section]))
    return groups


def _write_directive_line(container, keyword: str, value: str, *, first: bool) -> None:
    para = container.add_paragraph()
    _space(para, before=0 if first else 8, after=2)
    para.add_run(f"{keyword}: {value}").bold = True


def add_before_week_table(doc: "Document", sections: list[dict]) -> None:
    """The area before Week 1: one row per module.

    The shaded label row names the module; the value row opens with a
    ``Module:`` line and holds each of the module's pages under a ``Page:``
    line, in manifest order. A section without a module keeps the older
    one-row-per-page shape.
    """
    apply_heading(doc, doc.add_paragraph(BEFORE_WEEK_LABEL), FRAME_HEADING_STYLE)
    groups = group_before_week_by_module(sections)
    table = doc.add_table(rows=len(groups) * 2, cols=1)
    add_table_borders(table)
    set_column_widths(table, (usable_width_inches(doc),))
    set_cell_margins(table)
    for index, (module, group) in enumerate(groups):
        value = table.cell(index * 2 + 1, 0)
        if not module:
            section = group[0]
            write_label(table.cell(index * 2, 0), clean_label(section.get("label", "")) or BEFORE_WEEK_LABEL)
            write_blocks(value, section.get("blocks", []), missing=NOT_FOUND_LIST)
        else:
            write_label(table.cell(index * 2, 0), module)
            _write_directive_line(value, "Module", module, first=True)
            for section in group:
                _write_directive_line(value, "Page", clean_label(section.get("label", "")) or LABEL_FALLBACK, first=False)
                previous_kind = "section_label"
                open_lists = _OpenLists()
                for block in section.get("blocks", []):
                    _emit_block(value, block, previous_kind=previous_kind, open_lists=open_lists)
                    previous_kind = block.get("kind", "")
        _trim_leading_empty(value)
    doc.add_paragraph()


def add_simple_section(doc: "Document", heading: str, items: list[str]) -> None:
    apply_heading(doc, doc.add_paragraph(heading), FRAME_HEADING_STYLE)
    if not items:
        doc.add_paragraph("None.")
        return
    for item in items:
        _emit_block(doc, {"kind": "li", "level": 1, "runs": [{"text": item, "href": ""}]})


def relax_cell_opening_headings(doc: "Document") -> int:
    """Let a heading that opens a table cell break from the row's next paragraph.

    Word's heading styles keep with the next paragraph. Inside a table that is
    read as "keep this row with the next", and a week's value row is far
    longer than a page, so Word and LibreOffice start the row on a fresh page
    and leave the rest of the previous one empty (MBA 620, 2026-10-12: seven
    under-filled pages, 81 pages instead of 76). Only the heading that opens a
    cell is relaxed; a heading further down a cell keeps its attachment to the
    text under it. Returns the number of paragraphs changed.
    """
    changed = 0
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                first = next((p for p in cell.paragraphs if p.text.strip()), None)
                if first is not None and first.style is not None and first.style.name.startswith("Heading"):
                    first.paragraph_format.keep_with_next = False
                    changed += 1
    return changed


def render(
    model: dict,
    doc: "Document",
    *,
    section_layout: str = "top",
    rubrics_model: dict | None = None,
    activities_model: dict | None = None,
) -> None:
    clear_body(doc)

    title_para = doc.add_paragraph()
    title_run = title_para.add_run(model.get("course_title") or "Course Blueprint")
    title_run.bold = True
    title_run.font.size = Pt(16)

    note = doc.add_paragraph()
    note_run = note.add_run(
        "Evidence mode: extracted text is source-derived; missing fields remain marked for review."
    )
    note_run.italic = True
    note_run.font.size = Pt(8)
    note_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    doc.add_paragraph()

    fm = model.get("front_matter", {})
    add_front_matter_table(doc, DESCRIPTION_LABEL, fm.get("course_description", []))
    add_front_matter_table(doc, MATERIALS_LABEL, fm.get("required_materials", []))
    add_front_matter_table(doc, OUTCOMES_LABEL, fm.get("course_learning_outcomes", []))

    apply_heading(doc, doc.add_paragraph("Course Introduction"), FRAME_HEADING_STYLE)
    write_blocks(doc, fm.get("course_introduction", []), missing=NOT_FOUND_FIELD)

    before_week = model.get("before_week_1", [])
    if before_week:
        add_before_week_table(doc, before_week)

    content_para = doc.add_paragraph()
    content_para.add_run("Course Content:").bold = True
    doc.add_paragraph()

    for week in model.get("weeks", []):
        add_week_table(doc, week, section_layout=section_layout)

    unplaced = model.get("unplaced_activities", {})
    if unplaced.get("assignments") or unplaced.get("discussions"):
        apply_heading(doc, doc.add_paragraph("Unplaced Activities"), FRAME_HEADING_STYLE)
        doc.add_paragraph(
            "These activities were extracted from D2L object XML but were not connected to a "
            "module quicklink by resource code."
        )
        if unplaced.get("assignments"):
            apply_heading(doc, doc.add_paragraph("Assignments"), "Heading 2")
            write_value_labeled(doc, unplaced["assignments"], missing=NOT_FOUND_LIST, divider="object")
        if unplaced.get("discussions"):
            apply_heading(doc, doc.add_paragraph("Discussions"), "Heading 2")
            write_value_labeled(doc, unplaced["discussions"], missing=NOT_FOUND_LIST, divider="object")

    render_rubrics_section(
        doc,
        rubrics_model,
        activities_model,
        title="Rubric Appendix",
        page_break=bool((rubrics_model or {}).get("rubrics")),
    )

    add_simple_section(doc, "Extraction Notes", model.get("diagnostics", []) or ["None."])
    relax_cell_opening_headings(doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="Path to <label>__blueprint.json")
    parser.add_argument("--output", type=Path, required=True, help="Output .docx path")
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Optional .docx to use as a style base; body content is regenerated",
    )
    parser.add_argument(
        "--section-layout",
        choices=("top", "left"),
        default="top",
        help="DOCX weekly section label layout: shaded top rows (default) or shaded left column.",
    )
    parser.add_argument(
        "--rubrics-json",
        type=Path,
        default=None,
        help="Optional <label>__rubrics.json to append rubric grids to the blueprint DOCX.",
    )
    parser.add_argument(
        "--activities-json",
        type=Path,
        default=None,
        help="Optional <label>__course_activities.json for rubric Used by lines.",
    )
    args = parser.parse_args(argv)

    if not args.model.exists():
        raise SystemExit(f"error: model not found: {args.model}")
    model = json.loads(args.model.read_text(encoding="utf-8"))

    if args.template and args.template.exists():
        doc = Document(str(args.template))
    else:
        if args.template:
            sys.stderr.write(f"note: template not found ({args.template}); building DOCX from scratch.\n")
        doc = Document()

    rubrics_model = None
    if args.rubrics_json and args.rubrics_json.exists():
        rubrics_model = json.loads(args.rubrics_json.read_text(encoding="utf-8"))
    activities_model = None
    if args.activities_json and args.activities_json.exists():
        activities_model = json.loads(args.activities_json.read_text(encoding="utf-8"))

    render(
        model,
        doc,
        section_layout=args.section_layout,
        rubrics_model=rubrics_model,
        activities_model=activities_model,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(args.output))
    print(f"docx: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
