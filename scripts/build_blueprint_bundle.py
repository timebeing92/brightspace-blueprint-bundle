#!/usr/bin/env python3
"""Build a flat-file course blueprint (Markdown + DOCX) from a Brightspace export.

This is the workbench-native export-to-blueprint command. It runs the standard
export-triage and extraction scripts into one bundle folder, assembles a
structured blueprint *model*, and renders that model to:

- ``<label>__blueprint.json``  -- the structured model
- ``<label>__blueprint.md``    -- the flat Markdown blueprint
- ``<label>__blueprint.docx``  -- a DOCX styled after the 2020 CGPS template

Design stance: **mirror, don't reconstruct.** The blueprint frame (course front
matter + per-week sections) comes from the CGPS template, but each week's inner
structure is taken from the *course's own page headings* rather than forced into
a fixed taxonomy. A small alias table pulls the few universal buckets (Learning
Objectives, Resources, Checklist) into consistent rows; every other heading is
preserved under its own label with page provenance ("Page › Heading"), so
distinct pages stay distinct. Learning Objectives are split out only when the
course actually uses an objectives heading; otherwise that content stays in
Overview.

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

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
DEFAULT_TEMPLATE_REFERENCE = "Course Blueprint Template 2020 CGPS.docx"
TEMPLATE_DOCX = REPO_ROOT / "workspace" / "reference" / "blueprints" / "templates" / DEFAULT_TEMPLATE_REFERENCE
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
                 "orientation", "independent research")
CHECKLIST_KEYS = ("checklist",)
# Practice before assessments so "Self-Assessment" is practice, not assessment.
PRACTICE_KEYS = ("practice", "self-assessment", "self assessment",
                 "self-check", "self check")
ASSESSMENT_KEYS = ("quiz", "midterm", "assessment", "cumulative")
_EXAM_RE = re.compile(r"\bexam(s|ination|inations)?\b", re.IGNORECASE)  # not "example"
LESSON_KEYS = ("lesson", "lecture")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def safe_label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_if_changed(path: Path, content: str) -> None:
    """Avoid rewriting compatibility artifacts when content is unchanged."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def clean_text(value: str) -> str:
    """Collapse runs of whitespace; keep a single readable line of text."""
    return re.sub(r"\s+", " ", value or "").strip()


def clean_label(value: str) -> str:
    """Normalize a display label before renderers add their own trailing colon."""
    return clean_text(value).rstrip(":").strip()


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


