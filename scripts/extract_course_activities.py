#!/usr/bin/env python3
"""Extract dropbox folders, discussions, quizzes, checklists, and grade items into review artifacts.

Reads an unpacked Brightspace export (or ZIP) and emits a reviewer workbook,
canonical JSON, and a concise markdown note covering:

- dropbox_d2l.xml assignment folders
- discussion_d2l_*.xml forums and topics
- quiz_d2l_*.xml quiz instructions and settings
- checklist_d2l.xml checklist objects and items
- grades_d2l.xml grade items and categories
- the cross-file joins between them (activity -> grade item, activity ->
  rubric, activity -> condition set, manifest quicklink -> resource_code)

Extraction mode only: authored wording and embedded HTML are preserved
verbatim (paired readable-text and raw-HTML columns); joins that do not
resolve are reported as diagnostics, never guessed. Field semantics follow
docs/project/BUMG_650_FULL_EXPORT_XML_ANATOMY_AND_MANIPULATION_NOTES.md,
with shape variants confirmed against the CHEM 1020 (2021) and DSW 821
(2026) exports (e.g. folder date_due appears as a child element in newer
exports and may be an attribute in older ones; grade_item/out_of are absent
on ungraded folders).

Usage:
    python3 scripts/extract_course_activities.py /path/to/unpacked/export
    python3 scripts/extract_course_activities.py export.zip --output-dir workspace/review
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

from openpyxl import Workbook

from common_xml import clean, local_name
from reconstruct_course_structure import html_fragment_to_blocks

URL_RCODE = re.compile(r"r[Cc]ode=([A-Za-z0-9._-]+)")
IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ALT_ATTR = re.compile(r"""\balt\s*=\s*("[^"]*[^"\s][^"]*"|'[^']*[^'\s][^']*')""", re.IGNORECASE)


def html_to_text(raw: str) -> str:
    stripped = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(stripped)).strip()


def images_missing_alt(raw_html: str) -> int:
    count = 0
    for img in IMG_TAG.findall(raw_html):
        if not ALT_ATTR.search(img):
            count += 1
    return count


def children_by_name(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if local_name(child.tag) == name]


def first_child(elem: ET.Element, name: str) -> ET.Element | None:
    found = children_by_name(elem, name)
    return found[0] if found else None


def child_text(elem: ET.Element, name: str) -> str:
    child = first_child(elem, name)
    return clean(child.text) if child is not None else ""


def deep_text(elem: ET.Element | None) -> str:
    """All text content under an element (handles HTML stored in nested <text>)."""
    if elem is None:
        return ""
    return clean("".join(elem.itertext()))


def attr_by_local(elem: ET.Element, name: str) -> str:
    for key, value in elem.attrib.items():
        if local_name(key) == name:
            return clean(value)
    return ""


def parse_xml(path: Path, diagnostics: list[str]) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        diagnostics.append(f"{path.name} is not well-formed XML: {exc}")
        return None


def load_export_root(path: Path, holder: list) -> Path:
    if path.is_dir():
        return path
    if path.is_file() and zipfile.is_zipfile(path):
        tmp = tempfile.TemporaryDirectory(prefix="extract_activities_")
        holder.append(tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp.name)
        return Path(tmp.name)
    raise SystemExit(f"error: not an export directory or zip: {path}")


# --- reference layers (joins resolve against these) ---------------------------


