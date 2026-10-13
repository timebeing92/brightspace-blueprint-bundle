#!/usr/bin/env python3
"""Reconstruct the module/topic tree of a Brightspace export for review.

Reads imsmanifest.xml organizations, resolves each item's identifierref to its
resource (type, d2l material_type, href), classifies items (module, HTML topic,
quicklink by target type, tool payload, LTI link — typed but not parsed), and
flags hidden items, unresolved identifierrefs, and missing hrefs.

With --extract-html, also pulls each extractable HTML content topic's title and
body text verbatim into the JSON payload and checks that relative asset/link
references inside each page resolve within the package (extraction mode: no
editorial changes, readable text is derived alongside the raw page, never
replacing it). Non-HTML course files are preserved as linked references instead
of being decoded as page text.

Outputs a markdown tree and canonical JSON to workspace/review/.

Usage:
    python3 scripts/reconstruct_course_structure.py /path/to/unpacked/export
    python3 scripts/reconstruct_course_structure.py export.zip --extract-html
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from common_xml import (
    ALT_ATTR,
    IMG_TAG,
    SRC_ATTR,
    URL_RCODE,
    clean,
    flatten_html_text as _flatten_text,
    html_to_text,
    load_export_root,
    local_name,
    missing_alt_image_refs,
    resolve_export_root,
    safe_label,
)
from course_artifact_contracts import (
    annotate_structure_payload,
    load_source_identity,
    new_run_id,
    validate_contract,
)
from creatorplus_package import (
    FILENAME_POLICIES,
    PACKAGE_PROFILES,
    CreatorPlusPackageReport,
    format_diagnostic as format_creatorplus_diagnostic,
    inspect_creatorplus_package,
)

URL_SCHEMES = ("http://", "https://", "data:", "mailto:", "javascript:", "tel:", "#", "//")
QUICKLINK_TYPE = re.compile(r"[?&]type=([A-Za-z]+)")
HTML_REFERENCE_ATTRIBUTES = {"href", "src", "data-file"}
CREATORPLUS_PROVIDER_HREF_ATTRIBUTE = "data-d2l-activity-href"
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HTML_TOPIC_SUFFIXES = {".html", ".htm", ".xhtml"}
TEXT_TOPIC_SUFFIXES = {".txt", ".md", ".markdown"}
CONTENT_FILE_KIND = "content_file"
_TEXT_CONTROL_BYTES = set(range(0, 9)) | set(range(14, 32)) | {127}
CORE_PACKAGE_FILES = {
    "imsmanifest.xml",
    "grades_d2l.xml",
    "rubrics_d2l.xml",
    "dropbox_d2l.xml",
    "checklist_d2l.xml",
    "questiondb.xml",
    "orgunitconfig.xml",
}
DEFAULT_SYLLABUS_HOSTS = ("syllabi.une.edu",)
MAX_SYLLABUS_BYTES = 5 * 1024 * 1024


def _href_path_part(href: str) -> str:
    return html.unescape(unquote(href or "")).replace("\\", "/").split("?")[0].split("#")[0].lstrip("/")


def _is_local_href(href: str) -> bool:
    href = (href or "").strip()
    return bool(href) and not href.lower().startswith(URL_SCHEMES)


def _href_suffix(href: str) -> str:
    target = _href_path_part(href)
    return Path(target).suffix.lower() if target else ""


def _is_content_file_href(href: str) -> bool:
    suffix = _href_suffix(href)
    if not suffix:
        return False
    return suffix not in HTML_TOPIC_SUFFIXES and suffix not in TEXT_TOPIC_SUFFIXES


def _looks_like_binary_payload(data: bytes) -> bool:
    sample = data[:8192]
    if not sample:
        return False
    lower_sample = sample[:1024].lower().lstrip()
    if lower_sample.startswith((b"<!doctype", b"<html", b"<body", b"<h1", b"<h2", b"<p")) or b"<html" in lower_sample:
        return False
    if sample.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"%PDF-")):
        return True
    if b"[Content_Types].xml" in sample or b"word/document.xml" in sample:
        return True
    if sample.count(b"\x00") / len(sample) > 0.01:
        return True
    control_count = sum(1 for byte in sample if byte in _TEXT_CONTROL_BYTES)
    return control_count / len(sample) > 0.20


class _HTMLReferenceCollector(HTMLParser):
    """Collect exact HTML reference attributes without substring matching.

    Creator+ uses ``data-d2l-activity-href`` for provider lifecycle wiring and
    ``data-file`` for its package-local JSON payload. Treating the former as an
    ordinary ``href`` creates a false broken-link diagnostic; ignoring the
    latter hides a real package dependency.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[dict[str, str]] = []
        self.provider_hrefs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for raw_name, raw_value in attrs:
            name = (raw_name or "").casefold()
            value = (raw_value or "").strip()
            if name in HTML_REFERENCE_ATTRIBUTES and value:
                self.references.append({"tag": tag.casefold(), "attribute": name, "value": value})
            elif name == CREATORPLUS_PROVIDER_HREF_ATTRIBUTE:
                state = "missing"
                if value.startswith("REPLACE_WITH_"):
                    state = "placeholder"
                elif value.lower().startswith(("http://", "https://")):
                    state = "provider_backed"
                elif value:
                    state = "unknown"
                self.provider_hrefs.append({"value": value, "state": state})


def collect_html_references(raw: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    collector = _HTMLReferenceCollector()
    collector.feed(raw)
    collector.close()
    return collector.references, collector.provider_hrefs


def resolve_html_reference(
    ref: str,
    *,
    page_path: Path,
    package_root: Path,
) -> tuple[str, str]:
    """Return ``(state, value)`` for one non-external HTML reference.

    ``value`` is package-relative for existing paths and the authored reference
    otherwise. Resolving before the containment check also prevents a symlink or
    ``..`` path from being counted as valid package support.
    """

    target = html.unescape(unquote(ref)).replace("\\", "/").split("?")[0].split("#")[0]
    if not target:
        return "empty", ref
    if target.startswith("/"):
        return "server", ref
    package_root_resolved = package_root.resolve()
    candidate = (page_path.parent / target).resolve()
    try:
        relative = candidate.relative_to(package_root_resolved).as_posix()
    except ValueError:
        return "unsafe", ref
    if candidate.exists() and candidate.is_file():
        return "existing", relative
    return "missing", ref


def attr(elem: ET.Element, name: str) -> str:
    """Attribute lookup tolerant of namespace prefixes."""
    for key, value in elem.attrib.items():
        if local_name(key) == name:
            return value
    return ""


HEADING_TAG = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)


PRACTICE_TYPE_LABELS = {
    0: "Sorting",
    1: "Sequencing",
    2: "Hotspot",
    3: "Quick quiz",
    4: "Quick quiz - legacy dropdown",
    5: "Quick quiz - single choice",
    6: "Quick quiz - multi-select",
    7: "Quick quiz - fill-in-the-blank",
}


def _clean_practice_text(value) -> str:
    if value is None:
        return ""
    return html_to_text(str(value))


def _practice_label_block(text: str, href: str = "") -> dict | None:
    text = clean(text)
    if not text:
        return None
    return {"kind": "label", "level": 0, "runs": [{"text": text, "href": href}]}


def _practice_text_block(text: str, href: str = "") -> dict | None:
    text = clean(text)
    if not text:
        return None
    return {"kind": "p", "level": 0, "runs": [{"text": text, "href": href}]}


def _practice_meta_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _practice_card_block(title: str, href: str, details: list[tuple[str, str]]) -> dict:
    meta = {
        _practice_meta_key(name): value
        for name, value in details
        if value
    }
    if details:
        meta["summary"] = "; ".join(f"{name}: {value}" for name, value in details if value)
    return {
        "kind": "practice",
        "level": 0,
        "runs": [{"text": f"Creator+ practice: {title}", "href": href}],
        "meta": meta,
    }


def _format_practice_number(value) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return clean(str(value))
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _practice_type_label(payload: dict) -> str:
    practice_type = payload.get("type")
    if practice_type is None:
        return "Quick quiz - legacy" if isinstance(payload.get("questions"), list) else "Creator+ practice"
    return PRACTICE_TYPE_LABELS.get(practice_type, f"Creator+ type {practice_type}")


def _resolve_creatorplus_package_path(
    ref: str,
    package_root: Path | None,
    html_dir: Path | None,
    creatorplus_report: CreatorPlusPackageReport | None = None,
) -> Path | None:
    ref = html.unescape(unquote(ref or "")).strip()
    if not ref:
        return None
    target = ref.replace("\\", "/").split("?")[0].split("#")[0].lstrip("/")
    if not target:
        return None
    if creatorplus_report is not None and package_root is not None and html_dir is not None:
        relationship = creatorplus_report.relationship_for(
            package_root=package_root,
            html_dir=html_dir,
            data_file=ref,
        )
        if relationship and relationship.selected_path:
            return package_root / relationship.selected_path
    root_resolved = package_root.resolve() if package_root is not None else None
    for base in (package_root, html_dir):
        if base is None:
            continue
        candidate = (base / target).resolve()
        if root_resolved is not None:
            try:
                candidate.relative_to(root_resolved)
            except ValueError:
                continue
        if candidate.exists():
            return candidate
    return None


def _looks_like_creator_practice(data_file: str, src: str) -> bool:
    data_file = (data_file or "").lower()
    src = (src or "").lower()
    return data_file.endswith(".practice.json") or "practices.lcs.brightspace.com" in src


def _practice_source_label(path: Path | None, data_file: str, package_root: Path | None) -> str:
    if data_file:
        return data_file.replace("\\", "/")
    if path and package_root:
        try:
            return path.relative_to(package_root).as_posix()
        except ValueError:
            pass
    return path.name if path else ""


def _creator_practice_blocks(
    *,
    data_file: str,
    src: str,
    package_root: Path | None,
    html_dir: Path | None,
    diagnostics: list[str] | None,
    creatorplus_report: CreatorPlusPackageReport | None = None,
) -> list[dict]:
    path = _resolve_creatorplus_package_path(
        data_file,
        package_root,
        html_dir,
        creatorplus_report,
    )
    if path is None:
        if diagnostics is not None and data_file:
            diagnostics.append(f"Creator+ practice iframe: metadata file not found: {data_file}")
        first = _practice_label_block("Creator+ practice: metadata not found", src)
        source = _practice_label_block(f"Source file: {data_file}") if data_file else None
        return [block for block in (first, source) if block]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if diagnostics is not None:
            diagnostics.append(f"Creator+ practice iframe: unreadable metadata file {path.name}: {exc}")
        first = _practice_label_block("Creator+ practice: metadata unreadable", src)
        source = _practice_label_block(f"Source file: {_practice_source_label(path, data_file, package_root)}")
        return [block for block in (first, source) if block]

    title = _clean_practice_text(payload.get("title")) or "Untitled Creator+ practice"
    details: list[tuple[str, str]] = []
    details.append(("Practice type", _practice_type_label(payload)))
    source_label = _practice_source_label(path, data_file, package_root)
    if source_label:
        details.append(("Source file", source_label))
    if payload.get("id") not in (None, ""):
        details.append(("Practice id", str(payload.get("id"))))

    questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    sortable_items = payload.get("sortableItems") if isinstance(payload.get("sortableItems"), list) else []
    categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    if questions:
        details.append(("Questions", str(len(questions))))
    if sortable_items:
        details.append(("Items", str(len(sortable_items))))
    if categories:
        details.append(("Categories/slots", str(len(categories))))
    if payload.get("hasScoring") is not None:
        if payload.get("hasScoring"):
            points = _format_practice_number(payload.get("scorePoints"))
            details.append(("Scoring", f"{points} pts" if points else "Enabled"))
        else:
            details.append(("Scoring", "Not scored"))

    blocks: list[dict] = [_practice_card_block(title, src, details)]

    seen_text: set[str] = set()
    for label, text in (
        ("Description", _clean_practice_text(payload.get("description"))),
        ("Instructions", _clean_practice_text(payload.get("customInstructions"))),
        ("Prompt", _clean_practice_text(payload.get("questionText"))),
    ):
        normalized = text.lower()
        if text and normalized not in seen_text:
            seen_text.add(normalized)
            label_block = _practice_label_block(label)
            text_block = _practice_text_block(text)
            if label_block:
                blocks.append(label_block)
            if text_block:
                blocks.append(text_block)

    if questions:
        first_question = questions[0] if isinstance(questions[0], dict) else {}
        prompt = _clean_practice_text(first_question.get("text") or first_question.get("questionText"))
        normalized = prompt.lower()
        if prompt and normalized not in seen_text:
            label_block = _practice_label_block("First prompt")
            text_block = _practice_text_block(prompt)
            if label_block:
                blocks.append(label_block)
            if text_block:
                blocks.append(text_block)

    return blocks


