#!/usr/bin/env python3
"""Render a generated blueprint DOCX to PDF and PNG pages for visual QA.

Requires LibreOffice/soffice plus the Python pdf2image package. pdf2image also
needs Poppler utilities available on PATH.

Usage:
    python3 scripts/render_blueprint_docx.py output/course__blueprint.docx
    python3 scripts/render_blueprint_docx.py output/course__blueprint.docx --output-dir render_qa --emit-pdf
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    mac_path = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return str(mac_path) if mac_path.exists() else None


def convert_docx_to_pdf(docx: Path, work_dir: Path, soffice: str) -> Path:
    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work_dir),
            str(docx),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    pdf = work_dir / f"{docx.stem}.pdf"
    if result.returncode != 0 or not pdf.exists():
        raise RuntimeError(
            "LibreOffice conversion failed.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}".strip()
        )
    return pdf


def render_pdf_pages(pdf: Path, output_dir: Path, dpi: int) -> list[Path]:
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise RuntimeError("pdf2image is not installed in this Python environment.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = convert_from_path(
        str(pdf),
        dpi=dpi,
        output_folder=str(output_dir),
        fmt="png",
        output_file=pdf.stem,
        paths_only=True,
    )
    return [Path(path) for path in paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("docx", type=Path, help="Generated blueprint DOCX to render")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for PNG pages and render_summary.json (default: <docx stem>__render_qa)",
    )
    parser.add_argument("--dpi", type=int, default=144, help="PNG render DPI")
    parser.add_argument("--emit-pdf", action="store_true", help="Also copy the intermediate PDF into output-dir")
    args = parser.parse_args(argv)

    docx = args.docx.expanduser().resolve()
    if not docx.exists():
        raise SystemExit(f"error: DOCX not found: {docx}")
    soffice = find_soffice()
    if not soffice:
        raise SystemExit("error: soffice/libreoffice not found on PATH or in /Applications/LibreOffice.app")

    output_dir = args.output_dir or docx.with_name(f"{docx.stem}__render_qa")
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="blueprint_docx_render_") as tmp:
        pdf = convert_docx_to_pdf(docx, Path(tmp), soffice)
        rendered_pages = render_pdf_pages(pdf, output_dir, args.dpi)
        emitted_pdf = None
        if args.emit_pdf:
            emitted_pdf = output_dir / pdf.name
            shutil.copy2(pdf, emitted_pdf)

    summary = {
        "docx": str(docx),
        "output_dir": str(output_dir),
        "page_count": len(rendered_pages),
        "png_pages": [path.name for path in rendered_pages],
        "pdf": emitted_pdf.name if emitted_pdf else "",
        "status": "ok",
    }
    summary_path = output_dir / "render_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"render status: ok")
    print(f"pages: {len(rendered_pages)}")
    print(f"output: {output_dir}")
    print(f"summary: {summary_path}")
    if emitted_pdf:
        print(f"pdf: {emitted_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