def load_grade_items(root: Path, diagnostics: list[str]) -> tuple[list[dict], dict[str, dict]]:
    grades_path = root / "grades_d2l.xml"
    items: list[dict] = []
    if not grades_path.exists():
        diagnostics.append("grades_d2l.xml not present in export")
        return items, {}
    grades_root = parse_xml(grades_path, diagnostics)
    if grades_root is None:
        return items, {}

    categories: dict[str, str] = {}
    for category in grades_root.iter():
        if local_name(category.tag) == "category":
            categories[category.attrib.get("id", "")] = child_text(category, "name")

    for item in grades_root.iter():
        if local_name(item.tag) != "item":
            continue
        scoring = first_child(item, "scoring")
        record = {
            "id": item.attrib.get("id", ""),
            "identifier": item.attrib.get("identifier", ""),
            "resource_code": item.attrib.get("resource_code", ""),
            "name": child_text(item, "name"),
            "short_name": child_text(item, "short_name"),
            "type_id": child_text(item, "type_id"),
            "is_active": child_text(item, "is_active"),
            "category_id": item.attrib.get("category_id", ""),
            "category_name": categories.get(item.attrib.get("category_id", ""), ""),
            "out_of": child_text(scoring, "out_of") if scoring is not None else "",
            "max_grade": child_text(scoring, "max_grade") if scoring is not None else "",
            "is_bonus": child_text(scoring, "is_bonus") if scoring is not None else "",
            "exclude_from_final_grade_calc": (
                child_text(scoring, "exclude_from_final_grade_calc") if scoring is not None else ""
            ),
            "linked_activities": [],
        }
        items.append(record)
    by_code = {item["resource_code"]: item for item in items if item["resource_code"]}
    return items, by_code


def load_rubric_names(root: Path, diagnostics: list[str]) -> dict[str, str]:
    rubrics_path = root / "rubrics_d2l.xml"
    if not rubrics_path.exists():
        return {}
    rubrics_root = parse_xml(rubrics_path, diagnostics)
    if rubrics_root is None:
        return {}
    names: dict[str, str] = {}
    for rubric in rubrics_root.iter():
        if local_name(rubric.tag) != "rubric":
            continue
        rubric_id = rubric.attrib.get("id", "")
        name = rubric.attrib.get("name", "") or child_text(rubric, "name")
        if rubric_id:
            names[rubric_id] = clean(name)
    return names


def load_condition_sets(root: Path, diagnostics: list[str]) -> set[str]:
    cr_path = root / "conditionalrelease_d2l.xml"
    if not cr_path.exists():
        return set()
    cr_root = parse_xml(cr_path, diagnostics)
    if cr_root is None:
        return set()
    codes: set[str] = set()
    for elem in cr_root.iter():
        code = elem.attrib.get("resource_code", "")
        if code:
            codes.add(code)
    return codes


def load_quicklink_codes(root: Path, diagnostics: list[str]) -> dict[str, int]:
    """resource_code -> number of manifest quicklinks referencing it (rcode/rCode)."""
    manifest_path = root / "imsmanifest.xml"
    if not manifest_path.exists():
        diagnostics.append("imsmanifest.xml not present in export")
        return {}
    manifest_root = parse_xml(manifest_path, diagnostics)
    if manifest_root is None:
        return {}
    counts: dict[str, int] = {}
    for elem in manifest_root.iter():
        if local_name(elem.tag) != "resource":
            continue
        href = elem.attrib.get("href", "")
        for code in URL_RCODE.findall(href):
            counts[code] = counts.get(code, 0) + 1
    return counts


# --- activity extraction -------------------------------------------------------


def rubric_ids_of(elem: ET.Element) -> list[str]:
    associations = first_child(elem, "associations")
    if associations is None:
        return []
    return [clean(rubric.text) for rubric in associations.iter() if local_name(rubric.tag) == "rubric" and clean(rubric.text)]


def extract_dropbox(root: Path, diagnostics: list[str]) -> list[dict]:
    dropbox_path = root / "dropbox_d2l.xml"
    if not dropbox_path.exists():
        diagnostics.append("dropbox_d2l.xml not present in export")
        return []
    dropbox_root = parse_xml(dropbox_path, diagnostics)
    if dropbox_root is None:
        return []
    folders = []
    for folder in dropbox_root.iter():
        if local_name(folder.tag) != "folder":
            continue
        instructions = first_child(folder, "instructions")
        raw_html = deep_text(instructions)
        # date_due: child element in newer exports, attribute in some older ones
        date_due = child_text(folder, "date_due") or folder.attrib.get("date_due", "")
        folders.append(
            {
                "id": folder.attrib.get("id", ""),
                "name": folder.attrib.get("name", ""),
                "resource_code": folder.attrib.get("resource_code", ""),
                "submission_type": folder.attrib.get("submission_type", ""),
                "folder_type": folder.attrib.get("folder_type", ""),
                "out_of": folder.attrib.get("out_of", ""),
                "grade_item_code": folder.attrib.get("grade_item", ""),
                "rubric_ids": rubric_ids_of(folder),
                "date_due": date_due,
                "is_hidden": folder.attrib.get("is_hidden", ""),
                "condition_set": folder.attrib.get("condition_set", ""),
                "instructions_text": html_to_text(raw_html),
                "instructions_blocks": html_fragment_to_blocks(raw_html),
                "instructions_html": raw_html,
                "images_missing_alt": images_missing_alt(raw_html),
            }
        )
    return folders