# Formatting-preserving HTML extraction --------------------------------------
# Rather than flattening pages to one line, we parse them into blocks so that
# paragraphs, bullet lists, links, and useful visual structure survive into the
# blueprint. Each block is {"kind": str, "level": int, "runs": [...]}, with
# optional lightweight "meta" for visual/embedded blocks. Visual blocks may also
# carry nested "blocks" so callout/card styling can wrap the full source section.
# List items from an <ol> carry meta {"list": "ordered"}; a <br> stays inside
# its block as a "\n" run so the line break survives without splitting the block.
# <title> is skipped here because it is document metadata, recorded separately
# as html_title; left in, its text would read as the page's first paragraph.
# Runs carry "bold", "italic", and "underline" (only when true) from <strong>,
# <b>, <em>, <i>, <u>, and inline font-weight / font-style / text-decoration
# styles. A <table> becomes one "table" block: rows of cells, each cell a list of
# runs with block content inside the cell flattened to "\n"-separated lines
# ("• " marks a list item); leading rows of <th> cells, or rows inside <thead>,
# are counted in header_rows. A <details> element is one "dropdown" block: its
# <summary> text is the block's runs (the title) and everything else inside it
# is the block's nested blocks, so the panel's extent survives. A page body is
# parsed once, whole, and only then grouped into segments (``segment_blocks``),
# so a heading inside a panel stays inside it.
_SKIP_TAGS = {"script", "style", "noscript", "title"}
_BLOCK_BREAK_TAGS = {"p", "div", "blockquote", "figure", "figcaption", "section", "article"}
# A heading inside a fragment is a "label" block carrying its source level, the
# same shape a page's own headings take after segmenting. Before this, h1-h4 were
# inline in the fragment reader, so a description that opened with headings ran
# their words into the first paragraph.
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_TABLE_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col"}
_EMPHASIS_TAGS = {"strong": "bold", "b": "bold", "em": "italic", "i": "italic", "u": "underline"}
_BOLD_WEIGHT_RE = re.compile(r"font-weight\s*:\s*(bold|bolder|[6-9]00)\b", re.IGNORECASE)
_ITALIC_STYLE_RE = re.compile(r"font-style\s*:\s*(italic|oblique)\b", re.IGNORECASE)
_UNDERLINE_STYLE_RE = re.compile(r"text-decoration(?:-line)?\s*:[^;]*\bunderline\b", re.IGNORECASE)
_LINE_BREAK = "\n"
_CELL_LINE_BREAK = {"text": _LINE_BREAK, "href": ""}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
              "meta", "param", "source", "track", "wbr"}
_ARTIFACT_TEXTS = {"basic page - no banner", "basic page", "no banner", "banner", "blank page"}
_LIVE_SCHEMES = ("http://", "https://", "mailto:")
_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "kaltura", "mediasite", "panopto")
_VISUAL_CLASS_LABELS = (
    ("d2l-callout-title", "Callout"),
    ("accordion", "Accordion"),
    ("project-callout", "Project callout"),
    ("assessment-callout", "Assessment callout"),
    ("discussion-note", "Discussion note"),
    ("roleplay-callout", "Roleplay callout"),
    ("survey-callout", "Survey callout"),
    ("side-quest", "Side quest"),
    ("next-step", "Next step"),
    ("case-card", "Case card"),
    ("info-card", "Information card"),
    ("timeline-card", "Timeline card"),
    ("timeline-item", "Timeline item"),
    ("timeline", "Timeline"),
    ("slide-card", "Slide card"),
    ("launch-card", "Launch card"),
    ("prototype-card", "Prototype card"),
    ("resources-section", "Resources section"),
    ("details-grid", "Details grid"),
    ("styled-box", "Styled section"),
    ("box", "Box"),
    ("callout", "Callout"),
    ("note", "Note"),
    ("panel", "Panel"),
)
_NOISY_VISUAL_LABELS = {"Timeline item", "Timeline card"}
_VISUAL_EXCLUDED_CLASSES = {
    "mceNonEditable",
    "placeholder",
    "d2l-practice",
    "practice-card",
    "practice-item",
    "practice-stage",
    "practice-header",
    "practice-meta",
    "practice-shell",
    "practice-cluster",
    "practice-wrapper",
    "practice-side-quest",
    "side-quest-content",
    "side-quest-practice-card",
    "side-quest-practice-stack",
    "video-wrap",
    "video-shell",
    "back-top",
}
_VISUAL_CONTAINER_TAGS = {"aside", "article", "blockquote", "details", "div", "figure", "section", "table"}
# Classes a page template uses for chrome around the content: skipped as not authored (2026-10-08).
_PAGE_CHROME_CLASSES = {"skip", "skip-link", "kicker", "eyebrow", "course-label"}
_TONE_CLASS_RE = re.compile(r"^(?:callout|box|panel)--([a-z][a-z0-9_-]*)$")


def _emphasis_from_style(style: str) -> set[str]:
    """Emphasis a tag's inline style adds: bold, italic, underline."""
    style = style or ""
    found: set[str] = set()
    if _BOLD_WEIGHT_RE.search(style):
        found.add("bold")
    if _ITALIC_STYLE_RE.search(style):
        found.add("italic")
    if _UNDERLINE_STYLE_RE.search(style):
        found.add("underline")
    return found


def _emphasis_for(tag: str, attr_map: dict) -> set[str]:
    found = _emphasis_from_style(attr_map.get("style", ""))
    if tag in _EMPHASIS_TAGS:
        found.add(_EMPHASIS_TAGS[tag])
    return found


def _run(text: str, href: str = "", emphasis: set[str] | None = None) -> dict:
    run = {"text": text, "href": href}
    for flag in ("bold", "italic", "underline"):
        if emphasis and flag in emphasis:
            run[flag] = True
    return run


def _run_flags(run: dict) -> tuple:
    return (run.get("href", ""), bool(run.get("bold")), bool(run.get("italic")), bool(run.get("underline")))


def _block_lines(block: dict) -> list[list[dict]]:
    """A block's text as lines of runs, for flattening content into a table cell.

    List items get a bullet marker, headings their text, and nested blocks (a
    callout inside a cell, a nested table) their own lines in order.
    """
    kind = block.get("kind")
    if kind == "table":
        lines: list[list[dict]] = []
        for row in block.get("rows") or []:
            parts: list[dict] = []
            for index, cell in enumerate(row):
                if index:
                    parts.append(_run(" | "))
                parts.extend(run for run in cell if run["text"] != _LINE_BREAK or parts)
            if parts:
                lines.append(parts)
        return lines
    runs = [run for run in block.get("runs") or [] if run.get("text")]
    lines = []
    if runs:
        if kind == "li":
            runs = [_run("• "), *runs]
        if kind == "divider":
            runs = []
    if runs:
        lines.append(runs)
    for child in block.get("blocks") or []:
        lines.extend(_block_lines(child))
    return lines


def _block(kind: str, text: str = "", href: str = "", *, meta: dict | None = None) -> dict:
    out = {"kind": kind, "level": 0, "runs": [{"text": text, "href": href}] if text or href else []}
    if meta:
        out["meta"] = {key: str(value) for key, value in meta.items() if value is not None and str(value)}
    return out


def _class_tokens(attr_map: dict) -> list[str]:
    classes = attr_map.get("class", "")
    return [token for token in re.split(r"\s+", classes.strip()) if token]


# -- inline style, read conservatively ------------------------------------------
# Brightspace editor fields (assignment and discussion descriptions) strip class
# attributes, so a callout an author writes there arrives as inline style only.
# A container with no recognised class is therefore classified by its style, but
# conservatively: decoration (a numbered badge, an inline chip, a float) is not a
# panel, a tint or a left edge is a callout, a plain border is a framed section,
# and white or absent styling is nothing. This is a markup heuristic and the
# block says so (``meta.inferred``); it is not a fact about the surface.
_CSS_SIDES = ("top", "right", "bottom", "left")
_CSS_NO_COLOR = {"transparent", "none", "initial", "inherit", "unset", "revert"}
_CSS_WHITE = {"white", "#fff", "#ffffff", "#ffffffff", "rgb(255,255,255)", "rgba(255,255,255,1)", "hsl(0,0%,100%)"}
_CSS_BORDER_STYLES = {"none", "hidden", "dotted", "dashed", "solid", "double", "groove", "ridge", "inset", "outset"}
_CSS_BORDER_WIDTH_WORDS = {"thin": 1.0, "medium": 3.0, "thick": 5.0}
_CSS_BACKGROUND_WORDS = {
    "none", "no-repeat", "repeat", "repeat-x", "repeat-y", "space", "round", "center", "top", "bottom", "left",
    "right", "cover", "contain", "auto", "fixed", "scroll", "local", "padding-box", "border-box", "content-box",
    "text", "initial", "inherit", "unset", "revert",
}
_CSS_LENGTH_RE = re.compile(r"^(-?\d*\.?\d+)(px|pt|rem|em|%|vw|vh|ch)?$")
_CSS_HEX_RE = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$")
_CSS_SMALL_FIXED = {"px": 48.0, "pt": 36.0, "rem": 3.0, "em": 3.0, "": 48.0}


def _css_declarations(style: str) -> list[tuple[str, str]]:
    """An inline style as (name, value) pairs in source order, lowercased, ``!important`` dropped."""
    out: list[tuple[str, str]] = []
    for part in re.split(r";(?![^(]*\))", style or ""):
        if ":" not in part:
            continue
        name, value = part.split(":", 1)
        name = name.strip().lower()
        value = " ".join(value.replace("!important", "").split()).lower()
        if name and value:
            out.append((name, value))
    return out


def _css_tokens(value: str) -> list[str]:
    """Space-separated tokens of a CSS value, keeping function calls such as rgb(…) whole."""
    return [match.group(0) for match in re.finditer(r"[a-z-]+\([^)]*\)|\S+", value)]


def _css_length_px(token: str) -> float | None:
    """A length token in CSS pixels (pt, rem and em converted; unitless read as px), else None."""
    match = _CSS_LENGTH_RE.match(token.strip())
    if not match or match.group(2) in {"%", "vw", "vh", "ch"}:
        return None
    number, unit = float(match.group(1)), match.group(2) or ""
    return number * {"": 1.0, "px": 1.0, "pt": 4 / 3, "rem": 16.0, "em": 16.0}[unit]


def _css_small_fixed(value: str) -> bool:
    """True for a fixed width or height of 3 rem / 48 px or less: a badge, an icon, a chip."""
    match = _CSS_LENGTH_RE.match((value or "").strip())
    if not match:
        return False
    unit = match.group(2) or ""
    if unit not in _CSS_SMALL_FIXED:
        return False
    return float(match.group(1)) <= _CSS_SMALL_FIXED[unit]


