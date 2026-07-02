#!/usr/bin/env python3
"""Build a flat-file course blueprint (Markdown + DOCX) from a Brightspace export.

This is the standalone, colleague-shareable version of the workbench command.
It runs the bundled export-triage and extraction scripts into one bundle folder,
assembles a structured blueprint *model*, and renders that model to:

- ``<label>__blueprint.json``  -- the structured model (schema: schemas/blueprint_schema.json)
- ``<label>__blueprint.md``    -- the flat Markdown blueprint
- ``<label>__blueprint.docx``  -- a DOCX styled after the 2020 CGPS template

Design stance: **mirror, don't reconstruct.** The blueprint frame (course front
matter + per-week sections) comes from the CGPS template, but each week's inner
structure is taken from the *course's own page headings* rather than forced into
a fixed taxonomy. A small alias table pulls the few universal buckets (Learning
Objectives, Resources) into consistent rows; every other heading is preserved
under its own label. Learning Objectives are split out only when the course
actually uses an objectives heading; otherwise that content stays in Overview.

The blueprint is a *review surface*. It preserves extracted wording where the
package has evidence and keeps missing fields visible ("Needs review") rather
than inventing content.

Usage:
    python3 scripts/build_blueprint_bundle.py export.zip --label my_course
    python3 scripts/build_blueprint_bundle.py /path/to/unpacked/export \\
        --course-number "ABC 123" --course-title "Course Title" --term "Fall 2026"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
DEFAULT_TEMPLATE_REFERENCE = "Course Blueprint Template 2020 CGPS.docx"
TEMPLATE_DOCX = REPO_ROOT / "reference" / DEFAULT_TEMPLATE_REFERENCE
WEEKISH = re.compile(r"\b(week|module|unit)\s*0*(\d{1,2})\b", re.IGNORECASE)

NOT_FOUND_FIELD = "Needs review: not found in export extraction."
NOT_FOUND_LIST = "None found in export extraction."

# Heading alias table — the ONLY course-content taxonomy the tool imposes.
# Everything else is mirrored under the course's own heading text.
OBJECTIVE_KEYS = ("objective", "learning outcome", "outcomes", "goals",
                  "students will be able", "swbat", "competenc")
RESOURCE_KEYS = ("reading", "resource", "material", "multimedia", "media",
                 "video", "watch", "listen", "textbook", "reference",
                 "required", "optional", "explore")
OVERVIEW_KEYS = ("overview", "introduction", "welcome", "start here", "intro",
                 "orientation")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def safe_label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: str) -> str:
    """Collapse runs of whitespace; keep a single readable line of text."""
    return re.sub(r"\s+", " ", value or "").strip()


def run_workbench_script(script_name: str, args: list[str], quiet: bool = False) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / script_name), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{script_name} failed with exit code {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    if not quiet and result.stdout.strip():
        print(result.stdout.strip())
    if not quiet and result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


# --------------------------------------------------------------------------- #
# Manifest tree -> repeatable week/module sections
# --------------------------------------------------------------------------- #
def flatten_nodes(nodes: list[dict], ancestors: tuple[str, ...] = ()) -> list[dict]:
    flattened: list[dict] = []
    for node in nodes:
        row = dict(node)
        row["ancestor_titles"] = ancestors
        flattened.append(row)
        flattened.extend(flatten_nodes(node.get("children", []), ancestors + (node.get("title", ""),)))
    return flattened


def node_sort_key(node: dict) -> tuple[int, str]:
    match = WEEKISH.search(node.get("title", ""))
    if match:
        return (int(match.group(2)), node.get("title", ""))
    return (999, node.get("title", ""))


def group_course_modules(tree: list[dict]) -> list[dict]:
    """Return repeatable blueprint sections from the manifest tree.

    Prefer top-level week/module items. If an export has a single wrapper module
    containing week modules, use those children. If no obvious modules exist,
    fall back to the top-level manifest items so the blueprint still emits a
    reviewable structure.
    """
    top_level = [node for node in tree if node.get("title")]
    weekish_top = [node for node in top_level if WEEKISH.search(node.get("title", ""))]
    if weekish_top:
        return sorted(weekish_top, key=node_sort_key)

    if len(top_level) == 1:
        children = [node for node in top_level[0].get("children", []) if node.get("title")]
        weekish_children = [node for node in children if WEEKISH.search(node.get("title", ""))]
        if weekish_children:
            return sorted(weekish_children, key=node_sort_key)

    module_like = [node for node in top_level if node.get("children") or node.get("kind") == "module"]
    return module_like or top_level


# --------------------------------------------------------------------------- #
# Topic lookup + heading-driven routing
# --------------------------------------------------------------------------- #
def topic_lookup(structure: dict) -> dict[str, dict]:
    by_href: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    for topic in structure.get("html_topics", []):
        href = topic.get("href", "")
        title = topic.get("manifest_title", "")
        if href:
            by_href[href] = topic
        if title:
            by_title[title] = topic
    return {"href": by_href, "title": by_title}


def topic_for_node(node: dict, topics: dict[str, dict]) -> dict | None:
    href = node.get("href", "")
    title = node.get("title", "")
    return topics["href"].get(href) or topics["title"].get(title)


def classify_heading(text: str) -> str | None:
    """Map a heading (or topic title) to a blueprint bucket, or None if unknown.

    Objectives is checked first so 'Learning Objectives' beats the 'material'
    substring; Resources before Overview for the same reason.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if any(key in t for key in OBJECTIVE_KEYS):
        return "objectives"
    if any(key in t for key in RESOURCE_KEYS):
        return "resources"
    if any(key in t for key in OVERVIEW_KEYS):
        return "overview"
    return None