def extract_discussions(root: Path, diagnostics: list[str]) -> list[dict]:
    rows: list[dict] = []
    for disc_path in sorted(root.glob("discussion_d2l_*.xml")):
        disc_root = parse_xml(disc_path, diagnostics)
        if disc_root is None:
            continue
        for forum in disc_root.iter():
            if local_name(forum.tag) != "forum":
                continue
            forum_content = first_child(forum, "content")
            forum_title = child_text(forum_content, "title") if forum_content is not None else ""
            forum_props = first_child(forum, "properties")
            rows.append(
                {
                    "kind": "forum",
                    "source_file": disc_path.name,
                    "forum_id": forum.attrib.get("id", ""),
                    "topic_id": "",
                    "title": forum_title,
                    "resource_code": forum.attrib.get("resource_code", ""),
                    "score_out_of": "",
                    "grade_item_code": "",
                    "rubric_ids": [],
                    "is_hidden": child_text(forum_props, "is_hidden") if forum_props is not None else "",
                    "requires_approval": "",
                    "must_post_to_participate": "",
                    "condition_set": forum.attrib.get("condition_set", ""),
                    "description_text": "",
                    "description_html": "",
                    "images_missing_alt": 0,
                }
            )
            topics_container = first_child(forum, "topics")
            topic_parent = topics_container if topics_container is not None else forum
            for topic in topic_parent.iter():
                if local_name(topic.tag) != "topic":
                    continue
                props = first_child(topic, "properties")
                content = first_child(topic, "content")
                description = first_child(content, "description") if content is not None else None
                raw_html = deep_text(description)
                rows.append(
                    {
                        "kind": "topic",
                        "source_file": disc_path.name,
                        "forum_id": forum.attrib.get("id", ""),
                        "topic_id": topic.attrib.get("id", ""),
                        "title": child_text(content, "title") if content is not None else "",
                        "resource_code": topic.attrib.get("resource_code", ""),
                        "score_out_of": child_text(props, "score_out_of") if props is not None else "",
                        "grade_item_code": child_text(props, "grade_item_id") if props is not None else "",
                        "rubric_ids": rubric_ids_of(topic),
                        "is_hidden": child_text(props, "is_hidden") if props is not None else "",
                        "requires_approval": child_text(props, "requires_approval") if props is not None else "",
                        "must_post_to_participate": (
                            child_text(props, "must_post_to_participate") if props is not None else ""
                        ),
                        "condition_set": topic.attrib.get("condition_set", ""),
                        "description_text": html_to_text(raw_html),
                        "description_blocks": html_fragment_to_blocks(raw_html),
                        "description_html": raw_html,
                        "images_missing_alt": images_missing_alt(raw_html),
                    }
                )
    if not rows:
        diagnostics.append("no discussion_d2l_*.xml present in export")
    return rows


def sort_order(elem: ET.Element) -> int:
    try:
        return int(elem.attrib.get("sort_order", "0"))
    except ValueError:
        return 0


def _text_block(text: str, *, kind: str = "p", level: int = 0) -> dict | None:
    text = clean(text)
    if not text:
        return None
    return {"kind": kind, "level": level, "runs": [{"text": text, "href": ""}]}


def _checklist_description_blocks(elem: ET.Element | None) -> list[dict]:
    raw_html = deep_text(elem)
    if not raw_html:
        return []
    blocks = html_fragment_to_blocks(raw_html)
    if blocks:
        return blocks
    block = _text_block(html_to_text(raw_html))
    return [block] if block else []