def _css_hex(token: str) -> str:
    """A colour token as six-digit lowercase hex, or "" when it is not a hex colour."""
    token = (token or "").strip().lower()
    if not _CSS_HEX_RE.match(token):
        return ""
    digits = token[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits[:6]


def _css_color_class(token: str) -> str:
    """One colour token as "none" (absent or transparent), "white", or "tint"."""
    token = (token or "").strip().lower().replace(" ", "")
    if not token or token in _CSS_NO_COLOR:
        return "none"
    if token in _CSS_WHITE:
        return "white"
    if token.startswith(("rgba(", "hsla(")):
        parts = token[5:-1].split(",")
        if len(parts) == 4 and parts[3] in {"0", "0.0", "0%", ".0"}:
            return "none"
        if token.startswith("rgba(") and [p.strip() for p in parts[:3]] == ["255", "255", "255"]:
            return "white"
    if token.startswith("#") and len(token) == 9 and token.endswith("00"):
        return "none"
    if _css_hex(token) == "#ffffff":
        return "white"
    return "tint"


def _background_class(declarations: list[tuple[str, str]]) -> str:
    """The container's effective background colour: "none", "white" or "tint"."""
    result = "none"
    for name, value in declarations:
        if name == "background-color":
            result = _css_color_class(value)
        elif name == "background":
            result = "none"
            for token in _css_tokens(value):
                if "gradient(" in token:
                    result = "tint"
                    break
                if token.startswith("url(") or token in _CSS_BACKGROUND_WORDS:
                    continue
                if token.startswith(("#", "rgb(", "rgba(", "hsl(", "hsla(")) or token.isalpha():
                    result = _css_color_class(token)
                    break
    return result


def _border_sides(declarations: list[tuple[str, str]]) -> dict[str, dict]:
    """Each side's border after the cascade: width in px, style, colour token."""
    sides = {side: {"width": 3.0, "style": "none", "color": "currentcolor"} for side in _CSS_SIDES}

    def shorthand(value: str) -> dict:
        parsed = {"width": 3.0, "style": "none", "color": "currentcolor"}
        for token in _css_tokens(value):
            if token in _CSS_BORDER_STYLES:
                parsed["style"] = token
            elif token in _CSS_BORDER_WIDTH_WORDS:
                parsed["width"] = _CSS_BORDER_WIDTH_WORDS[token]
            elif _css_length_px(token) is not None:
                parsed["width"] = _css_length_px(token)
            else:
                parsed["color"] = token
        return parsed

    def per_side(tokens: list[str]) -> dict[str, str]:
        if not tokens:
            return {}
        if len(tokens) == 1:
            top = right = bottom = left = tokens[0]
        elif len(tokens) == 2:
            top, right, bottom, left = tokens[0], tokens[1], tokens[0], tokens[1]
        elif len(tokens) == 3:
            top, right, bottom, left = tokens[0], tokens[1], tokens[2], tokens[1]
        else:
            top, right, bottom, left = tokens[:4]
        return dict(zip(_CSS_SIDES, (top, right, bottom, left)))

    def convert(prop: str, token: str):
        if prop == "width":
            if token in _CSS_BORDER_WIDTH_WORDS:
                return _CSS_BORDER_WIDTH_WORDS[token]
            length = _css_length_px(token)
            return 3.0 if length is None else length
        return token

    for name, value in declarations:
        if name == "border":
            parsed = shorthand(value)
            for side in sides.values():
                side.update(parsed)
        elif name in {"border-top", "border-right", "border-bottom", "border-left"}:
            sides[name[7:]].update(shorthand(value))
        elif name in {"border-width", "border-style", "border-color"}:
            prop = name[7:]
            for side, token in per_side(_css_tokens(value)).items():
                sides[side][prop] = convert(prop, token)
        else:
            match = re.fullmatch(r"border-(top|right|bottom|left)-(width|style|color)", name)
            if match:
                sides[match.group(1)][match.group(2)] = convert(match.group(2), value)
    return sides


def _side_visible(side: dict) -> bool:
    return side["style"] not in {"none", "hidden"} and side["width"] > 0 and _css_color_class(side["color"]) != "none"


def _declared_left_color(declarations: list[tuple[str, str]]) -> str:
    """The hex colour an author gave the left edge (``border-left`` or ``border-left-color``), else ""."""
    color = ""
    for name, value in declarations:
        if name == "border-left-color":
            color = _css_hex(value) or color
        elif name == "border-left":
            for token in _css_tokens(value):
                if _css_hex(token):
                    color = _css_hex(token)
    return color


def _inline_panel_facts(style: str) -> dict | None:
    """Classify a container with no recognised class by its inline style alone.

    Returns ``{"label": "Callout" | "Framed section", "color": "#rrggbb" | ""}``, or
    None when the styling is decoration (an absolutely positioned or inline
    element, a float, a circle, a fixed size of 3 rem / 48 px or less) or carries
    no panel (white or absent background and no visible border, or a rule on
    one edge alone). A tint or a left edge is a callout; a border with a white
    or absent background is a framed section.
    """
    declarations = _css_declarations(style)
    if not declarations:
        return None
    props = dict(declarations)
    if props.get("position") in {"absolute", "fixed"}:
        return None
    if props.get("display", "").startswith("inline"):
        return None
    if props.get("float") in {"left", "right"}:
        return None
    if "50%" in props.get("border-radius", ""):
        return None
    if any(_css_small_fixed(props.get(name, "")) for name in ("width", "height")):
        return None
    sides = _border_sides(declarations)
    visible = {name: _side_visible(side) for name, side in sides.items()}
    background = _background_class(declarations)
    left, others = sides["left"], [sides[name] for name in ("top", "right", "bottom")]
    left_edge = visible["left"] and (
        not all(visible[name] for name in ("top", "right", "bottom"))
        or left["width"] > max(other["width"] for other in others)
        or any(other["color"] != left["color"] for other in others)
    )
    if left_edge or background == "tint":
        return {"label": "Callout", "color": _declared_left_color(declarations) if left_edge else ""}
    if any(visible.values()):
        if sum(visible.values()) == 1 and (visible["top"] or visible["bottom"]):
            return None  # a rule along one edge is a divider, not a frame
        return {"label": "Framed section", "color": ""}
    return None


def _runs_text(block: dict) -> str:
    """A block's own runs as one cleaned line (line breaks read as spaces)."""
    return re.sub(r"\s+", " ", "".join(str(run.get("text", "")) for run in block.get("runs") or [])).strip()


def _decoration_only(children: list[dict]) -> bool:
    """True when a styled wrapper holds no block content beyond a short token (a badge's number)."""
    if not children:
        return True
    if len(children) != 1:
        return False
    child = children[0]
    if child.get("kind") != "p" or child.get("blocks"):
        return False
    text = _runs_text(child)
    return len(text) <= 12 and re.fullmatch(r"[^\s.!?,;:]+", text) is not None


def _matches_visual_class(key: str, token: str) -> bool:
    if key == "side-quest":
        return token in {"side-quest", "side-quest-panel", "side-quest-header"}
    if key == "callout":
        return token == "callout" or token.endswith("-callout")
    if key == "note":
        return token == "note" or token.endswith("-note")
    if key == "timeline":
        return token == "timeline"
    if key == "box":
        return token == "box"  # exact: "checkbox", "lightbox" and the like are not panels
    return key in token


def _visual_block_for(tag: str, attr_map: dict) -> dict | None:
    """The panel block a container tag opens, or None when it is not a panel.

    A recognised class decides first (the callout family, timeline, and the
    named components, which keep their name as the panel's label); only a
    container with no recognised class is read by its inline style, and then
    conservatively (``_inline_panel_facts``). Every panel keeps its tag, classes
    and source style in ``meta`` so the inference can be checked.
    """
    if tag not in _VISUAL_CONTAINER_TAGS:
        return None
    tokens = _class_tokens(attr_map)
    lowered = [token.lower() for token in tokens]
    style = attr_map.get("style", "") or ""
    for key, label in _VISUAL_CLASS_LABELS:
        if (
            any(_matches_visual_class(key, token) for token in lowered)
            and not any(token.lower() in _VISUAL_EXCLUDED_CLASSES for token in tokens)
        ):
            if label in _NOISY_VISUAL_LABELS:
                return None
            meta = {"tag": tag, "classes": " ".join(tokens), "source_style": style}
            # a tone or colour the page gave the panel, carried so the blueprint can say [callout tone=gold]
            for token in lowered:
                tone = _TONE_CLASS_RE.match(token)
                if tone:
                    meta["tone"] = tone.group(1)
                    break
            color = _declared_left_color(_css_declarations(style))
            if color and "tone" not in meta:
                meta["color"] = color
            return _block("visual", label, meta=meta)
    facts = _inline_panel_facts(style)
    if facts is None:
        return None
    meta = {"tag": tag, "classes": " ".join(tokens), "source_style": style, "inferred": "inline-style"}
    if facts["color"]:
        meta["color"] = facts["color"]
    return _block("visual", facts["label"], meta=meta)


def _embed_kind(src: str) -> str:
    src_lower = (src or "").lower()
    if any(host in src_lower for host in _VIDEO_HOSTS):
        return "Video embed"
    return "Embedded media"


def _safe_media_href(src: str) -> str:
    src = (src or "").strip()
    if not src or src.lower().startswith("data:"):
        return ""
    return src


def _image_placeholder_block(attr_map: dict) -> dict | None:
    src = (attr_map.get("src") or "").strip()
    alt = clean(attr_map.get("alt") or "")
    label = alt or "No alt text"
    href = _safe_media_href(src)
    meta = {"alt": alt, "src": href}
    if src.lower().startswith("data:"):
        meta["src"] = "inline data URI"
    if not alt:
        meta["missing_alt"] = "true"
    return _block("image", label, href, meta=meta)


class _BlockExtractor(HTMLParser):
    """Walk an HTML fragment and emit paragraph/list blocks with link-aware runs."""

    def __init__(
        self,
        *,
        package_root: Path | None = None,
        html_dir: Path | None = None,
        diagnostics: list[str] | None = None,
        creatorplus_report: CreatorPlusPackageReport | None = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.chrome_skipped = 0
        self.blocks: list[dict] = []
        self._runs: list[dict] = []
        self._kind = "p"
        self._heading_level = 0  # the open heading tag's level while one is open
        self._lists: list[str] = []  # open <ul>/<ol> tags, innermost last
        self._items: list[dict] = []  # open <li> elements, innermost last
        self._href: list[str] = []
        self._skip = 0
        self._skip_until: list[str] = []
        self._visual_stack: list[dict] = []
        self._emphasis: list[tuple[str, set[str]]] = []  # open tags and the emphasis each added
        self._tables: list[dict] = []  # open <table> elements, innermost last
        self.package_root = package_root
        self.html_dir = html_dir
        self.diagnostics = diagnostics
        self.creatorplus_report = creatorplus_report

    def _append_block(self, block: dict) -> None:
        cell = self._open_cell()
        if cell is not None and not self._visual_stack_inside_cell():
            self._absorb_into_cell(cell, block)
        elif self._visual_stack:
            self._visual_stack[-1]["blocks"].append(block)
        else:
            self.blocks.append(block)

    # -- emphasis -------------------------------------------------------------
    def _current_emphasis(self) -> set[str]:
        found: set[str] = set()
        for _tag, added in self._emphasis:
            found |= added
        return found

    def _open_emphasis(self, tag: str, attr_map: dict) -> None:
        if tag in _VOID_TAGS:
            return
        self._emphasis.append((tag, _emphasis_for(tag, attr_map)))

    def _close_emphasis(self, tag: str) -> None:
        for index in range(len(self._emphasis) - 1, -1, -1):
            if self._emphasis[index][0] == tag:
                del self._emphasis[index:]  # tags left open above it close with it
                return

    # -- tables ---------------------------------------------------------------
    def _open_cell(self) -> dict | None:
        if not self._tables:
            return None
        row = self._tables[-1]["row"]
        if row is None or row["cell"] is None:
            return None
        return row["cell"]

    def _visual_stack_inside_cell(self) -> bool:
        """True when the innermost visual group opened inside the current cell."""
        if not self._visual_stack or not self._tables:
            return False
        return self._visual_stack[-1].get("table_depth", 0) == len(self._tables)

    def _absorb_into_cell(self, cell: dict, block: dict) -> None:
        for line in _block_lines(block):
            if cell["runs"]:
                cell["runs"].append(dict(_CELL_LINE_BREAK))
            cell["runs"].extend(line)

    def _start_table(self, tag: str) -> None:
        self._flush()
        self._note_starttag(tag)
        self._tables.append({"rows": [], "row": None, "in_thead": False, "body_seen": False, "header_rows": 0})

    def _start_row(self) -> None:
        table = self._tables[-1]
        self._end_row()
        table["row"] = {"cells": [], "cell": None, "all_th": True}

    def _start_cell(self, tag: str) -> None:
        table = self._tables[-1]
        if table["row"] is None:
            self._start_row()
        self._end_cell()
        table["row"]["cell"] = {"runs": [], "th": tag == "th"}

    def _end_cell(self) -> None:
        table = self._tables[-1]
        row = table["row"]
        if row is None or row["cell"] is None:
            return
        self._flush()
        cell = row["cell"]
        runs = cell["runs"]
        while runs and runs[-1]["text"] == _LINE_BREAK:
            runs.pop()
        row["cells"].append(runs)
        if not cell["th"]:
            row["all_th"] = False
        row["cell"] = None

    def _end_row(self) -> None:
        table = self._tables[-1]
        row = table["row"]
        if row is None:
            return
        self._end_cell()
        if row["cells"]:
            is_header = table["in_thead"] or row["all_th"]
            if is_header and not table["body_seen"]:
                table["header_rows"] += 1
            else:
                table["body_seen"] = True
            table["rows"].append(row["cells"])
        table["row"] = None

    def _end_table(self) -> None:
        self._flush()
        self._end_row()
        table = self._tables.pop()
        rows = [row for row in table["rows"] if any(any(run["text"].strip() for run in cell) for cell in row)]
        if rows:
            width = max(len(row) for row in rows)
            for row in rows:
                row.extend([] for _ in range(width - len(row)))
            self._append_block({"kind": "table", "level": 0, "runs": [], "rows": rows,
                                "header_rows": min(table["header_rows"], len(rows))})

    def _extend_blocks(self, blocks: list[dict]) -> None:
        for block in blocks:
            self._append_block(block)

    def _note_starttag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        for group in self._visual_stack:
            group["depth"] += 1

    @staticmethod
    def _settle_group(group: dict) -> list[dict]:
        """Shape a finished container group into the block(s) it leaves behind.

        For a <details> group the first summary block becomes the dropdown's
        title runs; the rest of the children are the panel's content. A panel
        read from inline style alone that holds no block content beyond a short
        token (a numbered badge, an icon wrapper) is decoration: its children
        are returned in its place, so their text stays where it was.
        """
        block = group["block"]
        children = group["blocks"]
        if block.get("kind") == "dropdown":
            summaries = [child for child in children if child.get("kind") == "dropdown" and "blocks" not in child]
            if summaries:
                block["runs"] = summaries[0]["runs"]
                children = [child for child in children if child is not summaries[0]]
        if (block.get("meta") or {}).get("inferred") == "inline-style" and _decoration_only(children):
            return children
        if children:
            block["blocks"] = children
        return [block]

    def _finish_visual_groups(self) -> None:
        while self._visual_stack and self._visual_stack[-1]["depth"] <= 0:
            group = self._visual_stack.pop()
            self._extend_blocks(self._settle_group(group))

    def _note_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        for group in self._visual_stack:
            group["depth"] -= 1
        self._finish_visual_groups()

    def _close_visual_groups(self) -> None:
        while self._visual_stack:
            group = self._visual_stack.pop()
            self._extend_blocks(self._settle_group(group))

    def _close_items(self, depth: int) -> None:
        """Forget open list items at ``depth`` or deeper."""
        while self._items and self._items[-1]["depth"] >= depth:
            self._items.pop()

    def _pending_block(self, runs: list[dict]) -> dict:
        """Shape the block about to be emitted from the collected runs.

        The first text block inside a list item is the item itself, even when
        the page wraps it in <p> or <div>; any later block in the same item
        stays a paragraph. Items of an <ol> are marked as ordered.
        """
        kind, level, meta = self._kind, 0, None
        if kind == "label":
            level = self._heading_level
        item = self._items[-1] if self._items else None
        if kind == "p" and item is not None and not item["used"]:
            item["used"] = True
            kind, level = "li", item["level"]
            if item["ordered"]:
                meta = {"list": "ordered"}
        block = {"kind": kind, "level": level, "runs": runs}
        if meta:
            block["meta"] = meta
        return block

    def _flush(self) -> None:
        runs: list[dict] = []
        for run in self._runs:
            if run.get("line_break"):
                # No trailing space before a line break; leading breaks are dropped.
                while runs and runs[-1]["text"] != _LINE_BREAK:
                    kept = runs[-1]["text"].rstrip()
                    if kept:
                        runs[-1]["text"] = kept
                        break
                    runs.pop()
                if runs:
                    runs.append({"text": _LINE_BREAK, "href": ""})
                continue
            text = re.sub(r"\s+", " ", run["text"])
            if not runs or runs[-1]["text"] == _LINE_BREAK:
                text = text.lstrip()  # no leading space at the start of a line
                if not text:
                    continue
            shaped = _run(text, run["href"], run.get("emphasis"))
            if runs and runs[-1]["text"] != _LINE_BREAK and _run_flags(runs[-1]) == _run_flags(shaped):
                runs[-1]["text"] += text  # one run per stretch of identical formatting
            else:
                runs.append(shaped)
        while runs and not runs[-1]["text"].strip():
            runs.pop()  # trailing whitespace-only runs and trailing line breaks
        if runs:
            runs[-1]["text"] = runs[-1]["text"].rstrip()
            combined = "".join(r["text"] for r in runs).strip().lower()
            if combined and combined not in _ARTIFACT_TEXTS:
                self._append_block(self._pending_block(runs))
        self._runs = []
        self._kind = "p"
        self._heading_level = 0

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        tokens = _class_tokens(attr_map)
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if any(token.lower() in {"sr-only", "visually-hidden"} for token in tokens):
            self._flush()
            self._skip += 1
            self._skip_until.append(tag)
            return
        if any(token.lower() in _PAGE_CHROME_CLASSES for token in tokens):
            # page chrome a template writes around the content (a skip link, the course label
            # above the title): not authored text, so it is left out of the blueprint
            self._flush()
            self._skip += 1
            self._skip_until.append(tag)
            self.chrome_skipped += 1
            return
        self._open_emphasis(tag, attr_map)
        if tag in _TABLE_TAGS and (tag == "table" or self._tables):
            if tag == "table":
                self._start_table(tag)
                return
            self._note_starttag(tag)
            if tag == "thead":
                self._flush()
                self._tables[-1]["in_thead"] = True
            elif tag == "tr":
                self._flush()
                self._start_row()
            elif tag in ("td", "th"):
                self._flush()
                self._start_cell(tag)
            elif tag in ("tbody", "tfoot", "caption"):
                self._flush()
            return
        if tag == "details":
            # The whole element is the dropdown; its summary becomes the title.
            self._flush()
            self._note_starttag(tag)
            self._visual_stack.append({"tag": tag, "depth": 1, "block": _block("dropdown"), "blocks": [],
                                       "table_depth": len(self._tables)})
            return
        visual = _visual_block_for(tag, attr_map)
        if visual:
            self._flush()
            self._note_starttag(tag)
            self._visual_stack.append({"tag": tag, "depth": 1, "block": visual, "blocks": [],
                                       "table_depth": len(self._tables)})
            return
        if tag == "hr":
            self._flush()
            self._append_block(_block("divider"))
            return
        if tag == "br":
            self._runs.append({"text": _LINE_BREAK, "href": "", "line_break": True})
            return
        if tag == "iframe":
            src = attr_map.get("src", "")
            if src:
                self._flush()
                data_file = attr_map.get("data-file", "").strip()
                if _looks_like_creator_practice(data_file, src):
                    self._extend_blocks(
                        _creator_practice_blocks(
                            data_file=data_file,
                            src=src,
                            package_root=self.package_root,
                            html_dir=self.html_dir,
                            diagnostics=self.diagnostics,
                            creatorplus_report=self.creatorplus_report,
                        )
                    )
                    return
                title = attr_map.get("title", "").strip() or "Embedded media"
                kind = _embed_kind(src)
                self._append_block(_block("embed", title, src, meta={"embed_type": kind}))
            self._skip += 1
            self._skip_until.append(tag)
            return
        if tag == "summary":
            self._flush()
            self._note_starttag(tag)
            self._kind = "dropdown"
            return
        if tag == "img":
            image_block = _image_placeholder_block(attr_map)
            if image_block:
                self._flush()
                self._append_block(image_block)
            return
        self._note_starttag(tag)
        if tag in _HEADING_TAGS:
            self._flush()
            self._kind = "label"
            self._heading_level = _HEADING_TAGS[tag]
            return
        if tag in ("ul", "ol"):
            self._lists.append(tag)
            return
        if tag == "li":
            self._flush()
            depth = len(self._lists)
            # An <li> left unclosed ends where the next item of its list starts.
            self._close_items(depth)
            self._items.append(
                {
                    "depth": depth,
                    "level": max(1, depth),
                    "ordered": bool(self._lists) and self._lists[-1] == "ol",
                    "used": False,
                }
            )
            return
        if tag in _BLOCK_BREAK_TAGS:
            self._flush()
            return
        if tag == "a":
            self._href.append(attr_map.get("href", ""))

    def handle_endtag(self, tag):
        if self._skip_until and tag == self._skip_until[-1]:
            self._skip_until.pop()
            if self._skip:
                self._skip -= 1
            return
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        self._close_emphasis(tag)
        if tag in _TABLE_TAGS and self._tables:
            if tag == "table":
                self._end_table()
            elif tag == "tr":
                self._end_row()
            elif tag in ("td", "th"):
                self._end_cell()
            elif tag == "thead":
                self._flush()
                self._end_row()
                self._tables[-1]["in_thead"] = False
            else:
                self._flush()
            self._note_endtag(tag)
            return
        if tag == "summary":
            self._flush()
            self._note_endtag(tag)
            return
        if tag in ("ul", "ol"):
            self._flush()
            self._close_items(len(self._lists))
            if self._lists:
                self._lists.pop()
            self._note_endtag(tag)
            return
        if tag in _HEADING_TAGS or tag in ("p", "div", "li", "blockquote", "figure",
                                           "figcaption", "section", "article"):
            self._flush()
            if tag == "li":
                self._close_items(len(self._lists))
            self._note_endtag(tag)
            return
        if tag == "a" and self._href:
            self._href.pop()
        self._note_endtag(tag)

    def handle_data(self, data):
        if self._skip or not data:
            return
        self._runs.append({"text": data, "href": self._href[-1] if self._href else "",
                           "emphasis": self._current_emphasis()})

    def close(self):
        super().close()
        self._flush()
        while self._tables:
            self._end_table()
        self._close_visual_groups()


def html_fragment_to_blocks(
    raw: str,
    *,
    package_root: Path | None = None,
    html_dir: Path | None = None,
    diagnostics: list[str] | None = None,
    creatorplus_report: CreatorPlusPackageReport | None = None,
) -> list[dict]:
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    parser = _BlockExtractor(
        package_root=package_root,
        html_dir=html_dir,
        diagnostics=diagnostics,
        creatorplus_report=creatorplus_report,
    )
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # pragma: no cover - malformed HTML fallback
        text = _flatten_text(raw)
        return [{"kind": "p", "level": 0, "runs": [{"text": text, "href": ""}]}] if text else []
    if parser.chrome_skipped and diagnostics is not None:
        diagnostics.append(f"Page chrome: {parser.chrome_skipped} element(s) with a skip-link or course-label class left out of the blueprint")
    return parser.blocks


def iter_block_runs(block: dict):
    """The runs of one block, including the cells of a table block."""
    yield from block.get("runs", []) or []
    for row in block.get("rows") or []:
        for cell in row:
            yield from cell


def blocks_to_text(blocks: list[dict]) -> str:
    parts: list[str] = []
    for block in blocks:
        # Line-break runs separate words here just as the joining space does.
        parts.extend(run["text"] for run in iter_block_runs(block) if run["text"] != _LINE_BREAK)
        if block.get("blocks"):
            parts.append(blocks_to_text(block["blocks"]))
    return clean(" ".join(parts))


def _content_file_display_name(href: str, title: str) -> str:
    target = _href_path_part(href)
    file_name = clean(Path(target).name) if target else ""
    return file_name or clean(title) or clean(href)


def _content_file_topic(node: dict, *, reason: str) -> dict:
    href = node.get("href", "")
    title = clean(node.get("title", ""))
    file_name = _content_file_display_name(href, title)
    text = file_name or title or clean(href) or "course file"
    blocks = [_block("file", text, href, meta={"file_name": file_name, "extraction": reason})]
    return {
        "manifest_title": title,
        "html_title": "",
        "href": href,
        "body_text": blocks_to_text(blocks),
        "body_segments": [{"heading": "", "level": 0, "blocks": blocks, "text": blocks_to_text(blocks)}],
        "empty_page": False,
        "missing_refs": [],
        "server_refs": [],
        "images_missing_alt": 0,
        "images_missing_alt_refs": [],
        "skipped_body_extraction": True,
        "extraction_note": reason,
    }


def _hidden_item_type_label(node: dict) -> str:
    kind = node.get("kind", "")
    href = node.get("href", "")
    if kind == "html_topic":
        return "HTML content page"
    if kind == CONTENT_FILE_KIND:
        suffix = _href_suffix(href).lstrip(".").upper()
        return f"{suffix} course file" if suffix else "course file"
    return kind.replace("_", " ") or "manifest item"


def _hidden_notice_block(node: dict, action: str) -> dict:
    href = node.get("href", "")
    title = clean(node.get("title", ""))
    item_type = _hidden_item_type_label(node)
    object_name = title or clean(href) or "hidden manifest item"
    text = (
        f"Object: {object_name}; type: {item_type}; hidden/faculty-facing in the "
        f"Brightspace manifest. {action}"
    )
    return _block(
        "hidden",
        text,
        href,
        meta={
            "object_name": object_name,
            "item_type": item_type,
            "manifest_kind": node.get("kind", ""),
            "extraction": "hidden/faculty-facing manifest item",
        },
    )


def _prepend_hidden_notice(node: dict, segments: list[dict], action: str) -> list[dict]:
    notice = _hidden_notice_block(node, action)
    if not segments:
        return [{"heading": "", "level": 0, "blocks": [notice], "text": blocks_to_text([notice])}]
    first = dict(segments[0])
    first_blocks = [notice, *first.get("blocks", [])]
    first["blocks"] = first_blocks
    first["text"] = blocks_to_text(first_blocks)
    return [first, *segments[1:]]


def _hidden_manifest_topic(
    node: dict,
    *,
    body_segments: list[dict] | None = None,
    action: str = "Body extraction was skipped; preserve this as a review note.",
    skipped_body_extraction: bool = True,
    extraction_note: str = "hidden/faculty-facing manifest item",
) -> dict:
    href = node.get("href", "")
    title = clean(node.get("title", ""))
    segments = _prepend_hidden_notice(node, body_segments or [], action)
    blocks = [block for segment in segments for block in segment.get("blocks", [])]
    body_text = blocks_to_text(blocks)
    return {
        "manifest_title": title,
        "html_title": "",
        "href": href,
        "body_text": body_text,
        "body_segments": segments,
        "empty_page": False,
        "missing_refs": [],
        "server_refs": [],
        "images_missing_alt": 0,
        "images_missing_alt_refs": [],
        "skipped_body_extraction": skipped_body_extraction,
        "hidden_manifest_item": True,
        "extraction_note": extraction_note,
    }


def _hidden_description_segments(node: dict) -> list[dict]:
    description = node.get("description", "")
    if not description:
        return []
    blocks = html_fragment_to_blocks(description)
    return [{"heading": "", "level": 0, "blocks": blocks, "text": blocks_to_text(blocks)}] if blocks else []


def panel_owned_heading(block: dict) -> dict | None:
    """The heading a panel owns: its first content child, when that child is a heading.

    A ``visual`` or ``dropdown`` block owns a heading when the first block inside
    it is a ``label`` with a source heading level (no prose, image or nested
    panel before it). Headings owned by a nested panel do not count for the
    outer one. Returns the label block, or None.
    """
    if block.get("kind") not in {"visual", "dropdown"}:
        return None
    children = block.get("blocks") or []
    if not children:
        return None
    first = children[0]
    if first.get("kind") == "label" and int(first.get("level") or 0) > 0 and _runs_text(first):
        return first
    return None


def _label_key(text: str) -> str:
    return clean(str(text or "")).rstrip(":").strip().casefold()


def panel_owns_label(blocks: list[dict], label: str) -> bool:
    """True when ``blocks`` opens with a panel whose owned heading reads as ``label``.

    This is the display dedupe rule every writer shares: a consumer that prints
    an outer label and then the blocks suppresses the label when the first block
    is a panel that carries the same heading inside it. Labels stay in the data
    for routing and provenance; only the second printing is suppressed.
    """
    if not blocks or not label:
        return False
    owned = panel_owned_heading(blocks[0])
    return owned is not None and _label_key(_runs_text(owned)) == _label_key(label)


def segment_blocks(blocks: list[dict]) -> list[dict]:
    """Group a page's top-level blocks into ``{heading, level, blocks, text}`` segments.

    The heading-ownership invariant: a top-level heading starts a segment, and
    so does a top-level panel whose first content child is a heading it owns.
    That panel stays whole, heading inside, as its segment's first block, and
    the segment carries the additive marker ``heading_owner: "panel"``. A panel
    with leading prose, or without an owned heading, joins the current segment,
    as does prose after a panel; so does a panel whose owned heading is a
    sub-heading of a content heading (h2 or below) that has nothing of its own
    yet (the heading above governs it). Later headings inside a panel never
    split it.
    Content before the first heading is an untitled intro segment
    (``heading == ""``, ``level == 0``).
    """
    segments: list[dict] = []
    current: dict | None = None
    for block in blocks:
        if block.get("kind") == "label" and int(block.get("level") or 0) > 0 and _runs_text(block):
            current = {"heading": _runs_text(block), "level": int(block["level"]), "blocks": []}
            segments.append(current)
            continue
        owned = panel_owned_heading(block)
        if owned is not None:
            level = int(owned["level"])
            if (
                current is not None and current["heading"] and not current["blocks"]
                and 2 <= int(current["level"]) < level
            ):
                # A content heading with nothing of its own yet, followed by a panel
                # that opens with a lower-level heading: the heading above governs the
                # panel (<h2>Assignment</h2><div class="callout"><h3>Deliverable</h3>…).
                # Starting a new segment there would leave the h2 empty and unwritten.
                # The page's own h1 is not such a heading: the sections under it are
                # siblings, and a panel-owned h2 right after it starts its own segment.
                current["blocks"].append(block)
                continue
            current = {
                "heading": _runs_text(owned),
                "level": level,
                "blocks": [block],
                "heading_owner": "panel",
            }
            segments.append(current)
            continue
        if current is None:
            current = {"heading": "", "level": 0, "blocks": []}
            segments.append(current)
        current["blocks"].append(block)
    output: list[dict] = []
    for segment in segments:
        if not segment["blocks"] and not segment["heading"]:
            continue
        segment["text"] = blocks_to_text(segment["blocks"])
        output.append(segment)
    return output


def html_to_segments(
    raw: str,
    *,
    package_root: Path | None = None,
    html_dir: Path | None = None,
    diagnostics: list[str] | None = None,
    creatorplus_report: CreatorPlusPackageReport | None = None,
) -> list[dict]:
    """Split an HTML body into ``{heading, level, blocks, text}`` chunks by heading ownership.

    The body is parsed once by the block reader, so segmentation sees the same
    container classification as every fragment, then grouped by
    ``segment_blocks``: headings h1-h6 start segments and carry their level; a
    panel that opens with a heading it owns starts a segment and stays whole
    (``heading_owner: "panel"``). Each chunk keeps its formatting as a list of
    blocks; ``text`` is a flattened convenience copy. This mirrors the page's
    authored structure rather than forcing a fixed taxonomy; downstream code
    decides which headings map to blueprint rows.
    """
    blocks = html_fragment_to_blocks(
        raw,
        package_root=package_root,
        html_dir=html_dir,
        diagnostics=diagnostics,
        creatorplus_report=creatorplus_report,
    )
    return segment_blocks(blocks)


# --------------------------------------------------------------------------- #
# Linked syllabus discovery and best-effort supplementary evidence
# --------------------------------------------------------------------------- #


def _iter_block_runs(blocks: list[dict]):
    for block in blocks:
        yield from iter_block_runs(block)
        yield from _iter_block_runs(block.get("blocks", []) or [])


def discover_syllabus_references(
    nodes: list[dict], html_topics: list[dict] | None = None
) -> list[dict]:
    """Return export-linked syllabus references with their course placement.

    Discovery is always local and deterministic. Fetching is a separate,
    explicitly bounded best-effort step so a network or page-shape failure can
    never erase the export-derived deliverable. References may be direct
    manifest links or external syllabus anchors inside package-local HTML.
    """
    references: list[dict] = []
    seen: set[tuple[str, str]] = set()
    placements: dict[str, dict] = {}

    def visit(items: list[dict], ancestors: tuple[str, ...]) -> None:
        for node in items:
            title = clean(node.get("title", ""))
            href = html.unescape(clean(node.get("href", "")))
            path = tuple(part for part in (*ancestors, title) if part)
            if _is_local_href(href):
                placements.setdefault(
                    _href_path_part(href),
                    {
                        "manifest_item_identifier": node.get("identifier", ""),
                        "manifest_resource_identifier": node.get("identifierref", ""),
                        "manifest_path": " > ".join(path),
                        "is_hidden": bool(node.get("is_hidden")),
                    },
                )
            if href.startswith(("http://", "https://")) and (
                "syllab" in title.casefold() or "syllab" in href.casefold()
            ):
                key = (href, node.get("identifier", "") or title)
                if key not in seen:
                    seen.add(key)
                    references.append(
                        {
                            "title": title or "Syllabus",
                            "url": href,
                            "manifest_item_identifier": node.get("identifier", ""),
                            "manifest_resource_identifier": node.get("identifierref", ""),
                            "manifest_path": " > ".join(path),
                            "is_hidden": bool(node.get("is_hidden")),
                            "discovery": "manifest_item_link",
                            "evidence_role": "supplemental_linked_syllabus",
                        }
                    )
            visit(node.get("children", []), path)

    visit(nodes, ())
    direct_urls = {reference["url"] for reference in references}
    nested_urls: set[str] = set()
    for topic in html_topics or []:
        topic_href = clean(topic.get("href", ""))
        placement = placements.get(_href_path_part(topic_href), {})
        for segment in topic.get("body_segments", []) or []:
            for run in _iter_block_runs(segment.get("blocks", []) or []):
                url = html.unescape(clean(run.get("href", "")))
                title = clean(run.get("text", ""))
                if not url.startswith(("http://", "https://")):
                    continue
                if "syllab" not in title.casefold() and "syllab" not in url.casefold():
                    continue
                if url in direct_urls or url in nested_urls:
                    continue
                nested_urls.add(url)
                container_path = placement.get("manifest_path") or clean(
                    topic.get("manifest_title", "")
                )
                references.append(
                    {
                        "title": title or "Syllabus",
                        "url": url,
                        "manifest_item_identifier": placement.get(
                            "manifest_item_identifier", ""
                        ),
                        "manifest_resource_identifier": placement.get(
                            "manifest_resource_identifier", ""
                        ),
                        "manifest_path": " > ".join(
                            part for part in (container_path, title) if part
                        ),
                        "is_hidden": bool(
                            placement.get("is_hidden")
                            or topic.get("hidden_manifest_item")
                        ),
                        "discovery": "package_html_link",
                        "container_href": topic_href,
                        "evidence_role": "supplemental_linked_syllabus",
                    }
                )
    return references


def _allowed_syllabus_host(url: str, allowed_hosts: set[str]) -> bool:
    hostname = (urlparse(url).hostname or "").casefold().rstrip(".")
    return any(
        hostname == allowed or hostname.endswith(f".{allowed}")
        for allowed in allowed_hosts
    )


def _response_charset(content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type or "", flags=re.IGNORECASE)
    return match.group(1).strip("\"'") if match else "utf-8"


def _syllabus_heading_score(field: str, heading: str, ancestors: tuple[str, ...]) -> int:
    value = re.sub(r"\s+", " ", clean(heading)).strip(" :").casefold()
    parent_text = " | ".join(part.casefold() for part in ancestors)
    if not value:
        return -1

    if field == "course_description":
        if value == "course description":
            return 130
        if value == "description":
            return 120
        if "course description" in value:
            return 110
        return 50 if value.endswith("description") else -1

    if field == "course_learning_outcomes":
        if "program" in value and "course" not in value:
            return -1
        if value in {"course learning outcomes", "course outcomes", "student learning outcomes"}:
            return 140
        if "course" in value and ("outcome" in value or "objective" in value):
            return 130
        if value == "learning objectives and outcomes":
            return 115
        if "learning outcome" in value or "student learning outcome" in value:
            return 105
        if "objective" in value and "course" in parent_text:
            return 90
        return -1

    if field == "required_materials":
        if value in {"required materials", "required course materials", "required texts", "textbooks"}:
            return 140
        if value in {"required", "required readings", "required textbooks"} and any(
            term in parent_text for term in ("material", "text", "reading", "resource")
        ):
            return 135
        if "required" in value and any(
            term in value for term in ("material", "text", "reading", "resource")
        ):
            return 130
        if value in {"materials", "course materials"}:
            return 90
        if "textbook" in value:
            return 100
        return -1

    return -1


def _segment_ancestors(segments: list[dict]) -> list[tuple[str, ...]]:
    stack: list[tuple[int, str]] = []
    output: list[tuple[str, ...]] = []
    for segment in segments:
        level = int(segment.get("level") or 0)
        heading = clean(segment.get("heading", ""))
        while stack and (level <= 0 or stack[-1][0] >= level):
            stack.pop()
        output.append(tuple(value for _depth, value in stack))
        if heading and level > 0:
            stack.append((level, heading))
    return output


def _section_blocks(segments: list[dict], index: int, own_blocks: list[dict] | None = None) -> list[dict]:
    """The blocks a segment heading governs: its own, then following lower-level segments with their labels."""
    target = segments[index]
    target_level = int(target.get("level") or 0)
    blocks = list(target.get("blocks") or []) if own_blocks is None else list(own_blocks)
    for following in segments[index + 1 :]:
        level = int(following.get("level") or 0)
        heading = clean(following.get("heading", ""))
        if heading and target_level > 0 and level <= target_level:
            break
        child_blocks = list(following.get("blocks") or [])
        if not child_blocks:
            continue
        if heading and following.get("heading_owner") != "panel":
            blocks.append(
                {
                    "kind": "label",
                    "level": 0,
                    "runs": [{"text": f"{heading}:", "href": ""}],
                }
            )
        blocks.extend(child_blocks)
    return blocks


def _container_section(children: list[dict], index: int) -> list[dict]:
    """The blocks after the heading at ``index`` in one container, up to the next heading of equal or higher rank there."""
    level = int(children[index].get("level") or 0)
    section: list[dict] = []
    for child in children[index + 1 :]:
        if child.get("kind") == "label" and 0 < int(child.get("level") or 0) <= level and _runs_text(child):
            break
        section.append(child)
    return section


def _index_nested_headings(blocks: list[dict], ancestors: tuple[str, ...], entries: list[dict], skip: dict | None) -> None:
    """Add every heading inside the panels of ``blocks`` to ``entries``, with its ancestors and section."""
    for block in blocks:
        if block.get("kind") not in {"visual", "dropdown"}:
            continue
        children = block.get("blocks") or []
        stack: list[tuple[int, str]] = []
        for index, child in enumerate(children):
            if child.get("kind") == "label" and int(child.get("level") or 0) > 0 and _runs_text(child):
                level, heading = int(child["level"]), _runs_text(child)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if not (skip is not None and child is skip):
                    entries.append({
                        "heading": heading,
                        "level": level,
                        "ancestors": ancestors + tuple(value for _depth, value in stack),
                        "blocks": _container_section(children, index),
                    })
                stack.append((level, heading))
            elif child.get("kind") in {"visual", "dropdown"}:
                _index_nested_headings([child], ancestors + tuple(value for _depth, value in stack), entries, None)


def _heading_index(segments: list[dict]) -> list[dict]:
    """Every heading in document order, including headings inside panels.

    Each entry is ``{heading, level, ancestors, blocks}``: a segment heading
    governs its ``_section_blocks``; a heading inside a panel governs the
    blocks that follow it in the same container up to the next heading of
    equal or higher rank there. A panel-owned segment heading is listed once,
    with the panel's own section plus what follows the panel. Three syllabus
    headings in one callout therefore yield three entries.
    """
    ancestors_by_index = _segment_ancestors(segments)
    entries: list[dict] = []
    for index, segment in enumerate(segments):
        heading = clean(segment.get("heading", ""))
        level = int(segment.get("level") or 0)
        blocks = list(segment.get("blocks") or [])
        owned = panel_owned_heading(blocks[0]) if segment.get("heading_owner") == "panel" and blocks else None
        if heading:
            if owned is not None:
                own = _container_section(blocks[0].get("blocks") or [], 0) + blocks[1:]
                entries.append({"heading": heading, "level": level, "ancestors": ancestors_by_index[index],
                                "blocks": _section_blocks(segments, index, own)})
            else:
                entries.append({"heading": heading, "level": level, "ancestors": ancestors_by_index[index],
                                "blocks": _section_blocks(segments, index)})
        inner_ancestors = ancestors_by_index[index] + ((heading,) if heading else ())
        _index_nested_headings(blocks, inner_ancestors, entries, owned)
    return entries


def _annotate_syllabus_blocks(blocks: list[dict], source: dict[str, str]) -> list[dict]:
    annotated: list[dict] = []
    for original in blocks:
        block = dict(original)
        block["runs"] = [dict(run) for run in original.get("runs", [])]
        meta = {str(key): str(value) for key, value in (original.get("meta") or {}).items()}
        meta.update(source)
        block["meta"] = meta
        if original.get("blocks") is not None:
            block["blocks"] = _annotate_syllabus_blocks(original.get("blocks") or [], source)
        annotated.append(block)
    return annotated


def extract_syllabus_front_matter(
    segments: list[dict],
    *,
    source_url: str,
    source_sha256: str,
    source_artifact: str,
) -> dict[str, dict]:
    """Extract verbatim supplemental fields from recognizable syllabus headings.

    Headings are found through ``_heading_index``, so a field whose heading sits
    inside a callout or another panel is found as readily as a top-level one.
    """
    index = _heading_index(segments)
    fields: dict[str, dict] = {}
    for field in ("course_description", "course_learning_outcomes", "required_materials"):
        candidates: list[tuple[int, int, dict]] = []
        for order, entry in enumerate(index):
            score = _syllabus_heading_score(field, entry["heading"], entry["ancestors"])
            if score < 0:
                continue
            if blocks_to_text(entry["blocks"]):
                candidates.append((score, order, entry))
        if not candidates:
            continue
        score, _order, entry = max(candidates, key=lambda item: (item[0], -item[1]))
        heading = entry["heading"]
        source = {
            "source_role": "supplemental_linked_syllabus",
            "source_url": source_url,
            "source_sha256": source_sha256,
            "source_artifact": source_artifact,
            "source_heading": heading,
            "extraction": "linked_syllabus_heading_section",
        }
        annotated = _annotate_syllabus_blocks(entry["blocks"], source)
        fields[field] = {
            "heading": heading,
            "heading_level": int(entry["level"]),
            "match_score": score,
            "blocks": annotated,
            "text": blocks_to_text(annotated),
            "extensions": {},
        }
    return fields


def extract_syllabus_title_candidates(segments: list[dict]) -> list[dict]:
    """Retain explicit course-coded headings, never a guessed document title.

    Session suffixes are retained as evidence but excluded from the course title;
    they do not establish a term or export date for a template.
    """
    candidates = []
    for entry in _heading_index(segments):
        heading = entry["heading"]
        # Title evidence belongs to the syllabus header. A later heading such
        # as "ABC 123 Course Project" is an activity label, not another title.
        if any(_syllabus_heading_score(field, heading, ()) >= 0 for field in (
            "course_description", "course_learning_outcomes", "required_materials"
        )):
            break
        match = re.fullmatch(r"([A-Z]{2,8})[ -]+(\d{3,4}[A-Z]?)\s*[:–—-]?\s+(.+)", heading)
        if not match:
            continue
        title = match.group(3).strip()
        session = re.search(
            r"(?:\s*[,–—-]\s*|\s+)(?:Spring|Summer|Fall|Autumn|Winter)(?:\s+[AB12])?\s*,?\s+(?:19|20)\d{2}$",
            title, flags=re.IGNORECASE,
        )
        suffix = title[session.start():] if session else ""
        if session:
            title = title[:session.start()].strip()
        if title:
            candidates.append({
                "course_code": f"{match.group(1)} {match.group(2)}",
                "title": title,
                "source_heading": heading,
                "excluded_session_suffix": suffix,
                "extraction": "course_coded_syllabus_header",
            })
    return candidates


def collect_syllabus_supplements(
    nodes: list[dict],
    *,
    html_topics: list[dict] | None = None,
    output_dir: Path,
    stem: str,
    fetch_enabled: bool,
    timeout: float,
    allowed_hosts: set[str],
    diagnostics: list[str],
) -> list[dict]:
    references = discover_syllabus_references(nodes, html_topics)
    if not references:
        return []

    source_dir = output_dir / f"{stem}__syllabus_sources"
    output: list[dict] = []
    for index, reference in enumerate(references, start=1):
        record = {
            **reference,
            "fetch_status": "inventory_only",
            "http_status": None,
            "content_type": "",
            "bytes": 0,
            "sha256": "",
            "fetched_at": None,
            "artifact_path": "",
            "front_matter": {},
            "course_title_candidates": [],
            "diagnostics": [],
            "extensions": {},
        }
        url = reference["url"]
        if reference.get("is_hidden"):
            record["fetch_status"] = "skipped_hidden"
            record["diagnostics"].append("Hidden manifest syllabus link was inventoried but not fetched.")
        elif not fetch_enabled:
            record["diagnostics"].append("Fetch disabled; manifest syllabus link was inventoried only.")
        elif not _allowed_syllabus_host(url, allowed_hosts):
            record["fetch_status"] = "skipped_unapproved_host"
            record["diagnostics"].append(
                f"Host {urlparse(url).hostname or '(missing)'} is not in the syllabus fetch allow-list."
            )
        else:
            request = Request(
                url,
                headers={
                    "User-Agent": "CourseCraft linked-syllabus supplement/1",
                    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.2",
                },
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    data = response.read(MAX_SYLLABUS_BYTES + 1)
                    if len(data) > MAX_SYLLABUS_BYTES:
                        raise ValueError(
                            f"linked syllabus exceeded the {MAX_SYLLABUS_BYTES}-byte safety limit"
                        )
                    content_type = response.headers.get("Content-Type", "")
                    suffix = ".pdf" if "pdf" in content_type.casefold() else ".html"
                    digest = hashlib.sha256(data).hexdigest()
                    url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
                    filename = f"{index:02d}_{safe_label(reference['title'])}_{url_key}{suffix}"
                    artifact = source_dir / filename
                    source_dir.mkdir(parents=True, exist_ok=True)
                    artifact.write_bytes(data)
                    artifact_relative = artifact.relative_to(output_dir).as_posix()
                    record.update(
                        {
                            "fetch_status": "fetched",
                            "http_status": getattr(response, "status", None),
                            "content_type": content_type,
                            "bytes": len(data),
                            "sha256": digest,
                            "fetched_at": datetime.now(timezone.utc)
                            .replace(microsecond=0)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "artifact_path": artifact_relative,
                        }
                    )
                    looks_html = "html" in content_type.casefold() or data.lstrip().startswith(b"<")
                    if looks_html:
                        raw = data.decode(_response_charset(content_type), errors="replace")
                        parse_diagnostics: list[str] = []
                        segments = html_to_segments(raw, diagnostics=parse_diagnostics)
                        record["course_title_candidates"] = extract_syllabus_title_candidates(segments)
                        record["front_matter"] = extract_syllabus_front_matter(
                            segments,
                            source_url=url,
                            source_sha256=digest,
                            source_artifact=artifact_relative,
                        )
                        record["extensions"]["segment_count"] = len(segments)
                        record["diagnostics"].extend(parse_diagnostics)
                        if not record["front_matter"]:
                            record["diagnostics"].append(
                                "Fetched HTML, but no recognized description, course-outcome, or required-material heading was found."
                            )
                    else:
                        record["diagnostics"].append(
                            "Fetched source was retained, but this runtime only extracts supplemental fields from HTML."
                        )
            except HTTPError as exc:
                record["fetch_status"] = "fetch_error"
                record["http_status"] = exc.code
                record["diagnostics"].append(f"HTTP error: {exc.reason}")
            except (URLError, OSError, TimeoutError, ValueError) as exc:
                record["fetch_status"] = "fetch_error"
                record["diagnostics"].append(str(getattr(exc, "reason", exc)))
            except Exception as exc:  # optional evidence must never erase the export deliverable
                record["fetch_status"] = "fetch_error"
                record["diagnostics"].append(
                    f"Unexpected linked-syllabus {type(exc).__name__}: {exc}"
                )

        fields = sorted(record["front_matter"])
        field_note = f"; extracted {', '.join(fields)}" if fields else ""
        diagnostics.append(
            f"Linked syllabus: {reference['title']} — {record['fetch_status']}{field_note}; "
            f"source remains supplemental ({url})."
        )
        output.append(record)
    return output


def load_resources(manifest_root: ET.Element) -> dict[str, dict]:
    resources: dict[str, dict] = {}
    for elem in manifest_root.iter():
        if local_name(elem.tag) != "resource":
            continue
        identifier = elem.attrib.get("identifier", "")
        resources[identifier] = {
            "identifier": identifier,
            "type": elem.attrib.get("type", ""),
            "material_type": attr(elem, "material_type"),
            "href": elem.attrib.get("href", ""),
            "link_target": attr(elem, "link_target"),
            "title": attr(elem, "title"),
        }
    return resources


def classify(item: ET.Element, resource: dict | None, has_children: bool) -> str:
    resource_type_key = attr(item, "resource_type_key")
    if resource_type_key == "D2L.LE.Lti.Link":
        return "lti_link"
    if resource is None:
        return "module" if has_children else "unresolved"
    material = resource["material_type"]
    if material == "contentmodule":
        return "module"
    if material == "content":
        if _is_content_file_href(resource.get("href", "")):
            return CONTENT_FILE_KIND
        return "html_topic"
    if material == "contentlink":
        href = resource["href"]
        match = QUICKLINK_TYPE.search(href)
        target = match.group(1).lower() if match else ""
        mapping = {
            "quiz": "quiz_link",
            "dropbox": "dropbox_link",
            "discuss": "discussion_link",
            "checklist": "checklist_link",
            "survey": "survey_link",
            "lti": "lti_link",
            "selfassess": "selfassessment_link",
            "content": "content_link",
        }
        return mapping.get(target, "quicklink")
    if material == "imsbasiclti_xmlv1p0":
        return "lti_link"
    if material.startswith("d2l"):
        return material
    return material or "unknown"


def walk_items(
    parent: ET.Element,
    resources: dict[str, dict],
    package_root: Path,
    diagnostics: list[str],
    depth: int = 0,
    parent_hidden: bool = False,
) -> list[dict]:
    nodes = []
    for item in parent:
        if local_name(item.tag) != "item":
            continue
        title = ""
        for child in item:
            if local_name(child.tag) == "title":
                title = clean(child.text)
                break
        identifierref = item.attrib.get("identifierref", "")
        resource = resources.get(identifierref) if identifierref else None
        item_hidden = parent_hidden or attr(item, "isvisible").lower() == "false"
        children = walk_items(item, resources, package_root, diagnostics, depth + 1, item_hidden)
        kind = classify(item, resource, bool(children))
        href = resource["href"] if resource else ""
        rcode_match = URL_RCODE.search(href) if href else None
        node = {
            "title": title,
            "identifier": item.attrib.get("identifier", ""),
            "identifierref": identifierref,
            "kind": kind,
            "href": href,
            "rcode": rcode_match.group(1) if rcode_match else "",
            "is_hidden": item_hidden,
            "resource_code": attr(item, "resource_code"),
            "description": item.attrib.get("description", ""),
            "flags": [],
            "children": children,
        }
        if identifierref and resource is None:
            node["flags"].append("identifierref does not resolve to a resource")
            diagnostics.append(f"item {title!r}: identifierref {identifierref} unresolved")
        if kind in {"html_topic", CONTENT_FILE_KIND} and href:
            normalized = href.replace("\\", "/").split("?")[0]
            if not (package_root / normalized).exists():
                node["flags"].append("href missing from package")
                label = "content file" if kind == CONTENT_FILE_KIND else "html topic"
                diagnostics.append(f"{label} {title!r}: href missing from package: {href}")
        nodes.append(node)
    return nodes


def descendant_count(node: dict) -> int:
    return sum(1 + descendant_count(child) for child in node.get("children", []))


def extract_html_topics(
    nodes: list[dict],
    package_root: Path,
    diagnostics: list[str],
    creatorplus_report: CreatorPlusPackageReport | None = None,
) -> list[dict]:
    topics = []

    def html_topic_from_node(node: dict, *, hidden: bool = False) -> dict | None:
        rel = node["href"].replace("\\", "/").split("?")[0]
        page_path = package_root / rel
        try:
            payload = page_path.read_bytes()
        except OSError as exc:
            diagnostics.append(f"html topic {node['title']!r}: unreadable: {exc}")
            return None
        if _looks_like_binary_payload(payload):
            reason = "binary-like payload"
            if hidden:
                topics.append(
                    _hidden_manifest_topic(
                        node,
                        action="The linked file looked binary-like, so the file body was not decoded.",
                    )
                )
                diagnostics.append(
                    f"hidden content {node['title']!r}: preserved as hidden/faculty-facing file reference "
                    f"because payload looked binary-like: {node['href']}"
                )
            else:
                topics.append(_content_file_topic(node, reason=reason))
                diagnostics.append(
                    f"html topic {node['title']!r}: body extraction skipped for binary-like payload: {node['href']}"
                )
            return None
        raw = payload.decode("utf-8", errors="replace")
        missing_refs = []
        server_refs = []
        unsafe_refs = []
        html_referenced_paths = []
        html_references, provider_activity_hrefs = collect_html_references(raw)
        for reference in html_references:
            ref = reference["value"].strip()
            if not ref or ref.lower().startswith(URL_SCHEMES):
                continue
            relationship = None
            if (
                creatorplus_report is not None
                and reference["attribute"] == "data-file"
                and ref.casefold().endswith(".practice.json")
            ):
                relationship = creatorplus_report.relationship_for(
                    package_root=package_root,
                    html_dir=page_path.parent,
                    data_file=ref,
                )
            if relationship is not None:
                if relationship.unsafe_reference:
                    state, value = "unsafe", ref
                elif relationship.selected_path:
                    state, value = "existing", relationship.selected_path
                else:
                    state, value = "missing", ref
            else:
                state, value = resolve_html_reference(
                    ref,
                    page_path=page_path,
                    package_root=package_root,
                )
            if state == "server":
                server_refs.append(ref)
            elif state == "existing":
                html_referenced_paths.append(value)
            elif state == "unsafe":
                unsafe_refs.append(ref)
            elif state == "missing":
                missing_refs.append(ref)
        missing_alt_refs = missing_alt_image_refs(raw)
        missing_alt = len(missing_alt_refs)
        title_match = TITLE_TAG.search(raw)
        body_text = html_to_text(raw)
        body_segments = html_to_segments(
            raw,
            package_root=package_root,
            html_dir=page_path.parent,
            diagnostics=diagnostics,
            creatorplus_report=creatorplus_report,
        )
        topic = {
            "manifest_title": node["title"],
            "html_title": html_to_text(title_match.group(1)) if title_match else "",
            "href": node["href"],
            "body_text": body_text,
            "body_segments": body_segments,
            "empty_page": len(body_text) == 0,
            "missing_refs": sorted(set(missing_refs)),
            "server_refs": sorted(set(server_refs)),
            "unsafe_refs": sorted(set(unsafe_refs)),
            "html_referenced_paths": sorted(set(html_referenced_paths)),
            "provider_activity_hrefs": sorted(
                provider_activity_hrefs,
                key=lambda item: (item["state"], item["value"]),
            ),
            "images_missing_alt": missing_alt,
            "images_missing_alt_refs": sorted(set(missing_alt_refs)),
        }
        if hidden:
            hidden_segments = _prepend_hidden_notice(
                node,
                body_segments,
                "Body text was extracted below for review; keep in mind this item is hidden/faculty-facing.",
            )
            hidden_blocks = [block for segment in hidden_segments for block in segment.get("blocks", [])]
            topic.update(
                {
                    "body_text": blocks_to_text(hidden_blocks),
                    "body_segments": hidden_segments,
                    "empty_page": False,
                    "hidden_manifest_item": True,
                    "extraction_note": "hidden/faculty-facing HTML body extracted",
                }
            )
        for ref in sorted(set(missing_refs)):
            diagnostics.append(f"html topic {node['title']!r}: broken relative reference: {ref}")
        for ref in sorted(set(unsafe_refs)):
            diagnostics.append(f"html topic {node['title']!r}: unsafe package-relative reference: {ref}")
        if not body_text:
            diagnostics.append(f"html topic {node['title']!r}: page body is empty")
        return topic

    def visit(node_list: list[dict]) -> None:
        for node in node_list:
            if node.get("is_hidden"):
                if node["kind"] == "module":
                    topics.append(
                        _hidden_manifest_topic(
                            node,
                            action=(
                                "The module itself has no page body; descendant items are preserved below "
                                "as hidden/faculty-facing review content where possible."
                            ),
                        )
                    )
                    diagnostics.append(
                        f"hidden module {node['title']!r}: included as hidden/faculty-facing section with "
                        f"{descendant_count(node)} descendant item(s)"
                    )
                elif node["kind"] == "html_topic" and node.get("href") and "href missing from package" not in node["flags"]:
                    topic = html_topic_from_node(node, hidden=True)
                    if topic is not None:
                        topics.append(topic)
                        diagnostics.append(
                            f"hidden content {node['title']!r}: included as hidden/faculty-facing HTML body: {node['href']}"
                        )
                elif node["kind"] == CONTENT_FILE_KIND and node.get("href"):
                    topics.append(
                        _hidden_manifest_topic(
                            node,
                            action="The linked course file is hidden/faculty-facing and was preserved as a file reference.",
                        )
                    )
                    diagnostics.append(
                        f"hidden content {node['title']!r}: preserved as hidden/faculty-facing file reference: {node['href']}"
                    )
                elif node.get("href") or node.get("description"):
                    topics.append(
                        _hidden_manifest_topic(
                            node,
                            body_segments=_hidden_description_segments(node),
                            action=(
                                "The manifest link is hidden/faculty-facing; any manifest description is "
                                "included below."
                            ),
                            skipped_body_extraction=False,
                            extraction_note="hidden/faculty-facing manifest link extracted",
                        )
                    )
                    diagnostics.append(f"hidden content {node['title']!r}: included as hidden/faculty-facing manifest link")
                visit(node["children"])
                continue
            if node["kind"] == CONTENT_FILE_KIND and node["href"]:
                topics.append(_content_file_topic(node, reason="non-HTML course file"))
                diagnostics.append(
                    f"content file {node['title']!r}: body extraction skipped for non-HTML file: {node['href']}"
                )
            elif node["kind"] == "html_topic" and node["href"] and "href missing from package" not in node["flags"]:
                topic = html_topic_from_node(node)
                if topic is not None:
                    topics.append(topic)
            visit(node["children"])

    visit(nodes)
    return topics


def package_file_category(rel_path: str) -> str:
    lower = rel_path.lower()
    name = Path(rel_path).name.lower()
    if name in CORE_PACKAGE_FILES or name.startswith("quiz_d2l_") or name.startswith("discussion_d2l"):
        return "d2l_xml"
    if lower.endswith(".xml"):
        return "xml"
    if lower.endswith((".html", ".htm", ".xhtml")):
        return "html"
    if lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp")):
        return "image"
    if lower.endswith((".mp4", ".mov", ".avi", ".m4v", ".webm", ".mp3", ".wav")):
        return "media"
    if lower.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt", ".qmd", ".rmd")):
        return "document"
    if lower.endswith((".css", ".js", ".json")):
        return "frontend_support"
    return "other"


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def package_files(package_root: Path) -> dict[str, int]:
    files: dict[str, int] = {}
    for path in package_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package_root).as_posix()
        if any(part in {"__MACOSX", ".git", ".github", ".pytest_cache", "__pycache__"} for part in Path(rel).parts):
            continue
        if Path(rel).name == ".DS_Store" or Path(rel).name.startswith("._"):
            continue
        try:
            files[rel] = path.stat().st_size
        except OSError:
            files[rel] = 0
    return files


def manifest_linked_paths(nodes: list[dict]) -> tuple[set[str], set[str]]:
    visible: set[str] = set()
    hidden: set[str] = set()

    def visit(node_list: list[dict]) -> None:
        for node in node_list:
            href = node.get("href", "")
            if _is_local_href(href):
                target = _href_path_part(href)
                if target:
                    if node.get("is_hidden") and node.get("kind") == CONTENT_FILE_KIND:
                        hidden.add(target)
                    else:
                        visible.add(target)
            visit(node.get("children", []))

    visit(nodes)
    return visible, hidden


def file_scope_summary(label: str, paths: set[str], size_by_path: dict[str, int], *, limit: int = 8) -> str | None:
    existing = [(path, size_by_path[path]) for path in paths if path in size_by_path]
    if not existing:
        return None
    total = sum(size for _, size in existing)
    by_type: Counter[str] = Counter()
    by_type_size: Counter[str] = Counter()
    for path, size in existing:
        category = package_file_category(path)
        by_type[category] += 1
        by_type_size[category] += size
    type_summary = ", ".join(
        f"{category}: {by_type[category]} / {format_bytes(by_type_size[category])}"
        for category in sorted(by_type_size, key=lambda key: (-by_type_size[key], key))[:6]
    )
    largest = ", ".join(
        f"{path} ({format_bytes(size)})"
        for path, size in sorted(existing, key=lambda item: item[1], reverse=True)[:limit]
    )
    return (
        f"Package scope: {label}: {len(existing)} file(s), {format_bytes(total)} total"
        f"; by type: {type_summary}; largest: {largest}"
    )


def package_scope_diagnostics(
    nodes: list[dict],
    package_root: Path,
    html_topics: list[dict] | None = None,
) -> list[str]:
    size_by_path = package_files(package_root)
    visible_paths, hidden_paths = manifest_linked_paths(nodes)
    html_referenced_paths = {
        path
        for topic in html_topics or []
        for path in topic.get("html_referenced_paths", [])
    }
    core_paths = {
        path
        for path in size_by_path
        if package_file_category(path) == "d2l_xml" or Path(path).name.lower() in CORE_PACKAGE_FILES
    }
    support_paths = html_referenced_paths - visible_paths - hidden_paths - core_paths
    unlinked_paths = set(size_by_path) - visible_paths - hidden_paths - core_paths - support_paths
    diagnostics: list[str] = []
    hidden_summary = file_scope_summary("hidden manifest-linked non-HTML files preserved as references", hidden_paths, size_by_path)
    if hidden_summary:
        diagnostics.append(hidden_summary)
    support_summary = file_scope_summary(
        "HTML-referenced support files",
        support_paths,
        size_by_path,
    )
    if support_summary:
        diagnostics.append(support_summary)
    unlinked_summary = file_scope_summary(
        "files not directly linked from the visible manifest",
        unlinked_paths,
        size_by_path,
    )
    if unlinked_summary:
        diagnostics.append(unlinked_summary)
    return diagnostics


def front_matter_source_diagnostics(package_root: Path) -> list[str]:
    path = package_root / "syllabus_d2l.xml"
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [f"Front matter source: syllabus_d2l.xml is not well-formed XML: {exc}"]
    description = clean(attr(root, "description"))
    homepage_file_id = clean(attr(root, "homepage_file_id"))
    if not description and not homepage_file_id:
        return ["Front matter source: syllabus_d2l.xml exists, but description and homepage_file_id are empty."]
    if not description:
        return ["Front matter source: syllabus_d2l.xml exists, but description is empty."]
    return []


def count_kinds(nodes: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}

    def visit(node_list: list[dict]) -> None:
        for node in node_list:
            counts[node["kind"]] = counts.get(node["kind"], 0) + 1
            visit(node["children"])

    visit(nodes)
    return counts


def render_tree(nodes: list[dict], indent: int = 0) -> list[str]:
    lines = []
    for node in nodes:
        marker = f"[{node['kind']}]"
        suffix = ""
        if node["is_hidden"]:
            suffix += " (hidden)"
        if node["flags"]:
            suffix += " ⚠ " + "; ".join(node["flags"])
        lines.append(f"{'  ' * indent}- {node['title'] or '(untitled)'} {marker}{suffix}")
        lines.extend(render_tree(node["children"], indent + 1))
    return lines


def render_markdown(
    label: str,
    tree: list[dict],
    topics: list[dict],
    diagnostics: list[str],
    syllabus_references: list[dict] | None = None,
) -> str:
    counts = count_kinds(tree)
    lines = [
        f"# Course Structure — {label}",
        "",
        "Item kinds: " + ", ".join(f"{kind}: {count}" for kind, count in sorted(counts.items())),
        "",
        "## Module / topic tree",
        "",
    ]
    lines.extend(render_tree(tree))
    if topics:
        broken = [t for t in topics if t["missing_refs"]]
        empty = [t for t in topics if t["empty_page"]]
        lines.extend(
            [
                "",
                "## Extracted content topics",
                "",
                f"- Topics extracted: {len(topics)}",
                f"- Non-HTML course files preserved as references: "
                f"{sum(1 for t in topics if t.get('skipped_body_extraction'))}",
                f"- Pages with broken relative references: {len(broken)}",
                f"- Empty pages: {len(empty)}",
                f"- Images missing alt text: {sum(t['images_missing_alt'] for t in topics)}",
                f"- Pages using server-hosted (root-absolute) references: "
                f"{sum(1 for t in topics if t['server_refs'])} "
                "(resolve on the Brightspace server, not in the package — portability note, not a break)",
                "",
                "Verbatim body text is preserved for HTML/text topics; non-HTML course files are linked without decoding.",
            ]
        )
    if syllabus_references:
        lines.extend(["", "## Linked syllabus evidence", ""])
        for reference in syllabus_references:
            fields = ", ".join(sorted(reference.get("front_matter", {}))) or "none"
            artifact = reference.get("artifact_path") or "not retained"
            lines.extend(
                [
                    f"- **{reference.get('title') or 'Syllabus'}** — "
                    f"{reference.get('fetch_status', 'inventory_only')}; "
                    f"supplemental fields: {fields}",
                    f"  - Manifest path: {reference.get('manifest_path') or '(not recorded)'}",
                    f"  - URL: {reference.get('url')}",
                    f"  - Retained artifact: {artifact}",
                    f"  - SHA-256: {reference.get('sha256') or 'not fetched'}",
                ]
            )
    lines.extend(["", "## Diagnostics", ""])
    lines.extend(f"- {d}" for d in diagnostics) if diagnostics else lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="Unpacked export directory or export ZIP")
    parser.add_argument("--label", default="", help="Label for output filenames (default: folder name)")
    parser.add_argument("--extract-html", action="store_true", help="Also extract HTML topic text and check page references")
    parser.add_argument(
        "--package-profile",
        choices=sorted(PACKAGE_PROFILES),
        default="external",
        help="Creator+ diagnostic profile (default: external evidence inspection)",
    )
    parser.add_argument(
        "--creatorplus-filename-policy",
        choices=sorted(FILENAME_POLICIES),
        default="preserve_observed",
        help="Preserve observed Creator+ filenames or enforce config-<id> naming",
    )
    parser.add_argument(
        "--no-syllabus-fetch",
        action="store_true",
        help=(
            "Inventory manifest-linked syllabi without fetching them. By default, "
            "--extract-html performs a non-fatal best-effort fetch from recognized hosts."
        ),
    )
    parser.add_argument(
        "--syllabus-timeout",
        type=float,
        default=8.0,
        help="Per-syllabus fetch timeout in seconds (default: 8).",
    )
    parser.add_argument(
        "--syllabus-host",
        action="append",
        default=[],
        help="Additional allowed syllabus hostname; may be repeated.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write outputs (default: <repo>/workspace/review)",
    )
    parser.add_argument("--run-id", default="", help="Optional shared coursecraft run ID supplied by an orchestrator.")
    parser.add_argument(
        "--source-identity",
        type=Path,
        default=None,
        help="Optional precomputed source-identity JSON supplied by an orchestrator.",
    )
    args = parser.parse_args(argv)

    holder: list = []
    source_arg = args.export.expanduser().resolve()
    root = load_export_root(source_arg, holder)
    label = args.label or safe_label(args.export.stem if args.export.is_file() else args.export.name)

    diagnostics: list[str] = []
    unpacked_root = root
    root, manifest_path = resolve_export_root(root)
    if manifest_path is None:
        raise SystemExit(f"error: no imsmanifest.xml in {unpacked_root}")
    if root != unpacked_root:
        diagnostics.append(
            f"imsmanifest.xml found below the export root; using '{root.name}/' as the export root"
        )
    try:
        manifest_root = ET.parse(manifest_path).getroot()
    except ET.ParseError as exc:
        raise SystemExit(f"error: imsmanifest.xml is not well-formed: {exc}")

    resources = load_resources(manifest_root)
    organizations = [el for el in manifest_root.iter() if local_name(el.tag) == "organization"]
    tree: list[dict] = []
    for organization in organizations:
        tree.extend(walk_items(organization, resources, root, diagnostics))
    if not tree:
        diagnostics.append("manifest has no organization items")

    creatorplus_report = None
    if args.extract_html:
        creatorplus_report = inspect_creatorplus_package(
            root,
            profile=args.package_profile,
            filename_policy=args.creatorplus_filename_policy,
        )
        diagnostics.extend(
            f"Creator+ {row.severity}: {format_creatorplus_diagnostic(row)}"
            for row in creatorplus_report.diagnostics
        )
    topics = (
        extract_html_topics(tree, root, diagnostics, creatorplus_report)
        if args.extract_html
        else []
    )
    if args.extract_html:
        diagnostics.extend(package_scope_diagnostics(tree, root, topics))
        diagnostics.extend(front_matter_source_diagnostics(root))

    output_dir = args.output_dir or (Path(__file__).resolve().parents[1] / "workspace" / "review")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_label(label)}__course_structure"
    allowed_syllabus_hosts = {
        *(host.casefold().rstrip(".") for host in DEFAULT_SYLLABUS_HOSTS),
        *(host.casefold().rstrip(".") for host in args.syllabus_host if host.strip()),
    }
    syllabus_references = collect_syllabus_supplements(
        tree,
        html_topics=topics,
        output_dir=output_dir,
        stem=stem,
        fetch_enabled=bool(args.extract_html and not args.no_syllabus_fetch),
        timeout=max(0.1, args.syllabus_timeout),
        allowed_hosts=allowed_syllabus_hosts,
        diagnostics=diagnostics,
    )
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"
    md_path.write_text(
        render_markdown(label, tree, topics, diagnostics, syllabus_references),
        encoding="utf-8",
    )
    source_identity = load_source_identity(
        args.source_identity.expanduser().resolve() if args.source_identity else None,
        source_arg=source_arg,
        logical_root=root,
    )
    run_id = args.run_id or new_run_id()
    extensions = {
        "syllabus_references": syllabus_references,
        "linked_syllabus_policy": {
            "evidence_role": "supplemental_linked_syllabus",
            "fetch_requested": bool(args.extract_html and not args.no_syllabus_fetch),
            "allowed_hosts": sorted(allowed_syllabus_hosts),
            "package_local_content_precedence": True,
            "extensions": {},
        },
    }
    if creatorplus_report is not None:
        extensions["coursecraft.creatorplus_relationships"] = creatorplus_report.to_dict()
    payload = annotate_structure_payload(
        {
            "schema": "coursecraft.structure/1",
            "run_id": run_id,
            "source": source_identity,
            "export": str(args.export),
            "label": label,
            "kind_counts": count_kinds(tree),
            "tree": tree,
            "html_topics": topics,
            "diagnostics": diagnostics,
            "extensions": extensions,
        }
    )
    contract_errors = [issue.render() for issue in validate_contract(payload) if issue.severity == "error"]
    if contract_errors:
        raise SystemExit("error: coursecraft.structure/1 validation failed:\n" + "\n".join(contract_errors))
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    counts = count_kinds(tree)
    print(f"items: {sum(counts.values())} ({counts.get('module', 0)} modules)")
    if args.extract_html:
        print(f"html topics extracted: {len(topics)}")
    print(
        f"syllabus links: {len(syllabus_references)} "
        f"({sum(1 for item in syllabus_references if item.get('fetch_status') == 'fetched')} fetched)"
    )
    print(f"diagnostics: {len(diagnostics)}")
    print("schema: coursecraft.structure/1")
    print(f"run id: {run_id}")
    print(f"report: {md_path}")
    print(f"json: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
