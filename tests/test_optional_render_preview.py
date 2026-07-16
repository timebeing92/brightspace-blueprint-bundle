from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_visual_render_dependencies_are_not_core_requirements() -> None:
    core = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    optional = (ROOT / "requirements-render.txt").read_text(encoding="utf-8")

    assert "pdf2image" not in core
    assert "-r requirements.txt" in optional
    assert "pdf2image" in optional


def test_advanced_flag_is_retained_and_described_as_manual_preview() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/build_blueprint_bundle.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--render-docx-check" in result.stdout
    assert "Advanced maintainer preview" in result.stdout
    assert "human inspection" in result.stdout


def test_render_helper_does_not_claim_automatic_visual_validation() -> None:
    source = (ROOT / "scripts" / "render_blueprint_docx.py").read_text(
        encoding="utf-8"
    )

    assert "does not automatically detect clipping" in source
    assert "requirements-render.txt" in source