def topic_label(topic: dict) -> str:
    return clean_text(topic.get("manifest_title") or topic.get("html_title") or topic.get("href", ""))


def _plain_blocks(text: str) -> list[dict]:
    text = clean_text(text)
    return [{"kind": "p", "level": 0, "runs": [{"text": text, "href": ""}]}] if text else []


def route_topic(topic: dict):
    """Yield (bucket, label, blocks) triples for one HTML topic.

    Uses the finest structural signal available: the topic's own segment
    headings when present, else the topic title. Intro content (no heading) and
    unknown-but-clearly-introductory content route to Overview; genuinely
    unknown headings route to 'other' so nothing is forced or dropped. Blocks
    preserve paragraph/list/link formatting from the source page.
    """
    segments = topic.get("body_segments")
    if not segments:
        blocks = _plain_blocks(topic.get("body_text", ""))
        if blocks:
            yield "overview", "", blocks
        return

    headed = [seg for seg in segments if seg.get("heading")]
    if not headed:
        blocks = [block for seg in segments for block in seg.get("blocks", [])]
        if not blocks:
            return
        bucket = classify_heading(topic_label(topic)) or "overview"
        yield bucket, topic_label(topic), blocks
        return

    for seg in segments:
        heading = clean_text(seg.get("heading", ""))
        blocks = seg.get("blocks", [])
        if not heading and not blocks:
            continue
        if not heading:
            yield "overview", "", blocks
            continue
        bucket = classify_heading(heading)
        yield (bucket or "other"), heading, blocks


# --------------------------------------------------------------------------- #
# Activity formatting (from D2L object XML)
# --------------------------------------------------------------------------- #
def rcode_set(nodes: list[dict]) -> set[str]:
    return {node.get("rcode", "") for node in nodes if node.get("rcode")}


def format_points(value) -> str:
    """Render a D2L points value cleanly (e.g. '10.000000000' -> '10', '7.50' -> '7.5')."""
    text = str(value).strip()
    if not text:
        return ""
    try:
        num = float(text)
    except ValueError:
        return text
    if num.is_integer():
        return str(int(num))
    return f"{num:g}"


def _meta_label(name: str, details: list[str]) -> str:
    name = clean_text(name)
    detail = "; ".join(d for d in details if d)
    return f"{name} ({detail})" if detail else name


def format_folder(folder: dict) -> dict:
    """Return {label, blocks}: name + points/grade/rubric meta, and instruction blocks."""
    details = []
    if folder.get("out_of"):
        details.append(f"{format_points(folder['out_of'])} pts")
    if folder.get("grade_item_name") and folder.get("grade_item_name") != folder.get("name"):
        details.append(f"grade item: {folder['grade_item_name']}")
    if folder.get("rubrics_resolved"):
        details.append(f"rubric: {folder['rubrics_resolved']}")
    label = _meta_label(folder.get("name", "") or "Assignment", details)
    blocks = folder.get("instructions_blocks") or _plain_blocks(folder.get("instructions_text", ""))
    return {"label": label, "blocks": blocks}