def extract_checklists(root: Path, diagnostics: list[str], quicklinks: dict[str, int] | None = None) -> list[dict]:
    checklist_path = root / "checklist_d2l.xml"
    if not checklist_path.exists():
        return []
    checklist_root = parse_xml(checklist_path, diagnostics)
    if checklist_root is None:
        return []

    linked_codes = set(quicklinks or {})
    filter_to_linked = quicklinks is not None and bool(linked_codes)
    rows: list[dict] = []
    for checklist in checklist_root.iter():
        if local_name(checklist.tag) != "checklist":
            continue
        resource_code = checklist.attrib.get("resource_code", "")
        if filter_to_linked and resource_code not in linked_codes:
            continue
        blocks: list[dict] = []
        for category in sorted(children_by_name(checklist, "category"), key=sort_order):
            category_name = child_text(category, "name")
            if category_name:
                block = _text_block(f"{category_name}:", kind="label")
                if block:
                    blocks.append(block)
            blocks.extend(_checklist_description_blocks(first_child(category, "description")))
            for item in sorted(children_by_name(category, "item"), key=sort_order):
                item_name = child_text(item, "name")
                if item_name:
                    block = _text_block(item_name, kind="li", level=1)
                    if block:
                        blocks.append(block)
                blocks.extend(_checklist_description_blocks(first_child(item, "description")))

        rows.append(
            {
                "id": checklist.attrib.get("id", ""),
                "name": child_text(checklist, "name"),
                "resource_code": resource_code,
                "display_in_new_window": checklist.attrib.get("display_in_new_window", ""),
                "blocks": blocks,
                "item_count": sum(1 for item in checklist.iter() if local_name(item.tag) == "item"),
                "quicklink_count": 0,
            }
        )
    return rows


def _first_assessment(root: ET.Element) -> ET.Element | None:
    for elem in root.iter():
        if local_name(elem.tag) == "assessment":
            return elem
    return None


def _qti_metadata(elem: ET.Element) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for field in elem.iter():
        if local_name(field.tag) != "qti_metadatafield":
            continue
        label = ""
        entry = ""
        for child in field:
            if local_name(child.tag) == "fieldlabel":
                label = clean(child.text)
            elif local_name(child.tag) == "fieldentry":
                entry = clean(child.text)
        if label:
            metadata[label] = entry
    return metadata


def _quiz_instruction_html(assessment: ET.Element, proc: ET.Element | None) -> str:
    rubric = first_child(assessment, "rubric")
    if rubric is not None:
        for mattext in rubric.iter():
            if local_name(mattext.tag) == "mattext":
                raw_html = deep_text(mattext)
                # Some exported HTML carries browser-extension wrappers that are
                # not course content and render poorly in reviewer artifacts.
                return re.sub(
                    r"<scribe-shadow\b.*?</scribe-shadow>",
                    "",
                    raw_html,
                    flags=re.IGNORECASE | re.DOTALL,
                )
    return deep_text(first_child(proc, "intro_message")) if proc is not None else ""


def _quiz_grade_item_code(proc: ET.Element | None) -> str:
    if proc is None:
        return ""
    grade_item = first_child(proc, "grade_item")
    if grade_item is None:
        return ""
    return attr_by_local(grade_item, "resource_code") or clean(grade_item.text)


def _quiz_sections(assessment: ET.Element) -> tuple[list[dict], Counter]:
    sections: list[dict] = []
    question_types: Counter = Counter()
    for section in assessment.iter():
        if local_name(section.tag) != "section":
            continue
        metadata = _qti_metadata(section)
        direct_items = [child for child in section if local_name(child.tag) in ("item", "itemref")]
        title = clean(section.attrib.get("title", ""))
        if not title and not direct_items:
            continue
        for item in direct_items:
            if local_name(item.tag) != "item":
                continue
            item_metadata = _qti_metadata(item)
            question_type = clean(item_metadata.get("qmd_questiontype", ""))
            if question_type:
                question_types[question_type] += 1
        sections.append(
            {
                "title": title,
                "draw_count": clean(metadata.get("qmd_numberofitems", "")),
                "candidate_count": len(direct_items),
                "points_per_question": clean(metadata.get("qmd_weighting", "")),
            }
        )
    return sections, question_types


