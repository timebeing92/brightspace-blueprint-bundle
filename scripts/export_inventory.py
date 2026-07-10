#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path


KNOWN_D2L_FILES = {
    "imsmanifest.xml",
    "questiondb.xml",
    "rubrics_d2l.xml",
    "grades_d2l.xml",
    "dropbox_d2l.xml",
    "checklist_d2l.xml",
    "conditionalrelease_d2l.xml",
    "intelligentagents_d2l.xml",
}


def should_ignore(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    ignore_names = {"__MACOSX", ".git", ".github", ".pytest_cache", "__pycache__", ".DS_Store"}
    return any(part in ignore_names for part in parts) or any(part.startswith("._") for part in parts)


def normalize_paths_from_folder(folder: Path) -> list[str]:
    paths = []
    for path in folder.rglob("*"):
        if path.is_file():
            rel = path.relative_to(folder).as_posix()
            if not should_ignore(rel):
                paths.append(rel)
    return sorted(paths)


def normalize_paths_from_zip(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return sorted(
            name for name in zf.namelist()
            if not name.endswith("/") and not should_ignore(name)
        )


def classify(path: str) -> str:
    lower = path.lower()
    name = Path(path).name.lower()

    if name in KNOWN_D2L_FILES or name.startswith("quiz_d2l_") or name.startswith("discussion_d2l"):
        return "d2l_xml"
    if lower.endswith(".xml"):
        return "xml"
    if lower.endswith((".html", ".htm")):
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


def course_name(path: Path) -> str:
    return path.stem if path.is_file() else path.name


def build_inventory(source: Path, relative_paths: list[str]) -> dict:
    counts = Counter(classify(p) for p in relative_paths)
    d2l_components = sorted(
        p for p in relative_paths
        if classify(p) == "d2l_xml"
    )
    likely_quiz_files = sorted(
        p for p in relative_paths
        if Path(p).name.lower().startswith("quiz_d2l_") or Path(p).name.lower() == "questiondb.xml"
    )
    likely_html_topics = sorted(
        p for p in relative_paths
        if p.lower().endswith((".html", ".htm"))
    )

    return {
        "source": str(source.resolve()),
        "label": course_name(source),
        "total_files": len(relative_paths),
        "counts": dict(sorted(counts.items())),
        "d2l_components": d2l_components,
        "likely_quiz_files": likely_quiz_files,
        "likely_html_topics": likely_html_topics,
        "all_files": relative_paths,
        "sample_files": relative_paths[:75],
    }


def markdown_report(inv: dict) -> str:
    lines = [
        f"# Export Inventory — {inv['label']}",
        "",
        f"- Source: `{inv['source']}`",
        f"- Total files: **{inv['total_files']}**",
        "",
        "## Counts",
        "",
    ]
    for key, value in inv["counts"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Recognized D2L components", ""])
    if inv["d2l_components"]:
        lines.extend(f"- `{p}`" for p in inv["d2l_components"])
    else:
        lines.append("- none recognized")
    lines.extend(["", "## Likely quiz-related files", ""])
    if inv["likely_quiz_files"]:
        lines.extend(f"- `{p}`" for p in inv["likely_quiz_files"])
    else:
        lines.append("- none found")
    lines.extend(["", "## Likely HTML topic files", ""])
    if inv["likely_html_topics"]:
        lines.extend(f"- `{p}`" for p in inv["likely_html_topics"])
    else:
        lines.append("- none found")
    lines.extend(["", "## All files", ""])
    lines.extend(f"- `{p}`" for p in inv["all_files"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a lightweight inventory of a Brightspace export ZIP or folder.")
    parser.add_argument("source", help="Path to a ZIP or unpacked folder")
    parser.add_argument("--output-dir", default=None, help="Optional directory for markdown/json outputs")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"Source does not exist: {source}", file=sys.stderr)
        return 2

    if source.is_dir():
        rel_paths = normalize_paths_from_folder(source)
    else:
        rel_paths = normalize_paths_from_zip(source)

    inv = build_inventory(source, rel_paths)
    md = markdown_report(inv)

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = course_name(source)
        (output_dir / f"{stem}__inventory.json").write_text(json.dumps(inv, indent=2), encoding="utf-8")
        (output_dir / f"{stem}__inventory.md").write_text(md, encoding="utf-8")

    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
