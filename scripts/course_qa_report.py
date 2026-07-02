#!/usr/bin/env python3
"""Course QA report: one command, severity-tiered integrity checks over an export.

Runs the workbench's parsed models (extract_course_activities for activities/
joins, reconstruct_course_structure for the module tree and HTML topics) plus
QA-only rules over any full Brightspace export or re-export, and reports
breaks / warnings / notes in md + JSON. Read-only and offline: no fixes, no
network calls, no LTI parsing.

Checks:
- join integrity: manifest hrefs/identifierrefs, activity->grade/rubric/
  condition joins, orphan grade items (quiz grade links are traced so
  quiz-graded items don't appear as false orphans), quiz draw counts
- dates: optional term window (--config term_start/term_end), due-before-end,
  stale-year heuristic (warns when a date trails the course's latest year)
- gradebook: expected total (--config expected_total), zero-point items,
  duplicate names
- content: broken package-relative references, empty pages, placeholder
  leakage (e.g. [Insert ...], TBD, PLACEHOLDER), external URL inventory
- accessibility seeds: images missing alt text
- rhythm: gaps in "Week N" module numbering (inference-level note)

Per-course conventions come from a small JSON config, never hardcoded:
    {"term_start": "2026-05-13", "term_end": "2026-08-28",
     "expected_total": 100, "extra_placeholder_patterns": ["FIXME"]}

Usage:
    python3 scripts/course_qa_report.py /path/to/unpacked/export
    python3 scripts/course_qa_report.py export.zip --config dsw821_qa.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_course_activities as activities_mod
import reconstruct_course_structure as structure_mod

PLACEHOLDER_PATTERNS = [
    r"\[insert[^\]]*\]",
    r"\bTBD\b",
    r"\bTODO\b",
    r"PLACEHOLDER",
    r"lorem ipsum",
]
EXTERNAL_URL = re.compile(r"""https?://[^\s"'<>]+""", re.IGNORECASE)


def parse_iso_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except ValueError:
        return None


class QaReport:
    def __init__(self) -> None:
        self.breaks: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def add(self, severity: str, message: str) -> None:
        getattr(self, severity).append(message)


def check_quiz_layer(root: Path, grade_by_code: dict[str, dict], report: QaReport) -> list[str]:
    """Quiz grade links + draw counts. Returns names of quiz-linked grade items."""
    quiz_linked: list[str] = []
    for quiz_path in sorted(root.glob("quiz_d2l_*.xml")):
        try:
            quiz_root = ET.parse(quiz_path).getroot()
        except ET.ParseError as exc:
            report.add("breaks", f"{quiz_path.name} is not well-formed XML: {exc}")
            continue
        title = ""
        for elem in quiz_root.iter():
            if activities_mod.local_name(elem.tag) == "assessment":
                title = elem.attrib.get("title", quiz_path.name)
                break
        for elem in quiz_root.iter():
            if activities_mod.local_name(elem.tag) == "grade_item":
                code = elem.attrib.get("resource_code", "")
                if not code:
                    continue
                grade_item = grade_by_code.get(code)
                if grade_item is None:
                    report.add("breaks", f"quiz {title!r}: grade_item {code} not found in grades_d2l.xml")
                else:
                    grade_item["linked_activities"].append(f"quiz: {title}")
                    quiz_linked.append(grade_item["name"])
        for section in quiz_root.iter():
            if activities_mod.local_name(section.tag) != "section":
                continue
            draw_raw = ""
            for child in section:
                if activities_mod.local_name(child.tag) == "sectionproc_extension":
                    for field in child.iter():
                        if activities_mod.local_name(field.tag) == "qti_metadatafield":
                            label = entry = ""
                            for fc in field:
                                if activities_mod.local_name(fc.tag) == "fieldlabel":
                                    label = activities_mod.clean(fc.text)
                                elif activities_mod.local_name(fc.tag) == "fieldentry":
                                    entry = activities_mod.clean(fc.text)
                            if label == "qmd_numberofitems":
                                draw_raw = entry
            if not draw_raw:
                continue
            try:
                draw = int(float(draw_raw))
            except ValueError:
                report.add("breaks", f"quiz {title!r} section {section.attrib.get('title', '')!r}: invalid draw count {draw_raw!r}")
                continue
            candidates = sum(
                1 for child in section if activities_mod.local_name(child.tag) in ("item", "itemref")
            )
            if draw > candidates:
                report.add(
                    "breaks",
                    f"quiz {title!r} section {section.attrib.get('title', '')!r}: draws {draw} from {candidates} candidates",
                )
    return quiz_linked