def format_discussion(topic: dict) -> dict:
    """Return {label, blocks}: title + points/grade/rubric meta, and description blocks."""
    details = []
    if topic.get("score_out_of"):
        details.append(f"{format_points(topic['score_out_of'])} pts")
    if topic.get("grade_item_name") and topic.get("grade_item_name") != topic.get("title"):
        details.append(f"grade item: {topic['grade_item_name']}")
    if topic.get("rubrics_resolved"):
        details.append(f"rubric: {topic['rubrics_resolved']}")
    label = _meta_label(topic.get("title", "") or "Discussion", details)
    blocks = topic.get("description_blocks") or _plain_blocks(topic.get("description_text", ""))
    return {"label": label, "blocks": blocks}


# --------------------------------------------------------------------------- #
# Course-level front matter (segment-aware)
# --------------------------------------------------------------------------- #
def find_front_matter(structure: dict, category: str) -> list[dict]:
    """Find a course-level field as blocks, preferring a matching *segment*.

    Looks first for a topic segment whose heading matches the category, then
    falls back to a whole topic whose title matches. Empty list when neither is
    found (renders as 'Needs review').
    """
    title_terms = {
        "description": ("course description", "catalog description", "description"),
        "materials": ("textbook", "required materials", "materials", "readings"),
        "outcomes": ("course learning outcomes", "learning outcomes", "course outcomes", "objectives"),
        "introduction": ("course introduction", "welcome", "start here", "course overview", "introduction"),
    }[category]
    heading_bucket = {
        "description": "overview",  # descriptions rarely have a dedicated heading
        "materials": "resources",
        "outcomes": "objectives",
        "introduction": "overview",
    }[category]

    if category in ("materials", "outcomes"):
        for topic in structure.get("html_topics", []):
            for seg in topic.get("body_segments", []):
                heading = seg.get("heading", "")
                if heading and classify_heading(heading) == heading_bucket and seg.get("blocks"):
                    return seg["blocks"]

    for topic in structure.get("html_topics", []):
        title = f"{topic.get('manifest_title', '')} {topic.get('html_title', '')}".lower()
        if not any(term in title for term in title_terms):
            continue
        blocks = [block for seg in topic.get("body_segments", []) for block in seg.get("blocks", [])]
        if blocks:
            return blocks
        if topic.get("body_text"):
            return _plain_blocks(topic["body_text"])
    return []


# --------------------------------------------------------------------------- #
# Build the structured blueprint model
# --------------------------------------------------------------------------- #
def build_week_model(
    module: dict,
    structure_topics: dict[str, dict],
    folders_by_code: dict[str, dict],
    discussions_by_code: dict[str, dict],
) -> tuple[dict, set[str], set[str]]:
    nodes = flatten_nodes([module])
    module_rcodes = rcode_set(nodes)
    topics = [topic for node in nodes if (topic := topic_for_node(node, structure_topics))]
    folders = [folders_by_code[code] for code in module_rcodes if code in folders_by_code]
    discussions = [discussions_by_code[code] for code in module_rcodes if code in discussions_by_code]
    quiz_links = [node for node in nodes if node.get("kind") == "quiz_link"]

    overview_blocks: list[dict] = []
    objective_blocks: list[dict] = []
    resources: list[dict] = []
    other_sections: list[dict] = []
    for topic in topics:
        for bucket, label, blocks in route_topic(topic):
            if not blocks:
                continue
            if bucket == "overview":
                overview_blocks.extend(blocks)
            elif bucket == "objectives":
                objective_blocks.extend(blocks)
            elif bucket == "resources":
                resources.append({"label": label or topic_label(topic), "blocks": blocks})
            else:
                other_sections.append({"label": label or topic_label(topic), "blocks": blocks})

    assignment_items = [format_folder(folder) for folder in folders]
    assignment_items.extend(
        {"label": clean_text(f"Quiz/assessment link: {node.get('title', '')}"), "blocks": []}
        for node in quiz_links
        if node.get("title")
    )
    discussion_items = [format_discussion(topic) for topic in discussions]

    week = {
        "title": clean_text(module.get("title", "Course Module")) or "Course Module",
        "overview": overview_blocks,
        "learning_objectives": objective_blocks,
        "resources": [item for item in resources if item["blocks"]],
        "assignments": [item for item in assignment_items if item.get("label") or item.get("blocks")],
        "discussions": [item for item in discussion_items if item.get("label") or item.get("blocks")],
        "other_sections": [item for item in other_sections if item["blocks"]],
    }
    placed_folders = {f["resource_code"] for f in folders if f.get("resource_code")}
    placed_discussions = {d["resource_code"] for d in discussions if d.get("resource_code")}
    return week, placed_folders, placed_discussions


