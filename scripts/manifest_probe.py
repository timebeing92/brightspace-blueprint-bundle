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
    matches = list(path.rglob("imsmanifest.xml"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError("Could not find imsmanifest.xml beneath the supplied path.")
    raise FileExistsError("Found multiple imsmanifest.xml files; point directly to the desired one.")


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

    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError("Could not find imsmanifest.xml inside the supplied ZIP.")
    raise FileExistsError("Found multiple imsmanifest.xml files inside the ZIP; unpack and point directly.")


def iter_elements(root: ET.Element, wanted: str):
    for elem in root.iter():
        if local_name(elem.tag) == wanted:
            yield elem


def load_manifest_root(source: Path) -> tuple[ET.Element, str, str]:
    if source.is_file() and source.suffix.lower() == ".zip":
        member = find_manifest_in_zip(source)
        with ZipFile(source, "r") as zf:
            with zf.open(member, "r") as manifest_file:
                tree = ET.parse(manifest_file)
        return tree.getroot(), f"{source}!{member}", source.stem

    manifest_path = find_manifest_path(source)
    tree = ET.parse(manifest_path)
    label = manifest_path.parent.name or "manifest"
    return tree.getroot(), str(manifest_path), label


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Brightspace imsmanifest.xml for quick review.")
    parser.add_argument(
        "source",
        help="Path to a Brightspace export ZIP, unpacked folder, or imsmanifest.xml",
    )
    parser.add_argument("--output-dir", default=None, help="Optional directory for markdown/json outputs")
    args = parser.parse_args()

    try:
        source = Path(args.source).expanduser().resolve()
        root, manifest_ref, label = load_manifest_root(source)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2

    data = parse_manifest(root, manifest_ref, label)
    md = markdown_report(data)

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = data["label"] or "manifest"
        (output_dir / f"{stem}__manifest_probe.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        (output_dir / f"{stem}__manifest_probe.md").write_text(md, encoding="utf-8")

    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
