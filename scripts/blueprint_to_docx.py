#!/usr/bin/env python3
"""Render a structured blueprint model (JSON) to a CGPS-styled DOCX.

Input is the ``<label>__blueprint.json`` produced by build_blueprint_bundle.py
(schema: schemas/blueprint_schema.json). The output mirrors the section
structure of ``reference/Course Blueprint Template 2020 CGPS.docx``:

- course header line + course title
- single-column front-matter tables (Description / Materials / Course Learning Outcomes)
- "Course Introduction" heading + intro text + "Course Content:"
- one 6-row table per week:
    Overview                         (full-width)
    Learning Objectives              (full-width)
    Assignment(s) and Instructions | Due
    Discussion Board Prompts        | Due
    Lecture topics                   (full-width)
    Assigned Reading and Multimedia  (full-width)

When a template DOCX is supplied with ``--template``, it is opened as the style
base so the output inherits the template's fonts (Open Sans), heading styles,
and page setup. Its body content is cleared and regenerated from the model.

Requires python-docx (``pip install python-docx``). If it is missing this exits
with code 3 and a clear message; the Markdown blueprint is unaffected.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
except ImportError:  # pragma: no cover - exercised only without the dependency
    sys.stderr.write(
        "python-docx is not installed; cannot render DOCX.\n"
        "Install it with:  pip install python-docx\n"
        "(The Markdown blueprint was still produced.)\n"
    )
    raise SystemExit(3)

LIVE_SCHEMES = ("http://", "https://", "mailto:")

NOT_FOUND_FIELD = "Needs review: not found in export extraction."
NOT_FOUND_LIST = "None found in export extraction."
LABEL_FALLBACK = "(blank in template — fill in during review)"

# Verbatim row labels from the 2020 CGPS template.
OVERVIEW_LABEL = "Overview: (add an introduction to the week's topic and activities here, with references as needed)"
OBJECTIVES_LABEL = (
    "Learning Objectives: Must follow the guidelines in this Learning Objectives Guide.\n"
    "Students will be able to:"
)
ASSIGNMENTS_LABEL = "Assignment(s) and Instructions:"
DISCUSSIONS_LABEL = "Discussion Board Prompts:"
READING_LABEL = "Assigned Reading and Multimedia: (add links, articles, textbook readings, videos). Include style-correct citations."
OTHER_LABEL = "Other course sections (mirrored from the export)"

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


def set_cell_background(cell, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


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
    """Write a bold label into the cell's first paragraph."""
    para = first_paragraph(cell)
    for chunk in text.split("\n"):
        if para.runs:
            para = cell.add_paragraph()
        run = para.add_run(chunk)
        run.bold = True


def write_value_block(cell, value: str, *, missing: str) -> None:
    """Append a value paragraph beneath the label inside the same cell."""
    para = cell.add_paragraph()
    run = para.add_run(value if value else missing)
    if not value:
        run.italic = True
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)


def write_value_bullets(cell, items: list[str], *, missing: str) -> None:
    if not items:
        para = cell.add_paragraph()
        run = para.add_run(missing)
        run.italic = True
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        return
    for item in items:
        para = cell.add_paragraph()
        para.paragraph_format.left_indent = Pt(12)
        para.add_run(f"• {item}")


def add_hyperlink(paragraph, url: str, text: str) -> None:
    """Append a real, clickable hyperlink run to a paragraph (cell or body)."""
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rpr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(underline)
    run.append(rpr)
    text_el = OxmlElement("w:t")
    text_el.set(qn("xml:space"), "preserve")
    text_el.text = text
    run.append(text_el)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _emit_runs(paragraph, runs: list[dict]) -> None:
    for run in runs:
        text = run.get("text", "")
        if not text:
            continue
        href = (run.get("href") or "").strip()
        if href.startswith(LIVE_SCHEMES):
            add_hyperlink(paragraph, href, text)
        elif href:
            paragraph.add_run(text)
            note = paragraph.add_run(f" ({href})")
            note.font.size = Pt(8)
            note.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        else:
            paragraph.add_run(text)


def _write_missing(container, missing: str) -> None:
    para = container.add_paragraph()
    run = para.add_run(missing)
    run.italic = True
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)


def _emit_block(container, block: dict) -> None:
    """Add one block as a paragraph; only list items get a bullet, paragraphs stay prose."""
    runs = [r for r in block.get("runs", []) if r.get("text")]
    if not runs:
        return
    para = container.add_paragraph()
    if block.get("kind") == "li":
        para.paragraph_format.left_indent = Pt(12 * max(1, block.get("level", 1)))
        para.add_run("• ")
    else:
        para.paragraph_format.left_indent = Pt(6)
    _emit_runs(para, runs)


def write_blocks(container, blocks: list[dict], *, missing: str) -> None:
    """Render blocks (paragraphs / bullets with link runs) into a cell or the body."""
    if not blocks:
        _write_missing(container, missing)
        return
    for block in blocks:
        _emit_block(container, block)