def build_blueprint_model(
    structure: dict,
    activities: dict,
    *,
    label: str,
    course_number: str,
    course_title: str,
    term: str,
    template_reference: str,
) -> dict:
    modules = group_course_modules(structure.get("tree", []))
    topics = topic_lookup(structure)
    folders_by_code = {
        folder.get("resource_code", ""): folder
        for folder in activities.get("dropbox_folders", [])
        if folder.get("resource_code")
    }
    discussions_by_code = {
        topic.get("resource_code", ""): topic
        for topic in activities.get("discussions", [])
        if topic.get("kind") == "topic" and topic.get("resource_code")
    }

    weeks: list[dict] = []
    placed_folder_codes: set[str] = set()
    placed_discussion_codes: set[str] = set()
    for module in modules:
        week, folder_codes, discussion_codes = build_week_model(
            module, topics, folders_by_code, discussions_by_code
        )
        weeks.append(week)
        placed_folder_codes.update(folder_codes)
        placed_discussion_codes.update(discussion_codes)

    unplaced_folders = [
        item
        for folder in activities.get("dropbox_folders", [])
        if folder.get("resource_code") not in placed_folder_codes
        for item in [format_folder(folder)]
        if item.get("label") or item.get("blocks")
    ]
    unplaced_discussions = [
        item
        for topic in activities.get("discussions", [])
        if topic.get("kind") == "topic" and topic.get("resource_code") not in placed_discussion_codes
        for item in [format_discussion(topic)]
        if item.get("label") or item.get("blocks")
    ]

    diagnostics = [f"Structure: {clean_text(item)}" for item in structure.get("diagnostics", [])]
    diagnostics += [f"Activities: {clean_text(item)}" for item in activities.get("diagnostics", [])]

    return {
        "schema": "coursecraft.blueprint/2",
        "template_reference": template_reference,
        "course_number": clean_text(course_number),
        "course_title": clean_text(course_title) or clean_text(label.replace("_", " ")),
        "term": clean_text(term),
        "front_matter": {
            "course_description": find_front_matter(structure, "description"),
            "required_materials": find_front_matter(structure, "materials"),
            "course_learning_outcomes": find_front_matter(structure, "outcomes"),
            "course_introduction": find_front_matter(structure, "introduction"),
        },
        "weeks": weeks,
        "unplaced_activities": {
            "assignments": [item for item in unplaced_folders if item],
            "discussions": [item for item in unplaced_discussions if item],
        },
        "diagnostics": diagnostics,
    }


# --------------------------------------------------------------------------- #
# Markdown rendering (consumes the model)
# --------------------------------------------------------------------------- #
LIVE_SCHEMES = ("http://", "https://", "mailto:")


def md_escape(value: str) -> str:
    return clean_text(value).replace("|", r"\|")


def md_inline(runs: list[dict]) -> str:
    """Render link-aware runs to inline Markdown; live URLs become clickable links."""
    parts = []
    for run in runs:
        text = md_escape(run.get("text", ""))
        href = (run.get("href") or "").strip()
        if not text:
            continue
        if href.startswith(LIVE_SCHEMES):
            parts.append(f"[{text}]({href})")
        elif href:
            parts.append(f"{text} ({md_escape(href)})")
        else:
            parts.append(text)
    return " ".join(parts).strip()


def md_blocks(blocks: list[dict], fallback: str) -> str:
    """Render a list of blocks into one Markdown table cell (lines joined by <br>)."""
    lines = []
    for block in blocks:
        inline = md_inline(block.get("runs", []))
        if not inline:
            continue
        lines.append(f"• {inline}" if block.get("kind") == "li" else inline)
    return "<br>".join(lines) if lines else fallback


def md_field(blocks: list[dict]) -> str:
    return md_blocks(blocks, NOT_FOUND_FIELD)


