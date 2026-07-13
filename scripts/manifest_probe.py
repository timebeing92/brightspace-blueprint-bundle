#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from common_xml import local_name


def find_manifest_path(path: Path) -> Path:
    if path.is_file():
        return path
    candidate = path / "imsmanifest.xml"
    if candidate.exists():
        return candidate
    matches = sorted(path.rglob("imsmanifest.xml"), key=lambda p: (len(p.parts), str(p)))
    if not matches:
        raise FileNotFoundError("Could not find imsmanifest.xml beneath the supplied path.")
    if len(matches) > 1:
        others = ", ".join(str(m) for m in matches[1:])
        sys.stderr.write(
            f"warning: multiple imsmanifest.xml files found; using shallowest "
            f"({matches[0]}); others: {others}\n"
        )
    return matches[0]


def should_ignore_zip_member(name: str) -> bool:
    parts = Path(name).parts
    return any(part in {"__MACOSX", ".DS_Store"} for part in parts) or any(
        part.startswith("._") for part in parts
    )


def find_manifest_in_zip(zip_path: Path) -> str:
    with ZipFile(zip_path, "r") as zf:
        matches = sorted(
            name
            for name in zf.namelist()
            if not name.endswith("/")
            and Path(name).name == "imsmanifest.xml"
            and not should_ignore_zip_member(name)
        )

    if not matches:
        raise FileNotFoundError("Could not find imsmanifest.xml inside the supplied ZIP.")
    matches.sort(key=lambda name: (len(Path(name).parts), name))
    if len(matches) > 1:
        others = ", ".join(matches[1:])
        sys.stderr.write(
            f"warning: multiple imsmanifest.xml files found in ZIP; using shallowest "
            f"({matches[0]}); others: {others}\n"
        )
    return matches[0]


def iter_elements(root: ET.Element, wanted: str):
    for elem in root.iter():
        if local_name(elem.tag) == wanted:
            yield elem


def load_manifest_root(source: Path, display: str | None = None) -> tuple[ET.Element, str, str]:
    # `source` is used for file access; `display` (the path as given on the
    # CLI) is what gets recorded in outputs, so they stay machine-portable.
    shown = display if display is not None else str(source)
    if source.is_file() and source.suffix.lower() == ".zip":
        member = find_manifest_in_zip(source)
        with ZipFile(source, "r") as zf:
            with zf.open(member, "r") as manifest_file:
                tree = ET.parse(manifest_file)
        return tree.getroot(), f"{shown}!{member}", source.stem

    manifest_path = find_manifest_path(source)
    tree = ET.parse(manifest_path)
    label = manifest_path.parent.name or "manifest"
    if display is None:
        manifest_ref = str(manifest_path)
    elif manifest_path == source:
        manifest_ref = display
    else:
        manifest_ref = str(Path(display) / manifest_path.relative_to(source))
    return tree.getroot(), manifest_ref, label


def parse_manifest(root: ET.Element, manifest_ref: str, label: str) -> dict:

    resources = []
    for res in iter_elements(root, "resource"):
        resources.append({
            "identifier": res.attrib.get("identifier"),
            "type": res.attrib.get("type"),
            "href": res.attrib.get("href"),
        })

    items = []
    for item in iter_elements(root, "item"):
        title = None
        for child in item:
            if local_name(child.tag) == "title":
                title = (child.text or "").strip() or None
                break
        items.append({
            "identifier": item.attrib.get("identifier"),
            "identifierref": item.attrib.get("identifierref"),
            "title": title,
        })

    href_missing = [r for r in resources if not r.get("href")]
    likely_quiz = [
        r for r in resources
        if (r.get("href") or "").lower().find("quiz") >= 0
    ]
    likely_html = [
        r for r in resources
        if (r.get("href") or "").lower().endswith((".html", ".htm"))
    ]

    return {
        "label": label,
        "manifest_path": manifest_ref,
        "resource_count": len(resources),
        "item_count": len(items),
        "items_with_identifierref": sum(1 for i in items if i.get("identifierref")),
        "resources": resources,
        "items": items,
        "resources_missing_href": href_missing,
        "likely_quiz_resources": likely_quiz,
        "likely_html_resources": likely_html,
    }


def markdown_report(data: dict) -> str:
    lines = [
        f"# Manifest Probe — {data['label']}",
        "",
        f"- Manifest: `{data['manifest_path']}`",
        f"- Resources: **{data['resource_count']}**",
        f"- Items: **{data['item_count']}**",
        f"- Items with `identifierref`: **{data['items_with_identifierref']}**",
        "",
        "## Likely quiz resources",
        "",
    ]
    if data["likely_quiz_resources"]:
        for r in data["likely_quiz_resources"]:
            lines.append(f"- `{r.get('identifier')}` → `{r.get('href')}`")
    else:
        lines.append("- none obvious")
    lines.extend(["", "## Likely HTML resources", ""])
    if data["likely_html_resources"]:
        for r in data["likely_html_resources"]:
            lines.append(f"- `{r.get('identifier')}` → `{r.get('href')}`")
    else:
        lines.append("- none obvious")
    lines.extend(["", "## Resources missing href", ""])
    if data["resources_missing_href"]:
        for r in data["resources_missing_href"]:
            lines.append(f"- `{r.get('identifier')}` (type={r.get('type')})")
    else:
        lines.append("- none")
    lines.extend(["", "## All resources", ""])
    for resource in data["resources"]:
        href = resource.get("href") or "(none)"
        lines.append(
            f"- `{resource.get('identifier')}` (type={resource.get('type')}) → `{href}`"
        )
    lines.extend(["", "## All items", ""])
    for item in data["items"]:
        title = item.get("title") or "(untitled)"
        lines.append(f"- `{item.get('identifier')}` / `{item.get('identifierref')}` — {title}")
    return "\n".join(lines) + "\n"


def summary_report(data: dict, report_path: Path | None = None) -> str:
    lines = [
        f"Manifest probe — {data['label']}: {data['resource_count']} resources, "
        f"{data['item_count']} items ({data['items_with_identifierref']} with identifierref).",
        f"  Likely quiz resources: {len(data['likely_quiz_resources'])} · "
        f"likely HTML: {len(data['likely_html_resources'])} · "
        f"missing href: {len(data['resources_missing_href'])}",
    ]
    if report_path is not None:
        lines.append(f"  Full report: {report_path}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Brightspace imsmanifest.xml for quick review.")
    parser.add_argument(
        "source",
        help="Path to a Brightspace export ZIP, unpacked folder, or imsmanifest.xml",
    )
    parser.add_argument("--output-dir", default=None, help="Optional directory for markdown/json outputs")
    parser.add_argument(
        "--print-full",
        action="store_true",
        help="Print the full markdown report to stdout (default: a short summary)",
    )
    args = parser.parse_args()

    try:
        source = Path(args.source).expanduser().resolve()
        root, manifest_ref, label = load_manifest_root(source, display=args.source)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    data = parse_manifest(root, manifest_ref, label)
    md = markdown_report(data)

    md_path = None
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = data["label"] or "manifest"
        (output_dir / f"{stem}__manifest_probe.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        md_path = output_dir / f"{stem}__manifest_probe.md"
        md_path.write_text(md, encoding="utf-8")

    print(md if args.print_full else summary_report(data, md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