def _draw_count_total(sections: list[dict]) -> int:
    total = 0
    for section in sections:
        raw = section.get("draw_count", "")
        if not raw:
            continue
        try:
            total += int(float(raw))
        except ValueError:
            continue
    return total


def extract_quizzes(root: Path, diagnostics: list[str], quicklinks: dict[str, int] | None = None) -> list[dict]:
    rows: list[dict] = []
    for quiz_path in sorted(root.glob("quiz_d2l_*.xml")):
        quiz_root = parse_xml(quiz_path, diagnostics)
        if quiz_root is None:
            continue
        assessment = _first_assessment(quiz_root)
        if assessment is None:
            diagnostics.append(f"{quiz_path.name}: no assessment element found")
            continue
        proc = first_child(assessment, "assess_procextension")
        instructions_html = _quiz_instruction_html(assessment, proc)
        sections, question_types = _quiz_sections(assessment)
        resource_code = attr_by_local(assessment, "resource_code")
        rows.append(
            {
                "source_file": quiz_path.name,
                "id": assessment.attrib.get("id", ""),
                "ident": assessment.attrib.get("ident", ""),
                "title": clean(assessment.attrib.get("title", "")),
                "resource_code": resource_code,
                "grade_item_code": _quiz_grade_item_code(proc),
                "is_active": child_text(proc, "is_active") if proc is not None else "",
                "attempts_allowed": child_text(proc, "attempts_allowed") if proc is not None else "",
                "time_limit_minutes": child_text(proc, "time_limit") if proc is not None else "",
                "show_clock": child_text(proc, "show_clock") if proc is not None else "",
                "enforce_time_limit": child_text(proc, "enforce_time_limit") if proc is not None else "",
                "mark_calculation_type": child_text(proc, "mark_calculation_type") if proc is not None else "",
                "date_due": child_text(proc, "date_due") if proc is not None else "",
                "section_count": len(sections),
                "candidate_question_count": sum(int(section.get("candidate_count", 0)) for section in sections),
                "draw_count_total": _draw_count_total(sections),
                "question_type_summary": "; ".join(
                    f"{name}: {count}" for name, count in sorted(question_types.items())
                ),
                "sections": sections,
                "instructions_text": html_to_text(instructions_html),
                "instructions_blocks": html_fragment_to_blocks(instructions_html),
                "instructions_html": instructions_html,
                "images_missing_alt": images_missing_alt(instructions_html),
                "quicklink_count": 0,
                "rubric_ids": [],
            }
        )

    if not rows:
        return rows
    linked_codes = set(quicklinks or {})
    if quicklinks is not None and any(row.get("resource_code") in linked_codes for row in rows):
        return [row for row in rows if row.get("resource_code") in linked_codes]
    return rows


# --- join resolution -----------------------------------------------------------