def md_bullets(items: Iterable[str], fallback: str = NOT_FOUND_LIST) -> str:
    cleaned = [md_escape(item) for item in items if md_escape(item)]
    if not cleaned:
        return fallback
    return "<br>".join(f"- {item}" for item in cleaned)


def md_labeled(sections: list[dict], fallback: str = NOT_FOUND_LIST) -> str:
    lines = []
    for sec in sections:
        body = md_blocks(sec.get("blocks", []), "")
        label = md_escape(sec.get("label", ""))
        if label and body:
            lines.append(f"**{label}:**<br>{body}")
        elif label:
            lines.append(f"**{label}**")
        elif body:
            lines.append(body)
    return "<br>".join(lines) if lines else fallback


def render_markdown(model: dict) -> str:
    fm = model["front_matter"]
    header_course = model["course_number"] or "Course #"
    header_term = model["term"] or "Term"

    lines = [
        f"# {header_course} - Course Blueprint - {header_term}",
        "",
        model["course_title"] or "COURSE TITLE",
        "",
        f"> Template format reference: {model['template_reference']}",
        "> Evidence mode: this blueprint mirrors the exported course structure. "
        "Extracted text is source-derived; missing fields remain marked for review.",
        "",
        "| COURSE DESCRIPTION (keep in mind the Course Description must match the published catalog, "
        "any changes must be approved by the Program and planned in advance) |",
        "| --- |",
        f"| {md_field(fm['course_description'])} |",
        "",
        "| TEXTBOOK/S OR REQUIRED MATERIALS |",
        "| --- |",
        f"| {md_field(fm['required_materials'])} |",
        "",
        "| COURSE LEARNING OUTCOMES |",
        "| --- |",
        f"| {md_field(fm['course_learning_outcomes'])} |",
        "",
        "### Course Introduction",
        "",
        md_field(fm["course_introduction"]),
        "",
        "Course Content:",
        "",
    ]

    for week in model["weeks"]:
        rows = [
            f"### {md_escape(week['title'])}",
            "",
            "| Overview: (add an introduction to the week's topic and activities here, "
            f"with references as needed) | {md_field(week['overview'])} |",
            "| --- | --- |",
            "| Learning Objectives: Must follow the guidelines in this Learning Objectives Guide."
            f"<br><br>Students will be able to: | {md_field(week['learning_objectives'])} |",
            f"| Assignment(s) and Instructions: | {md_labeled(week['assignments'])} |",
            f"| Discussion Board Prompts: | {md_labeled(week['discussions'])} |",
            "| Assigned Reading and Multimedia: (add links, articles, textbook readings, videos). "
            f"Include style-correct citations. | {md_labeled(week['resources'])} |",
        ]
        if week["other_sections"]:
            rows.append(f"| Other course sections (mirrored from the export) | {md_labeled(week['other_sections'])} |")
        rows.append("")
        lines.extend(rows)

    unplaced = model["unplaced_activities"]
    if unplaced["assignments"] or unplaced["discussions"]:
        lines.extend(
            [
                "## Unplaced Activities",
                "",
                "These activities were extracted from D2L object XML but were not connected to a "
                "module quicklink by resource code.",
                "",
            ]
        )
        if unplaced["assignments"]:
            lines.extend(["### Assignments", "", md_labeled(unplaced["assignments"]), ""])
        if unplaced["discussions"]:
            lines.extend(["### Discussions", "", md_labeled(unplaced["discussions"]), ""])

    if model["diagnostics"]:
        lines.extend(["## Extraction Notes", "", *[f"- {md_escape(item)}" for item in model["diagnostics"]], ""])
    else:
        lines.extend(["## Extraction Notes", "", "- None.", ""])

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Bundle README
# --------------------------------------------------------------------------- #
def write_bundle_readme(
    path: Path,
    *,
    label: str,
    export: Path,
    blueprint_md: Path,
    blueprint_json: Path,
    blueprint_docx: Path | None,
    template_reference: str,
    include_qa: bool,
) -> None:
    qa_line = "- course QA report" if include_qa else "- course QA report skipped by command option"
    docx_line = f"- `{blueprint_docx.name}`" if blueprint_docx else "- DOCX skipped (python-docx not installed)"
    path.write_text(
        "\n".join(
            [
                f"# Blueprint Bundle - {label}",
                "",
                f"Source export: `{export}`",
                f"Template format reference: `{template_reference}`",
                "",
                "Primary outputs:",
                f"- `{blueprint_md.name}`",
                docx_line,
                f"- `{blueprint_json.name}` (structured model)",
                "",
                "Companion artifacts:",
                "- package inventory",
                "- manifest probe",
                "- course structure JSON/Markdown",
                "- course activities JSON/Markdown/workbook",
                qa_line,
                "",
                "Review note: the blueprint mirrors the exported course structure. It is "
                "source-derived, not a final instructional-design approval. Rows marked "
                "`Needs review` had no clear extracted source in the package.",
                "",
            ]
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="Brightspace export ZIP or unpacked export folder")
    parser.add_argument("--label", default="", help="Label for output filenames")
    parser.add_argument("--course-number", default="", help="Course number for the blueprint heading")
    parser.add_argument("--course-title", default="", help="Course title for the blueprint heading")
    parser.add_argument("--term", default="", help="Term for the blueprint heading")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "output",
        help="Base output directory; a <label>__blueprint_bundle folder is created inside it",
    )
    parser.add_argument("--bundle-dir", type=Path, default=None, help="Exact bundle directory to write")
    parser.add_argument(
        "--template-reference",
        default=DEFAULT_TEMPLATE_REFERENCE,
        help="Human-readable template source note to include in the blueprint",
    )
    parser.add_argument("--skip-qa", action="store_true", help="Do not run course_qa_report.py")
    parser.add_argument("--no-docx", action="store_true", help="Do not render the DOCX blueprint")
    parser.add_argument("--quiet", action="store_true", help="Suppress companion script stdout")
    args = parser.parse_args(argv)

    export = args.export.expanduser().resolve()
    if not export.exists():
        raise SystemExit(f"error: export not found: {export}")
    label = args.label or safe_label(export.stem if export.is_file() else export.name)
    stem = safe_label(label)
    bundle_dir = args.bundle_dir or (args.output_dir / f"{stem}__blueprint_bundle")
    bundle_dir.mkdir(parents=True, exist_ok=True)

    common = [str(export), "--output-dir", str(bundle_dir)]
    labeled = [*common, "--label", label]
    run_workbench_script("export_inventory.py", common, args.quiet)
    run_workbench_script("manifest_probe.py", common, args.quiet)
    run_workbench_script("reconstruct_course_structure.py", [*labeled, "--extract-html"], args.quiet)
    run_workbench_script("extract_course_activities.py", labeled, args.quiet)
    if not args.skip_qa:
        run_workbench_script("course_qa_report.py", labeled, args.quiet)

    structure_path = bundle_dir / f"{stem}__course_structure.json"
    activities_path = bundle_dir / f"{stem}__course_activities.json"
    model = build_blueprint_model(
        read_json(structure_path),
        read_json(activities_path),
        label=label,
        course_number=args.course_number,
        course_title=args.course_title,
        term=args.term,
        template_reference=args.template_reference,
    )

    blueprint_json = bundle_dir / f"{stem}__blueprint.json"
    blueprint_md = bundle_dir / f"{stem}__blueprint.md"
    blueprint_docx = bundle_dir / f"{stem}__blueprint.docx"

    blueprint_json.write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    blueprint_md.write_text(render_markdown(model), encoding="utf-8")

    docx_written: Path | None = None
    if not args.no_docx:
        docx_args = [str(blueprint_json), "--output", str(blueprint_docx)]
        if TEMPLATE_DOCX.exists():
            docx_args += ["--template", str(TEMPLATE_DOCX)]
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "blueprint_to_docx.py"), *docx_args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            docx_written = blueprint_docx
            if not args.quiet and result.stdout.strip():
                print(result.stdout.strip())
        else:
            print(
                "warning: DOCX rendering skipped.\n"
                f"{result.stdout.strip()}\n{result.stderr.strip()}".strip(),
                file=sys.stderr,
            )

    write_bundle_readme(
        bundle_dir / "README.md",
        label=label,
        export=export,
        blueprint_md=blueprint_md,
        blueprint_json=blueprint_json,
        blueprint_docx=docx_written,
        template_reference=args.template_reference,
        include_qa=not args.skip_qa,
    )

    print(f"bundle: {bundle_dir}")
    print(f"blueprint (markdown): {blueprint_md}")
    print(f"blueprint (json):     {blueprint_json}")
    if docx_written:
        print(f"blueprint (docx):     {docx_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
