#!/usr/bin/env python3
"""Reconstruct the module/topic tree of a Brightspace export for review.

Reads imsmanifest.xml organizations, resolves each item's identifierref to its
resource (type, d2l material_type, href), classifies items (module, HTML topic,
quicklink by target type, tool payload, LTI link — typed but not parsed), and
flags hidden items, unresolved identifierrefs, and missing hrefs.

With --extract-html, also pulls each HTML content topic's title and body text
verbatim into the JSON payload and checks that relative asset/link references
inside each page resolve within the package (extraction mode: no editorial
changes, readable text is derived alongside the raw page, never replacing it).

Outputs a markdown tree and canonical JSON to workspace/review/.

Usage:
    python3 scripts/reconstruct_course_structure.py /path/to/unpacked/export
    python3 scripts/reconstruct_course_structure.py export.zip --extract-html
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

from common_xml import clean, local_name

URL_SCHEMES = ("http://", "https://", "data:", "mailto:", "javascript:", "tel:", "#", "//")
QUICKLINK_TYPE = re.compile(r"[?&]type=([A-Za-z]+)")
URL_RCODE = re.compile(r"r[Cc]ode=([A-Za-z0-9._-]+)")
REF_ATTR = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ALT_ATTR = re.compile(r"""\balt\s*=\s*("[^"]*[^"\s][^"]*"|'[^']*[^'\s][^']*')""", re.IGNORECASE)
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def attr(elem: ET.Element, name: str) -> str:
    """Attribute lookup tolerant of namespace prefixes."""
    for key, value in elem.attrib.items():
        if local_name(key) == name:
            return value
    return ""


HEADING_TAG = re.compile(r"<h([1-4])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)


def _flatten_text(raw: str) -> str:
    stripped = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(stripped)).strip()


def html_to_text(raw: str) -> str:
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    return _flatten_text(raw)


# Formatting-preserving HTML extraction --------------------------------------
# Rather than flattening pages to one line, we parse them into blocks so that
# paragraphs, bullet lists, and links survive into the blueprint. Each block is
# {"kind": "p"|"li", "level": int, "runs": [{"text": str, "href": str}]}.
_SKIP_TAGS = {"script", "style", "noscript"}
_BLOCK_BREAK_TAGS = {"p", "div", "br", "h5", "h6", "blockquote", "figure",
                     "figcaption", "tr", "table", "section", "article"}
_ARTIFACT_TEXTS = {"basic page - no banner", "basic page", "no banner", "banner"}
_LIVE_SCHEMES = ("http://", "https://", "mailto:")