def classify_heading(text: str, *, page_title: bool = False) -> str | None:
    """Map a heading (or, with page_title=True, a topic title) to a blueprint
    bucket, or None if unknown.

    Check order matters: checklist and practice first (so 'Self-Assessment'
    beats 'assessment'), objectives before resources (so 'Learning Objectives'
    beats the 'material' substring). For segment headings, resources beats
    overview; for page titles the priority flips — a page titled 'Week 1
    Overview and Learning Materials' is the week's overview page, and a
    'Lesson N' title wins over its own 'intro'-ish words.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if any(key in t for key in CHECKLIST_KEYS):
        return "checklist"
    if any(key in t for key in PRACTICE_KEYS):
        return "practice"
    if any(key in t for key in OBJECTIVE_KEYS):
        return "objectives"
    if any(key in t for key in ASSESSMENT_KEYS) or _EXAM_RE.search(t):
        return "assessments"
    ordered = (
        (LESSON_KEYS, "lessons"), (OVERVIEW_KEYS, "overview"), (RESOURCE_KEYS, "resources"),
    ) if page_title else (
        (RESOURCE_KEYS, "resources"), (LESSON_KEYS, "lessons"), (OVERVIEW_KEYS, "overview"),
    )
    for keys, bucket in ordered:
        if any(key in t for key in keys):
            return bucket
    return None


def topic_label(topic: dict) -> str:
    return clean_text(topic.get("manifest_title") or topic.get("html_title") or topic.get("href", ""))


def _plain_blocks(text: str) -> list[dict]:
    text = clean_text(text)
    return [{"kind": "p", "level": 0, "runs": [{"text": text, "href": ""}]}] if text else []


def _page_default_bucket(page: str) -> str:
    """Bucket for a page classified only by its title: week-titled or untitled
    pages read as the week's own narrative (overview); any other distinctly
    titled page is preserved as its own labeled section rather than merged
    anonymously into Overview."""
    if not page or WEEKISH.search(page):
        return "overview"
    return "other"


def _path_label(page: str, path: list[str]) -> str:
    """Join page title + heading path into a provenance label ("Page › Heading").

    Adjacent parts where one contains the other collapse to the longer part, so
    'Lesson 1.1 The Anatomy of a Prediction › The Anatomy of a Prediction ›
    Practice: X' reads as 'Lesson 1.1 The Anatomy of a Prediction › Practice: X'.
    """
    parts: list[str] = []
    for part in [page, *path]:
        part = clean_label(part)
        if not part:
            continue
        if parts and (part.lower() in parts[-1].lower() or parts[-1].lower() in part.lower()):
            if len(part) > len(parts[-1]):
                parts[-1] = part
            continue
        parts.append(part)
    return " › ".join(parts)


def route_topic(topic: dict):
    """Yield routing entries for one HTML topic:
    ``{bucket, label, blocks, source_page, level}``.

    Uses the finest structural signal available: the topic's own segment
    headings when present, else the topic title. Every entry carries its source
    page title so distinct pages stay distinct in the output. Headings that
    match no alias go to 'other' under a "Page › Heading" path label built from
    the h1–h4 hierarchy; nothing is forced or dropped. Blocks preserve
    paragraph/list/link formatting from the source page.
    """
    page = topic_label(topic)
    page_bucket = classify_heading(page, page_title=True)
    segments = topic.get("body_segments")
    if not segments:
        blocks = _plain_blocks(topic.get("body_text", ""))
        if blocks:
            yield {"bucket": page_bucket or _page_default_bucket(page),
                   "label": page, "blocks": blocks, "source_page": page, "level": 0}
        return

    headed = [seg for seg in segments if seg.get("heading")]
    if not headed:
        blocks = [block for seg in segments for block in seg.get("blocks", [])]
        if not blocks:
            return
        yield {"bucket": page_bucket or _page_default_bucket(page),
               "label": page, "blocks": blocks, "source_page": page, "level": 0}
        return

    stack: list[tuple[int, str]] = []
    for seg in segments:
        heading = clean_text(seg.get("heading", ""))
        blocks = seg.get("blocks", [])
        if not heading and not blocks:
            continue
        if not heading:
            # Intro content before the first heading follows the page's own
            # classification (a "Learning Materials" page intro belongs with
            # resources, not the week overview).
            yield {"bucket": page_bucket or "overview",
                   "label": page if page_bucket else "",
                   "blocks": blocks, "source_page": page, "level": 0}
            continue
        level = int(seg.get("level") or 0)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        bucket = classify_heading(heading)
        label = heading if bucket else _path_label(page, [h for _, h in stack])
        yield {"bucket": bucket or "other", "label": label, "blocks": blocks,
               "source_page": page, "level": level}


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


def _label_block(text: str) -> dict:
    return {"kind": "label", "level": 0, "runs": [{"text": clean_label(text), "href": ""}]}


def _activity_meta_blocks(details: list[tuple[str, str]]) -> list[dict]:
    blocks = []
    for name, value in details:
        value = clean_text(value)
        if value:
            blocks.append(_label_block(f"{name}: {value}"))
    return blocks


def format_folder(folder: dict) -> dict:
    """Return {label, blocks}: name plus styled metadata and instruction blocks."""
    details: list[tuple[str, str]] = []
    if folder.get("out_of"):
        details.append(("Points", f"{format_points(folder['out_of'])} pts"))
    if folder.get("grade_item_name") and folder.get("grade_item_name") != folder.get("name"):
        details.append(("Gradebook item", folder["grade_item_name"]))
    if folder.get("rubrics_resolved"):
        details.append(("Rubric", folder["rubrics_resolved"]))
    label = clean_label(folder.get("name", "") or "Assignment")
    blocks = folder.get("instructions_blocks") or _plain_blocks(folder.get("instructions_text", ""))
    return {"label": label, "blocks": _activity_meta_blocks(details) + blocks}


def format_discussion(topic: dict) -> dict:
    """Return {label, blocks}: title plus styled metadata and description blocks."""
    details: list[tuple[str, str]] = []
    if topic.get("score_out_of"):
        details.append(("Points", f"{format_points(topic['score_out_of'])} pts"))
    if topic.get("grade_item_name") and topic.get("grade_item_name") != topic.get("title"):
        details.append(("Gradebook item", topic["grade_item_name"]))
    if topic.get("rubrics_resolved"):
        details.append(("Rubric", topic["rubrics_resolved"]))
    label = clean_label(topic.get("title", "") or "Discussion")
    blocks = topic.get("description_blocks") or _plain_blocks(topic.get("description_text", ""))
    return {"label": label, "blocks": _activity_meta_blocks(details) + blocks}


def format_checklist(checklist: dict | None, link_node: dict | None = None) -> dict:
    """Return {label, blocks} for a D2L checklist tool payload or manifest link."""
    link_title = clean_label((link_node or {}).get("title", ""))
    label = clean_label((checklist or {}).get("name", "") or link_title or "Checklist")
    blocks = list((checklist or {}).get("blocks", []))
    if blocks:
        return {"label": label, "blocks": blocks}

    if checklist is None:
        text = (
            "Brightspace checklist tool link found in the module; item-level "
            "checklist contents were not found in checklist_d2l.xml."
        )
    else:
        text = "Brightspace checklist found, but no checklist items were extracted."
    return {"label": label, "blocks": _plain_blocks(text)}


def format_quiz(quiz: dict | None, link_node: dict | None = None) -> dict:
    """Return {label, blocks} for a D2L quiz payload or manifest quicklink."""
    link_title = clean_label((link_node or {}).get("title", ""))
    label = clean_label((quiz or {}).get("title", "") or link_title or "Quiz")
    if quiz is None:
        text = (
            "Brightspace quiz link found in the module; quiz instructions and settings "
            "were not found in quiz_d2l XML."
        )
        return {"label": clean_label(f"Quiz: {label}"), "blocks": _plain_blocks(text)}

    details: list[tuple[str, str]] = []
    if quiz.get("grade_item_out_of"):
        details.append(("Points", f"{format_points(quiz['grade_item_out_of'])} pts"))
    if quiz.get("grade_item_name"):
        details.append(("Gradebook item", quiz["grade_item_name"]))
    if quiz.get("attempts_allowed"):
        details.append(("Attempts allowed", quiz["attempts_allowed"]))
    time_limit = clean_text(quiz.get("time_limit_minutes", ""))
    if time_limit:
        rendered_limit = "No time limit" if time_limit in {"0", "0.0"} else f"{format_points(time_limit)} minutes"
        details.append(("Time limit", rendered_limit))
    if quiz.get("enforce_time_limit") and quiz.get("enforce_time_limit") != "no":
        details.append(("Time limit enforced", quiz["enforce_time_limit"]))
    if quiz.get("draw_count_total"):
        questions = f"{quiz['draw_count_total']} drawn"
        if quiz.get("candidate_question_count"):
            questions += f" from {quiz['candidate_question_count']} candidates"
        details.append(("Questions", questions))
    if quiz.get("section_count"):
        details.append(("Sections", str(quiz["section_count"])))
    if quiz.get("question_type_summary"):
        details.append(("Question types", quiz["question_type_summary"]))
    if quiz.get("is_active") and quiz.get("is_active") != "yes":
        details.append(("Active", quiz["is_active"]))

    blocks = quiz.get("instructions_blocks") or _plain_blocks(quiz.get("instructions_text", ""))
    if not blocks:
        blocks = _plain_blocks("Brightspace quiz found, but no quiz-level instructions were extracted.")
    return {"label": clean_label(f"Quiz: {label}"), "blocks": _activity_meta_blocks(details) + blocks}


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
    checklists_by_code: dict[str, dict],
    quizzes_by_code: dict[str, dict],
) -> tuple[dict, set[str], set[str], set[str]]:
    nodes = flatten_nodes([module])
    module_rcodes = rcode_set(nodes)
    topics = [topic for node in nodes if (topic := topic_for_node(node, structure_topics))]
    folders = [folders_by_code[code] for code in module_rcodes if code in folders_by_code]
    discussions = [discussions_by_code[code] for code in module_rcodes if code in discussions_by_code]
    quiz_links = [node for node in nodes if node.get("kind") == "quiz_link"]
    checklist_links = [node for node in nodes if node.get("kind") == "checklist_link"]

    overview_blocks: list[dict] = []
    objective_blocks: list[dict] = []
    resources: list[dict] = []
    checklist: list[dict] = []
    other_sections: list[dict] = []

    def extend_overview(entry: dict) -> None:
        label = clean_label(entry.get("label", ""))
        bare_label = label.lower()
        if label and bare_label not in {"overview", "introduction", "welcome", "start here"}:
            overview_blocks.append(
                {"kind": "label", "level": 0, "runs": [{"text": f"{label}:", "href": ""}]}
            )
        overview_blocks.extend(entry["blocks"])

    for topic in topics:
        for entry in route_topic(topic):
            blocks = entry["blocks"]
            if not blocks:
                continue
            bucket = entry["bucket"]
            if bucket == "overview":
                extend_overview(entry)
            elif bucket == "objectives":
                objective_blocks.extend(blocks)
            else:
                section = {
                    "label": entry["label"] or entry["source_page"],
                    "blocks": blocks,
                    "source_page": entry["source_page"],
                    "level": entry["level"],
                }
                if bucket == "resources":
                    resources.append(section)
                elif bucket == "checklist":
                    checklist.append(section)
                else:
                    other_sections.append(section)

    assignment_items = [format_folder(folder) for folder in folders]
    assignment_items.extend(
        format_quiz(quizzes_by_code.get(node.get("rcode", "")), node)
        for node in quiz_links
        if node.get("title") or node.get("rcode")
    )
    discussion_items = [format_discussion(topic) for topic in discussions]
    checklist.extend(
        format_checklist(checklists_by_code.get(node.get("rcode", "")), node)
        for node in checklist_links
        if node.get("title") or node.get("rcode")
    )

    week = {
        "title": clean_text(module.get("title", "Course Module")) or "Course Module",
        "overview": overview_blocks,
        "learning_objectives": objective_blocks,
        "resources": [item for item in resources if item["blocks"]],
        "assignments": [item for item in assignment_items if item.get("label") or item.get("blocks")],
        "discussions": [item for item in discussion_items if item.get("label") or item.get("blocks")],
        "checklist": [item for item in checklist if item["blocks"]],
        "other_sections": [item for item in other_sections if item["blocks"]],
    }
    placed_folders = {f["resource_code"] for f in folders if f.get("resource_code")}
    placed_discussions = {d["resource_code"] for d in discussions if d.get("resource_code")}
    placed_quizzes = {
        node.get("rcode", "")
        for node in quiz_links
        if node.get("rcode", "") in quizzes_by_code
    }
    return week, placed_folders, placed_discussions, placed_quizzes


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
    checklists_by_code = {
        checklist.get("resource_code", ""): checklist
        for checklist in activities.get("checklists", [])
        if checklist.get("resource_code")
    }
    quizzes_by_code = {
        quiz.get("resource_code", ""): quiz
        for quiz in activities.get("quizzes", [])
        if quiz.get("resource_code")
    }

    weeks: list[dict] = []
    placed_folder_codes: set[str] = set()
    placed_discussion_codes: set[str] = set()
    placed_quiz_codes: set[str] = set()
    for module in modules:
        week, folder_codes, discussion_codes, quiz_codes = build_week_model(
            module, topics, folders_by_code, discussions_by_code, checklists_by_code, quizzes_by_code
        )
        weeks.append(week)
        placed_folder_codes.update(folder_codes)
        placed_discussion_codes.update(discussion_codes)
        placed_quiz_codes.update(quiz_codes)

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
    unplaced_quizzes = [
        item
        for quiz in activities.get("quizzes", [])
        if quiz.get("resource_code") not in placed_quiz_codes
        for item in [format_quiz(quiz)]
        if item.get("label") or item.get("blocks")
    ]

    diagnostics = [f"Structure: {clean_text(item)}" for item in structure.get("diagnostics", [])]
    diagnostics += [f"Activities: {clean_text(item)}" for item in activities.get("diagnostics", [])]
    return {
        "schema": "coursecraft.blueprint/3",
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
            "assignments": [item for item in [*unplaced_folders, *unplaced_quizzes] if item],
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
    """Render a list of blocks into one Markdown table cell.

    List items get bullets indented by nesting level; consecutive bullets stay
    tight while paragraph boundaries get a blank line so prose keeps its shape.
    """
    parts: list[tuple[str, str]] = []
    for block in blocks:
        inline = md_inline(block.get("runs", []))
        if not inline:
            continue
        if block.get("kind") == "li":
            indent = "&nbsp;&nbsp;&nbsp;" * max(0, int(block.get("level") or 1) - 1)
            parts.append(("li", f"{indent}• {inline}"))
        elif block.get("kind") == "label":
            parts.append(("label", f"**{inline}**"))
        else:
            parts.append(("p", inline))
    if not parts:
        return fallback
    out: list[str] = []
    for index, (kind, text) in enumerate(parts):
        if index:
            tight = (
                (kind == "li" and parts[index - 1][0] == "li")
                or (kind == "label" and parts[index - 1][0] == "label")
            )
            out.append("<br>" if tight else "<br><br>")
        out.append(text)
    return "".join(out)


def md_field(blocks: list[dict]) -> str:
    return md_blocks(blocks, NOT_FOUND_FIELD)


def md_labeled(sections: list[dict], fallback: str = NOT_FOUND_LIST) -> str:
    lines = []
    for sec in sections:
        body = md_blocks(sec.get("blocks", []), "")
        label = md_escape(clean_label(sec.get("label", "")))
        if label and body:
            lines.append(f"**{label}:**<br>{body}")
        elif label:
            lines.append(f"**{label}**")
        elif body:
            lines.append(body)
    return "<br><br>".join(lines) if lines else fallback


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
        section_rows = [
            (
                "Overview: (add an introduction to the week's topic and activities here, with references as needed)",
                md_field(week["overview"]),
            ),
            (
                "Learning Objectives: Must follow the guidelines in this Learning Objectives Guide.<br><br>Students will be able to:",
                md_field(week["learning_objectives"]),
            ),
            (
                "Assigned Reading and Multimedia: (add links, articles, textbook readings, videos). Include style-correct citations.",
                md_labeled(week["resources"]),
            ),
            ("Assignment(s) and Instructions:", md_labeled(week["assignments"])),
            ("Discussion Board Prompts:", md_labeled(week["discussions"])),
        ]
        if week["checklist"]:
            section_rows.append(("Checklist (mirrored from the export)", md_labeled(week["checklist"])))
        if week["other_sections"]:
            section_rows.append(("Other course sections (mirrored from the export)", md_labeled(week["other_sections"])))
        rows = [f"### {md_escape(week['title'])}", "", "| Section |", "| --- |"]
        for label, body in section_rows:
            rows.append(f"| **{label}** |")
            rows.append(f"| {body} |")
        rows.extend(["", "---", ""])
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
        default=REPO_ROOT / "workspace" / "review",
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
    parser.add_argument(
        "--docx-section-layout",
        choices=("top", "left"),
        default="top",
        help="DOCX weekly section label layout: shaded top rows (default) or shaded left column.",
    )
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
    legacy_blueprint_md = bundle_dir / f"{stem}__cps_blueprint.md"
    blueprint_docx = bundle_dir / f"{stem}__blueprint.docx"

    blueprint_json.write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rendered_markdown = render_markdown(model)
    write_text_if_changed(blueprint_md, rendered_markdown)
    write_text_if_changed(legacy_blueprint_md, rendered_markdown)

    docx_written: Path | None = None
    if not args.no_docx:
        docx_args = [
            str(blueprint_json),
            "--output",
            str(blueprint_docx),
            "--section-layout",
            args.docx_section_layout,
        ]
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
    print(f"blueprint (legacy markdown alias): {legacy_blueprint_md}")
    print(f"blueprint (json):     {blueprint_json}")
    if docx_written:
        print(f"blueprint (docx):     {docx_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