def write_value_labeled(container, sections: list[dict], *, missing: str) -> None:
    """Write labeled sections: a bold label line, then its blocks (kind-aware)."""
    if not sections:
        _write_missing(container, missing)
        return
    for section in sections:
        label = (section.get("label") or "").strip()
        if label:
            para = container.add_paragraph()
            para.add_run(f"{label}:").bold = True
        for block in section.get("blocks", []):
            _emit_block(container, block)


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
    header = table.cell(0, 0)
    write_label(header, label)
    set_cell_background(header, "F2F2F2")
    write_blocks(table.cell(1, 0), blocks, missing=NOT_FOUND_FIELD)
    doc.add_paragraph()


def add_week_table(doc: "Document", week: dict) -> None:
    apply_heading(doc, doc.add_paragraph(week.get("title", "Course Module")), "Heading 2")

    has_other = bool(week.get("other_sections"))
    row_count = 6 if has_other else 5
    table = doc.add_table(rows=row_count, cols=2)
    add_table_borders(table)

    # Row 0 - Overview (full width)
    overview = table.cell(0, 0).merge(table.cell(0, 1))
    write_label(overview, OVERVIEW_LABEL)
    write_blocks(overview, week.get("overview", []), missing=NOT_FOUND_FIELD)

    # Row 1 - Learning Objectives (full width)
    objectives = table.cell(1, 0).merge(table.cell(1, 1))
    write_label(objectives, OBJECTIVES_LABEL)
    write_blocks(objectives, week.get("learning_objectives", []), missing=NOT_FOUND_FIELD)

    # Row 2 - Assignments (full width). Due day-of-week rides along in the
    # assignment text; coded numeric dates are term-relative and not encoded.
    assignments = table.cell(2, 0).merge(table.cell(2, 1))
    write_label(assignments, ASSIGNMENTS_LABEL)
    write_value_labeled(assignments, week.get("assignments", []), missing=NOT_FOUND_LIST)

    # Row 3 - Discussions (full width)
    discussions = table.cell(3, 0).merge(table.cell(3, 1))
    write_label(discussions, DISCUSSIONS_LABEL)
    write_value_labeled(discussions, week.get("discussions", []), missing=NOT_FOUND_LIST)

    # Row 4 - Assigned Reading and Multimedia (full width, labeled resources)
    reading = table.cell(4, 0).merge(table.cell(4, 1))
    write_label(reading, READING_LABEL)
    write_value_labeled(reading, week.get("resources", []), missing=NOT_FOUND_LIST)

    # Row 5 - Other course sections (full width, only when present)
    if has_other:
        other = table.cell(5, 0).merge(table.cell(5, 1))
        write_label(other, OTHER_LABEL)
        write_value_labeled(other, week.get("other_sections", []), missing=NOT_FOUND_LIST)

    doc.add_paragraph()


def add_simple_section(doc: "Document", heading: str, items: list[str]) -> None:
    apply_heading(doc, doc.add_paragraph(heading), "Heading 2")
    if not items:
        doc.add_paragraph("None.")
        return
    for item in items:
        para = doc.add_paragraph()
        para.paragraph_format.left_indent = Pt(12)
        para.add_run(f"• {item}")


def render(model: dict, doc: "Document") -> None:
    clear_body(doc)

    header_course = model.get("course_number") or "Course #"
    header_term = model.get("term") or "Term"

    title_para = doc.add_paragraph()
    title_run = title_para.add_run(f"{header_course} - Course Blueprint - {header_term}")
    title_run.bold = True
    title_run.font.size = Pt(16)

    subtitle = doc.add_paragraph()
    subtitle_run = subtitle.add_run(model.get("course_title") or "COURSE TITLE")
    subtitle_run.bold = True
    subtitle_run.font.size = Pt(13)

    note = doc.add_paragraph()
    note_run = note.add_run(
        f"Template format reference: {model.get('template_reference', '')}  |  "
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

    apply_heading(doc, doc.add_paragraph("Course Introduction"), "Heading 2")
    write_blocks(doc, fm.get("course_introduction", []), missing=NOT_FOUND_FIELD)
    content_para = doc.add_paragraph()
    content_para.add_run("Course Content:").bold = True
    doc.add_paragraph()

    for week in model.get("weeks", []):
        add_week_table(doc, week)

    unplaced = model.get("unplaced_activities", {})
    if unplaced.get("assignments") or unplaced.get("discussions"):
        apply_heading(doc, doc.add_paragraph("Unplaced Activities"), "Heading 2")
        doc.add_paragraph(
            "These activities were extracted from D2L object XML but were not connected to a "
            "module quicklink by resource code."
        )
        if unplaced.get("assignments"):
            apply_heading(doc, doc.add_paragraph("Assignments"), "Heading 4")
            write_value_labeled(doc, unplaced["assignments"], missing=NOT_FOUND_LIST)
        if unplaced.get("discussions"):
            apply_heading(doc, doc.add_paragraph("Discussions"), "Heading 4")
            write_value_labeled(doc, unplaced["discussions"], missing=NOT_FOUND_LIST)

    add_simple_section(doc, "Extraction Notes", model.get("diagnostics", []) or ["None."])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="Path to <label>__blueprint.json")
    parser.add_argument("--output", type=Path, required=True, help="Output .docx path")
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="CGPS template .docx to use as the style base (optional)",
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

    render(model, doc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(args.output))
    print(f"docx: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