def resolve_joins(
    folders: list[dict],
    discussion_rows: list[dict],
    checklists: list[dict],
    quizzes: list[dict],
    grade_items: list[dict],
    grade_by_code: dict[str, dict],
    rubric_names: dict[str, str],
    condition_codes: set[str],
    quicklinks: dict[str, int],
    diagnostics: list[str],
) -> list[dict]:
    joins: list[dict] = []

    def add_join(kind: str, name: str, code: str, join_type: str, target: str, resolved: str) -> None:
        joins.append(
            {
                "source_kind": kind,
                "source_name": name,
                "source_code": code,
                "join_type": join_type,
                "target": target,
                "resolved": resolved,
            }
        )

    def resolve_activity(kind: str, record: dict, name: str) -> None:
        code = record["resource_code"]
        grade_code = record.get("grade_item_code", "")
        if grade_code:
            grade_item = grade_by_code.get(grade_code)
            if grade_item is not None:
                record["grade_item_name"] = grade_item["name"]
                record["grade_item_out_of"] = grade_item["out_of"]
                grade_item["linked_activities"].append(f"{kind}: {name}")
                add_join(kind, name, code, "grade_item", grade_code, "yes")
            else:
                record["grade_item_name"] = "(unresolved)"
                record["grade_item_out_of"] = ""
                add_join(kind, name, code, "grade_item", grade_code, "NO")
                diagnostics.append(
                    f"{kind} {name!r}: grade_item {grade_code} not found in grades_d2l.xml"
                )
        else:
            record["grade_item_name"] = ""
            record["grade_item_out_of"] = ""

        resolved_rubrics = []
        for rubric_id in record.get("rubric_ids", []):
            rubric_name = rubric_names.get(rubric_id)
            if rubric_name is not None:
                resolved_rubrics.append(f"{rubric_id}: {rubric_name}")
                add_join(kind, name, code, "rubric", rubric_id, "yes")
            else:
                resolved_rubrics.append(f"{rubric_id}: (unresolved)")
                add_join(kind, name, code, "rubric", rubric_id, "NO")
                diagnostics.append(f"{kind} {name!r}: rubric id {rubric_id} not found in rubrics_d2l.xml")
        record["rubrics_resolved"] = "; ".join(resolved_rubrics)

        condition = record.get("condition_set", "")
        if condition:
            resolved = "yes" if condition in condition_codes else ("n/a" if not condition_codes else "NO")
            add_join(kind, name, code, "condition_set", condition, resolved)
            if resolved == "NO":
                diagnostics.append(
                    f"{kind} {name!r}: condition_set {condition} not found in conditionalrelease_d2l.xml"
                )

        link_count = quicklinks.get(code, 0)
        record["quicklink_count"] = link_count
        if code:
            add_join(kind, name, code, "manifest_quicklink", f"{link_count} link(s)", "yes" if link_count else "none")

    for folder in folders:
        resolve_activity("dropbox_folder", folder, folder["name"])
        if folder["out_of"] and folder.get("grade_item_out_of"):
            try:
                if float(folder["out_of"]) != float(folder["grade_item_out_of"]):
                    diagnostics.append(
                        f"dropbox_folder {folder['name']!r}: folder out_of {folder['out_of']} "
                        f"!= grade item out_of {folder['grade_item_out_of']}"
                    )
            except ValueError:
                pass

    for row in discussion_rows:
        if row["kind"] == "topic":
            resolve_activity("discussion_topic", row, row["title"])
        else:
            row["grade_item_name"] = ""
            row["grade_item_out_of"] = ""
            row["rubrics_resolved"] = ""
            row["quicklink_count"] = quicklinks.get(row["resource_code"], 0)

    for checklist in checklists:
        code = checklist.get("resource_code", "")
        link_count = quicklinks.get(code, 0)
        checklist["quicklink_count"] = link_count
        if code:
            add_join(
                "checklist",
                checklist.get("name", ""),
                code,
                "manifest_quicklink",
                f"{link_count} link(s)",
                "yes" if link_count else "none",
            )

    for quiz in quizzes:
        resolve_activity("quiz", quiz, quiz["title"])

    for grade_item in grade_items:
        if not grade_item["linked_activities"] and grade_item["type_id"] not in ("9",):
            add_join(
                "grade_item",
                grade_item["name"],
                grade_item["resource_code"],
                "linked_activity",
                "(none found in dropbox/discussions/quizzes)",
                "none",
            )
    return joins


# --- output ----------------------------------------------------------------------