def check_dates(dated: list[tuple[str, str]], config: dict, report: QaReport) -> None:
    term_start = parse_iso_date(config.get("term_start", ""))
    term_end = parse_iso_date(config.get("term_end", ""))
    parsed = [(label, parse_iso_date(value)) for label, value in dated if value]
    parsed = [(label, value) for label, value in parsed if value is not None]
    if not parsed:
        report.add("notes", "no dated objects found to check")
        return
    years = sorted({value.year for _, value in parsed})
    latest_year = years[-1]
    for label, value in parsed:
        if term_start and value < term_start:
            report.add("breaks", f"date before term start ({term_start}): {label} = {value}")
        elif term_end and value > term_end:
            report.add("breaks", f"date after term end ({term_end}): {label} = {value}")
        elif value.year < latest_year - 1:
            report.add("warnings", f"possible stale date (course's latest year is {latest_year}): {label} = {value}")


def check_gradebook(grade_items: list[dict], config: dict, report: QaReport) -> None:
    countable = [
        g
        for g in grade_items
        if g.get("is_bonus", "").lower() != "true"
        and g.get("exclude_from_final_grade_calc", "").lower() != "true"
        and g.get("type_id") == "1"
    ]
    total = 0.0
    for g in countable:
        try:
            total += float(g["out_of"] or 0)
        except ValueError:
            report.add("warnings", f"grade item {g['name']!r}: non-numeric out_of {g['out_of']!r}")
    expected = config.get("expected_total")
    if expected is not None:
        if abs(total - float(expected)) > 0.001:
            report.add("breaks", f"gradebook total {total:g} != expected_total {expected:g} ({len(countable)} countable items)")
        else:
            report.add("notes", f"gradebook total matches expected_total {expected:g}")
    else:
        report.add("notes", f"gradebook countable total: {total:g} (set expected_total in --config to enforce)")
    for g in grade_items:
        try:
            if g.get("type_id") == "1" and float(g["out_of"] or 0) == 0:
                report.add("warnings", f"grade item {g['name']!r} is worth 0 points")
        except ValueError:
            pass
    names: dict[str, int] = {}
    for g in grade_items:
        names[g["name"]] = names.get(g["name"], 0) + 1
    for name, count in sorted(names.items()):
        if count > 1:
            report.add("warnings", f"duplicate grade item name x{count}: {name!r}")


def check_placeholders(text_sources: list[tuple[str, str]], patterns: list[str], report: QaReport) -> None:
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    for label, text in text_sources:
        for pattern in compiled:
            match = pattern.search(text)
            if match:
                report.add("breaks", f"placeholder leakage in {label}: {match.group(0)!r}")
                break


def check_week_rhythm(tree: list[dict], report: QaReport) -> None:
    week_numbers = []
    for node in tree:
        match = re.search(r"\bweek\s+(\d+)", node["title"], re.IGNORECASE)
        if match and node["kind"] == "module":
            week_numbers.append(int(match.group(1)))
    if len(week_numbers) < 2:
        return
    missing = sorted(set(range(min(week_numbers), max(week_numbers) + 1)) - set(week_numbers))
    if missing:
        report.add(
            "notes",
            f"week-module numbering gap (inference): weeks {missing} absent between "
            f"{min(week_numbers)} and {max(week_numbers)}",
        )


