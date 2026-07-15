"""docx_structure_qa.py: passes on the worked example, catches real damage."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from conftest import BUNDLE_ROOT, EXPECTED_BUNDLE

SCRIPT = BUNDLE_ROOT / "scripts" / "docx_structure_qa.py"
GOLDEN_DOCX = EXPECTED_BUNDLE / "sample_course__blueprint.docx"
GOLDEN_MODEL = EXPECTED_BUNDLE / "sample_course__blueprint.json"
GOLDEN_RUBRICS = EXPECTED_BUNDLE / "sample_course__rubrics.json"


def run_qa(docx: Path, model: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(docx), "--model", str(model), *extra],
        cwd=BUNDLE_ROOT / "scripts",
        text=True,
        capture_output=True,
        check=False,
    )


def test_golden_docx_passes(tmp_path: Path) -> None:
    result = run_qa(
        GOLDEN_DOCX,
        GOLDEN_MODEL,
        "--rubrics-json", str(GOLDEN_RUBRICS),
        "--output-dir", str(tmp_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "sample_course__docx_structure.json").read_text())
    assert report["breaks"] == []
    assert report["warnings"] == []
    assert report["stats"]["hyperlinks"] == report["stats"]["model_live_links"]


def test_dangling_hyperlink_relationship_breaks(tmp_path: Path) -> None:
    broken = tmp_path / "broken.docx"
    with zipfile.ZipFile(GOLDEN_DOCX) as zin, zipfile.ZipFile(broken, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/_rels/document.xml.rels":
                text = data.decode("utf-8")
                text = re.sub(r"<Relationship [^>]*hyperlink[^>]*/>", "", text, count=1)
                data = text.encode("utf-8")
            zout.writestr(item, data)

    result = run_qa(broken, GOLDEN_MODEL)
    assert result.returncode == 1
    assert "Dangling relationship reference" in result.stdout


def test_wrong_layout_warns_but_passes(tmp_path: Path) -> None:
    result = run_qa(
        GOLDEN_DOCX,
        GOLDEN_MODEL,
        "--rubrics-json", str(GOLDEN_RUBRICS),
        "--section-layout", "left",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode == 0
    report = json.loads((tmp_path / "sample_course__docx_structure.json").read_text())
    assert report["breaks"] == []
    assert any("shape" in warning for warning in report["warnings"])


def test_model_mismatch_breaks(tmp_path: Path) -> None:
    model = json.loads(GOLDEN_MODEL.read_text(encoding="utf-8"))
    model["weeks"][0]["title"] = "Week 99: Not In The Document"
    mismatched = tmp_path / "mismatched.json"
    mismatched.write_text(json.dumps(model), encoding="utf-8")

    result = run_qa(GOLDEN_DOCX, mismatched)
    assert result.returncode == 1
    assert "Week heading not found" in result.stdout