def write_workbook(
    path: Path,
    folders: list[dict],
    discussion_rows: list[dict],
    checklists: list[dict],
    quizzes: list[dict],
    grade_items: list[dict],
    joins: list[dict],
    diagnostics: list[str],
) -> None:
    wb = Workbook()

    def add_sheet(title: str, headers: list[str], rows: list[list]) -> None:
        ws = wb.create_sheet(title)
        ws.append(headers)
        ws.freeze_panes = "A2"
        for row in rows:
            ws.append(row)

    wb.remove(wb.active)
    add_sheet(
        "Dropbox_Folders",
        [
            "id", "name", "resource_code", "submission_type", "out_of", "grade_item_code",
            "grade_item_name", "grade_item_out_of", "rubrics", "date_due", "is_hidden",
            "condition_set", "quicklink_count", "images_missing_alt", "instructions_text",
            "instructions_html",
        ],
        [
            [
                f["id"], f["name"], f["resource_code"], f["submission_type"], f["out_of"],
                f["grade_item_code"], f.get("grade_item_name", ""), f.get("grade_item_out_of", ""),
                f.get("rubrics_resolved", ""), f["date_due"], f["is_hidden"], f["condition_set"],
                f.get("quicklink_count", 0), f["images_missing_alt"], f["instructions_text"],
                f["instructions_html"],
            ]
            for f in folders
        ],
    )
    add_sheet(
        "Discussions",
        [
            "kind", "source_file", "forum_id", "topic_id", "title", "resource_code",
            "score_out_of", "grade_item_code", "grade_item_name", "rubrics", "is_hidden",
            "requires_approval", "must_post_to_participate", "condition_set",
            "quicklink_count", "images_missing_alt", "description_text", "description_html",
        ],
        [
            [
                r["kind"], r["source_file"], r["forum_id"], r["topic_id"], r["title"],
                r["resource_code"], r["score_out_of"], r["grade_item_code"],
                r.get("grade_item_name", ""), r.get("rubrics_resolved", ""), r["is_hidden"],
                r["requires_approval"], r["must_post_to_participate"], r["condition_set"],
                r.get("quicklink_count", 0), r["images_missing_alt"], r["description_text"],
                r["description_html"],
            ]
            for r in discussion_rows
        ],
    )
    add_sheet(
        "Grade_Items",
        [
            "id", "identifier", "resource_code", "name", "short_name", "type_id", "is_active",
            "category", "out_of", "max_grade", "is_bonus", "exclude_from_final_grade_calc",
            "linked_activities",
        ],
        [
            [
                g["id"], g["identifier"], g["resource_code"], g["name"], g["short_name"],
                g["type_id"], g["is_active"], g["category_name"], g["out_of"], g["max_grade"],
                g["is_bonus"], g["exclude_from_final_grade_calc"],
                "; ".join(g["linked_activities"]),
            ]
            for g in grade_items
        ],
    )
    add_sheet(
        "Checklists",
        ["id", "name", "resource_code", "item_count", "quicklink_count"],
        [
            [
                c["id"], c["name"], c["resource_code"], c["item_count"],
                c.get("quicklink_count", 0),
            ]
            for c in checklists
        ],
    )
    add_sheet(
        "Quizzes",
        [
            "source_file", "id", "ident", "title", "resource_code", "grade_item_code",
            "grade_item_name", "grade_item_out_of", "is_active", "attempts_allowed",
            "time_limit_minutes", "show_clock", "enforce_time_limit", "mark_calculation_type",
            "date_due", "section_count", "candidate_question_count", "draw_count_total",
            "question_type_summary", "quicklink_count", "images_missing_alt", "instructions_text",
            "instructions_html",
        ],
        [
            [
                q["source_file"], q["id"], q["ident"], q["title"], q["resource_code"],
                q["grade_item_code"], q.get("grade_item_name", ""), q.get("grade_item_out_of", ""),
                q["is_active"], q["attempts_allowed"], q["time_limit_minutes"], q["show_clock"],
                q["enforce_time_limit"], q["mark_calculation_type"], q["date_due"], q["section_count"],
                q["candidate_question_count"], q["draw_count_total"], q["question_type_summary"],
                q.get("quicklink_count", 0), q["images_missing_alt"], q["instructions_text"],
                q["instructions_html"],
            ]
            for q in quizzes
        ],
    )
    add_sheet(
        "Activity_Joins",
        ["source_kind", "source_name", "source_code", "join_type", "target", "resolved"],
        [[j["source_kind"], j["source_name"], j["source_code"], j["join_type"], j["target"], j["resolved"]] for j in joins],
    )
    add_sheet("Diagnostics", ["diagnostic"], [[d] for d in diagnostics] or [["None."]])
    wb.save(path)