class _BlockExtractor(HTMLParser):
    """Walk an HTML fragment and emit paragraph/list blocks with link-aware runs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._runs: list[dict] = []
        self._kind = "p"
        self._level = 0
        self._list_depth = 0
        self._href: list[str] = []
        self._skip = 0

    def _flush(self) -> None:
        runs = []
        for run in self._runs:
            text = re.sub(r"\s+", " ", run["text"])
            if not text.strip() and not runs:
                continue  # drop leading whitespace-only runs
            runs.append({"text": text, "href": run["href"]})
        while runs and not runs[-1]["text"].strip():
            runs.pop()
        if runs:
            runs[0]["text"] = runs[0]["text"].lstrip()
            runs[-1]["text"] = runs[-1]["text"].rstrip()
            combined = "".join(r["text"] for r in runs).strip().lower()
            if combined and combined not in _ARTIFACT_TEXTS:
                self.blocks.append({"kind": self._kind, "level": self._level, "runs": runs})
        self._runs = []
        self._kind = "p"
        self._level = 0

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "iframe":
            src = attr_map.get("src", "")
            if src:
                self._flush()
                title = attr_map.get("title", "").strip() or "Embedded media"
                self.blocks.append({"kind": "p", "level": 0, "runs": [{"text": title, "href": src}]})
            return
        if tag == "img":
            alt = (attr_map.get("alt") or "").strip()
            src = attr_map.get("src", "")
            if alt:
                href = src if src.startswith(_LIVE_SCHEMES) else ""
                self._runs.append({"text": f"[image: {alt}]", "href": href})
            return
        if tag in ("ul", "ol"):
            self._list_depth += 1
            return
        if tag == "li":
            self._flush()
            self._kind = "li"
            self._level = max(1, self._list_depth)
            return
        if tag in _BLOCK_BREAK_TAGS:
            self._flush()
            return
        if tag == "a":
            self._href.append(attr_map.get("href", ""))

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if tag in ("ul", "ol"):
            if self._list_depth:
                self._list_depth -= 1
            return
        if tag in ("p", "div", "li", "h5", "h6", "blockquote", "figure",
                   "figcaption", "table", "tr", "section", "article"):
            self._flush()
            return
        if tag == "a" and self._href:
            self._href.pop()

    def handle_data(self, data):
        if self._skip or not data:
            return
        self._runs.append({"text": data, "href": self._href[-1] if self._href else ""})

    def close(self):
        super().close()
        self._flush()


def html_fragment_to_blocks(raw: str) -> list[dict]:
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    parser = _BlockExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # pragma: no cover - malformed HTML fallback
        text = _flatten_text(raw)
        return [{"kind": "p", "level": 0, "runs": [{"text": text, "href": ""}]}] if text else []
    return parser.blocks


def blocks_to_text(blocks: list[dict]) -> str:
    return clean(" ".join(run["text"] for block in blocks for run in block["runs"]))


def html_to_segments(raw: str) -> list[dict]:
    """Split an HTML body into ``{heading, level, blocks, text}`` chunks by its own headings.

    Content before the first heading becomes an untitled intro segment
    (``heading == ""``, ``level == 0``). Each chunk keeps its formatting as a
    list of blocks (paragraphs / bullets with link-aware runs); ``text`` is a
    flattened convenience copy. This mirrors the page's authored structure
    rather than forcing a fixed taxonomy; downstream code decides which headings
    map to blueprint rows.
    """
    raw = re.sub(r"<(script|style)\b.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    matches = list(HEADING_TAG.finditer(raw))

    def make(heading: str, level: int, slice_raw: str) -> dict | None:
        blocks = html_fragment_to_blocks(slice_raw)
        if not blocks and not heading:
            return None
        return {"heading": heading, "level": level, "blocks": blocks, "text": blocks_to_text(blocks)}

    segments: list[dict] = []
    if not matches:
        seg = make("", 0, raw)
        return [seg] if seg else []

    if matches[0].start() > 0:
        seg = make("", 0, raw[: matches[0].start()])
        if seg:
            segments.append(seg)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        heading = _flatten_text(match.group(2))
        seg = make(heading, int(match.group(1)), raw[match.end() : end])
        if seg:
            segments.append(seg)
    return segments


def load_export_root(path: Path, holder: list) -> Path:
    if path.is_dir():
        return path
    if path.is_file() and zipfile.is_zipfile(path):
        tmp = tempfile.TemporaryDirectory(prefix="course_structure_")
        holder.append(tmp)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp.name)
        return Path(tmp.name)
    raise SystemExit(f"error: not an export directory or zip: {path}")


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
        children = walk_items(item, resources, package_root, diagnostics, depth + 1)
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
            "is_hidden": attr(item, "isvisible").lower() == "false",
            "resource_code": attr(item, "resource_code"),
            "description": item.attrib.get("description", ""),
            "flags": [],
            "children": children,
        }
        if identifierref and resource is None:
            node["flags"].append("identifierref does not resolve to a resource")
            diagnostics.append(f"item {title!r}: identifierref {identifierref} unresolved")
        if kind == "html_topic" and href:
            normalized = href.replace("\\", "/").split("?")[0]
            if not (package_root / normalized).exists():
                node["flags"].append("href missing from package")
                diagnostics.append(f"html topic {title!r}: href missing from package: {href}")
        nodes.append(node)
    return nodes


def extract_html_topics(nodes: list[dict], package_root: Path, diagnostics: list[str]) -> list[dict]:
    topics = []

    def visit(node_list: list[dict]) -> None:
        for node in node_list:
            if node["kind"] == "html_topic" and node["href"] and "href missing from package" not in node["flags"]:
                rel = node["href"].replace("\\", "/").split("?")[0]
                page_path = package_root / rel
                try:
                    raw = page_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    diagnostics.append(f"html topic {node['title']!r}: unreadable: {exc}")
                    visit(node["children"])
                    continue
                missing_refs = []
                server_refs = []
                for ref in REF_ATTR.findall(raw):
                    ref = ref.strip()
                    if not ref or ref.lower().startswith(URL_SCHEMES):
                        continue
                    target = ref.replace("\\", "/").split("?")[0].split("#")[0]
                    if not target:
                        continue
                    if target.startswith("/"):
                        # Root-absolute paths (/shared/..., /d2l/..., /content/...)
                        # resolve against the Brightspace server at runtime, not
                        # the package — inventory, don't flag.
                        server_refs.append(ref)
                        continue
                    if not (page_path.parent / target).exists():
                        missing_refs.append(ref)
                missing_alt = sum(1 for img in IMG_TAG.findall(raw) if not ALT_ATTR.search(img))
                title_match = TITLE_TAG.search(raw)
                body_text = html_to_text(raw)
                topics.append(
                    {
                        "manifest_title": node["title"],
                        "html_title": html_to_text(title_match.group(1)) if title_match else "",
                        "href": node["href"],
                        "body_text": body_text,
                        "body_segments": html_to_segments(raw),
                        "empty_page": len(body_text) == 0,
                        "missing_refs": sorted(set(missing_refs)),
                        "server_refs": sorted(set(server_refs)),
                        "images_missing_alt": missing_alt,
                    }
                )
                for ref in sorted(set(missing_refs)):
                    diagnostics.append(f"html topic {node['title']!r}: broken relative reference: {ref}")
                if not body_text:
                    diagnostics.append(f"html topic {node['title']!r}: page body is empty")
            visit(node["children"])

    visit(nodes)
    return topics


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


def render_markdown(label: str, tree: list[dict], topics: list[dict], diagnostics: list[str]) -> str:
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
                "## HTML topics",
                "",
                f"- Topics extracted: {len(topics)}",
                f"- Pages with broken relative references: {len(broken)}",
                f"- Empty pages: {len(empty)}",
                f"- Images missing alt text: {sum(t['images_missing_alt'] for t in topics)}",
                f"- Pages using server-hosted (root-absolute) references: "
                f"{sum(1 for t in topics if t['server_refs'])} "
                "(resolve on the Brightspace server, not in the package — portability note, not a break)",
                "",
                "Verbatim body text is preserved in the JSON payload; this note only summarizes.",
            ]
        )
    lines.extend(["", "## Diagnostics", ""])
    lines.extend(f"- {d}" for d in diagnostics) if diagnostics else lines.append("- None.")
    lines.append("")
    return "\n".join(lines)


def safe_label(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "export"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="Unpacked export directory or export ZIP")
    parser.add_argument("--label", default="", help="Label for output filenames (default: folder name)")
    parser.add_argument("--extract-html", action="store_true", help="Also extract HTML topic text and check page references")
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
    manifest_path = root / "imsmanifest.xml"
    if not manifest_path.exists():
        raise SystemExit(f"error: no imsmanifest.xml in {root}")
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

    topics = extract_html_topics(tree, root, diagnostics) if args.extract_html else []

    output_dir = args.output_dir or (Path(__file__).resolve().parents[1] / "workspace" / "review")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_label(label)}__course_structure"
    md_path = output_dir / f"{stem}.md"
    json_path = output_dir / f"{stem}.json"
    md_path.write_text(render_markdown(label, tree, topics, diagnostics), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "export": str(args.export),
                "label": label,
                "kind_counts": count_kinds(tree),
                "tree": tree,
                "html_topics": topics,
                "diagnostics": diagnostics,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    counts = count_kinds(tree)
    print(f"items: {sum(counts.values())} ({counts.get('module', 0)} modules)")
    if args.extract_html:
        print(f"html topics extracted: {len(topics)}")
    print(f"diagnostics: {len(diagnostics)}")
    print(f"report: {md_path}")
    print(f"json: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