def collect_external_urls(text_sources: list[tuple[str, str]]) -> list[str]:
    urls = set()
    for _, text in text_sources:
        urls.update(EXTERNAL_URL.findall(text))
    return sorted(urls)


def render_markdown(label: str, report: QaReport, summary: dict, external_urls: list[str]) -> str:
    lines = [
        f"# Course QA Report — {label}",
        "",
        f"- Breaks: {len(report.breaks)}",
        f"- Warnings: {len(report.warnings)}",
        f"- Notes: {len(report.notes)}",
        "- Scope: " + ", ".join(f"{key}: {value}" for key, value in summary.items()),
        "",
        "Read-only diagnostics over the export. Breaks need a decision before",
        "launch/import; warnings deserve a look; notes are context. External",
        "URLs are inventoried, not fetched.",
        "",
        "## Breaks",
        "",
    ]
    lines.extend(f"- {b}" for b in report.breaks) if report.breaks else lines.append("- None.")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {w}" for w in report.warnings) if report.warnings else lines.append("- None.")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {n}" for n in report.notes) if report.notes else lines.append("- None.")
    lines.extend(["", "## External URLs (inventory only)", ""])
    if external_urls:
        lines.extend(f"- {url}" for url in external_urls[:200])
        if len(external_urls) > 200:
            lines.append(f"- … and {len(external_urls) - 200} more (see JSON)")
    else:
        lines.append("- None found.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="Unpacked export directory or export ZIP")
    parser.add_argument("--label", default="", help="Label for output filenames (default: folder name)")
    parser.add_argument("--config", type=Path, default=None, help="Per-course QA config JSON")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write outputs (default: <repo>/workspace/review)",
    )
    parser.add_argument("--fail-on-break", action="store_true", help="Exit 1 when any break is found")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    holder: list = []
    root = activities_mod.load_export_root(args.export.expanduser().resolve(), holder)
    label = args.label or activities_mod.safe_label(
        args.export.stem if args.export.is_file() else args.export.name
    )

    report = QaReport()

    # Activities + joins (Phase 1 model); its diagnostics are QA breaks.
    diagnostics: list[str] = []
    grade_items, grade_by_code = activities_mod.load_grade_items(root, diagnostics)
    rubric_names = activities_mod.load_rubric_names(root, diagnostics)
    condition_codes = activities_mod.load_condition_sets(root, diagnostics)
    quicklinks = activities_mod.load_quicklink_codes(root, diagnostics)
    folders = activities_mod.extract_dropbox(root, diagnostics)
    discussion_rows = activities_mod.extract_discussions(root, diagnostics)
    joins = activities_mod.resolve_joins(
        folders, discussion_rows, grade_items, grade_by_code,
        rubric_names, condition_codes, quicklinks, diagnostics,
    )
    for diagnostic in diagnostics:
        # Absent optional payload files (no dropbox/discussion/grades XML) are
        # warnings — legitimate for component bundles; broken joins are breaks.
        severity = "warnings" if "present in export" in diagnostic else "breaks"
        report.add(severity, diagnostic)

    check_quiz_layer(root, grade_by_code, report)
    for join in joins:
        if join["join_type"] == "linked_activity":
            grade_item = next((g for g in grade_items if g["name"] == join["source_name"]), None)
            if grade_item is not None and grade_item["linked_activities"]:
                continue  # quiz link traced above; not an orphan
            report.add("warnings", f"grade item with no linked activity: {join['source_name']!r}")

    # Structure + HTML (Phase 3 model).
    structure_diagnostics: list[str] = []
    manifest_path = root / "imsmanifest.xml"
    tree: list[dict] = []
    topics: list[dict] = []
    if manifest_path.exists():
        try:
            manifest_root = ET.parse(manifest_path).getroot()
        except ET.ParseError as exc:
            report.add("breaks", f"imsmanifest.xml is not well-formed: {exc}")
            manifest_root = None
        if manifest_root is not None:
            resources = structure_mod.load_resources(manifest_root)
            organizations = [el for el in manifest_root.iter() if structure_mod.local_name(el.tag) == "organization"]
            for organization in organizations:
                tree.extend(structure_mod.walk_items(organization, resources, root, structure_diagnostics))
            topics = structure_mod.extract_html_topics(tree, root, structure_diagnostics)
    else:
        report.add("breaks", "imsmanifest.xml missing from export")
    for diagnostic in structure_diagnostics:
        severity = "warnings" if "page body is empty" in diagnostic else "breaks"
        report.add(severity, diagnostic)

    # Dates.
    dated: list[tuple[str, str]] = []
    dated.extend((f"dropbox folder {f['name']!r} date_due", f["date_due"]) for f in folders if f["date_due"])
    if manifest_path.exists():
        try:
            for item in ET.parse(manifest_path).getroot().iter():
                if structure_mod.local_name(item.tag) == "item" and item.attrib.get("date_due"):
                    title = next(
                        (activities_mod.clean(c.text) for c in item if structure_mod.local_name(c.tag) == "title"),
                        item.attrib.get("identifier", ""),
                    )
                    dated.append((f"manifest item {title!r} date_due", item.attrib["date_due"]))
        except ET.ParseError:
            pass  # already reported above
    checklist_path = root / "checklist_d2l.xml"
    if checklist_path.exists():
        try:
            checklist_root = ET.parse(checklist_path).getroot()
            for item in checklist_root.iter():
                if structure_mod.local_name(item.tag) == "date_end" and activities_mod.clean(item.text):
                    dated.append(("checklist item date_end", activities_mod.clean(item.text)))
        except ET.ParseError as exc:
            report.add("breaks", f"checklist_d2l.xml is not well-formed: {exc}")
    check_dates(dated, config, report)

    # Gradebook.
    check_gradebook(grade_items, config, report)

    # Placeholder leakage + accessibility + external URLs.
    text_sources: list[tuple[str, str]] = []
    text_sources.extend((f"dropbox folder {f['name']!r} instructions", f["instructions_html"]) for f in folders)
    text_sources.extend(
        (f"discussion topic {r['title']!r} description", r["description_html"])
        for r in discussion_rows
        if r["kind"] == "topic"
    )
    text_sources.extend((f"html topic {t['manifest_title']!r}", t["body_text"]) for t in topics)
    patterns = PLACEHOLDER_PATTERNS + config.get("extra_placeholder_patterns", [])
    check_placeholders(text_sources, patterns, report)

    missing_alt = sum(f["images_missing_alt"] for f in folders)
    missing_alt += sum(r["images_missing_alt"] for r in discussion_rows)
    missing_alt += sum(t["images_missing_alt"] for t in topics)
    if missing_alt:
        report.add("warnings", f"images missing alt text across activities and pages: {missing_alt}")

    check_week_rhythm(tree, report)
    external_urls = collect_external_urls(text_sources)

    summary = {
        "dropbox_folders": len(folders),
        "discussion_topics": sum(1 for r in discussion_rows if r["kind"] == "topic"),
        "grade_items": len(grade_items),
        "manifest_items": sum(structure_mod.count_kinds(tree).values()),
        "html_topics": len(topics),
        "external_urls": len(external_urls),
    }

    output_dir = args.output_dir or (Path(__file__).resolve().parents[1] / "workspace" / "review")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{activities_mod.safe_label(label)}__course_qa"
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"
    md_path.write_text(render_markdown(label, report, summary, external_urls), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "export": str(args.export),
                "label": label,
                "config": config,
                "summary": summary,
                "breaks": report.breaks,
                "warnings": report.warnings,
                "notes": report.notes,
                "external_urls": external_urls,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"breaks: {len(report.breaks)}")
    print(f"warnings: {len(report.warnings)}")
    print(f"notes: {len(report.notes)}")
    print(f"report: {md_path}")
    print(f"json: {json_path}")
    if args.fail_on_break and report.breaks:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