def render_markdown(
    label: str,
    folders: list[dict],
    discussion_rows: list[dict],
    checklists: list[dict],
    quizzes: list[dict],
    grade_items: list[dict],
    joins: list[dict],
    diagnostics: list[str],
) -> str:
    topics = [r for r in discussion_rows if r["kind"] == "topic"]
    forums = [r for r in discussion_rows if r["kind"] == "forum"]
    unresolved = [j for j in joins if j["resolved"] == "NO"]
    orphans = [j for j in joins if j["join_type"] == "linked_activity"]
    lines = [
        f"# Course Activities Review — {label}",
        "",
        f"- Dropbox folders: {len(folders)} ({sum(1 for f in folders if f['grade_item_code'])} graded)",
        f"- Discussion forums: {len(forums)}; topics: {len(topics)} "
        f"({sum(1 for t in topics if t['grade_item_code'])} graded)",
        f"- Checklists: {len(checklists)} ({sum(c.get('item_count', 0) for c in checklists)} items)",
        f"- Quizzes: {len(quizzes)} ({sum(1 for q in quizzes if q['grade_item_code'])} graded; "
        f"{sum(int(q.get('draw_count_total') or 0) for q in quizzes)} drawn questions)",
        f"- Grade items: {len(grade_items)}",
        f"- Join edges traced: {len(joins)}; unresolved: {len(unresolved)}",
        f"- Grade items with no linked activity: {len(orphans)}",
        f"- Images missing alt text: "
        f"{sum(f['images_missing_alt'] for f in folders) + sum(r['images_missing_alt'] for r in discussion_rows) + sum(q['images_missing_alt'] for q in quizzes)}",
        "",
        "Embedded HTML is preserved verbatim in the workbook's `*_html` columns;",
        "`*_text` columns are readable derivations, not replacements.",
        "",
        "## Diagnostics",
        "",
    ]
    lines.extend(f"- {d}" for d in diagnostics) if diagnostics else lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def safe_label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="Unpacked export directory or export ZIP")
    parser.add_argument("--label", default="", help="Label for output filenames (default: folder name)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write outputs (default: <repo>/workspace/review)",
    )
    args = parser.parse_args(argv)

    holder: list = []
    root = load_export_root(args.export.expanduser().resolve(), holder)
    label = args.label or safe_label(args.export.stem if args.export.is_file() else args.export.name)

    diagnostics: list[str] = []
    grade_items, grade_by_code = load_grade_items(root, diagnostics)
    rubric_names = load_rubric_names(root, diagnostics)
    condition_codes = load_condition_sets(root, diagnostics)
    quicklinks = load_quicklink_codes(root, diagnostics)
    folders = extract_dropbox(root, diagnostics)
    discussion_rows = extract_discussions(root, diagnostics)
    checklists = extract_checklists(root, diagnostics, quicklinks)
    quizzes = extract_quizzes(root, diagnostics, quicklinks)
    joins = resolve_joins(
        folders, discussion_rows, checklists, quizzes, grade_items, grade_by_code,
        rubric_names, condition_codes, quicklinks, diagnostics,
    )

    output_dir = args.output_dir or (Path(__file__).resolve().parents[1] / "workspace" / "review")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_label(label)}__course_activities"
    xlsx_path = output_dir / f"{stem}.xlsx"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"

    write_workbook(xlsx_path, folders, discussion_rows, checklists, quizzes, grade_items, joins, diagnostics)
    json_path.write_text(
        json.dumps(
            {
                "export": str(args.export),
                "label": label,
                "dropbox_folders": folders,
                "discussions": discussion_rows,
                "checklists": checklists,
                "quizzes": quizzes,
                "grade_items": grade_items,
                "joins": joins,
                "diagnostics": diagnostics,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    md_path.write_text(
        render_markdown(label, folders, discussion_rows, checklists, quizzes, grade_items, joins, diagnostics),
        encoding="utf-8",
    )

    print(f"dropbox folders: {len(folders)}")
    print(f"discussion rows: {len(discussion_rows)}")
    print(f"checklists: {len(checklists)}")
    print(f"quizzes: {len(quizzes)}")
    print(f"grade items: {len(grade_items)}")
    print(f"diagnostics: {len(diagnostics)}")
    print(f"workbook: {xlsx_path}")
    print(f"json: {json_path}")
    print(f"note: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
