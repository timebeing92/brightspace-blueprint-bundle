"""Shared fixtures: one full pipeline run over the committed sample export."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BUNDLE_ROOT / "scripts"
EXAMPLES_DIR = BUNDLE_ROOT / "examples"
SAMPLE_EXPORT = EXAMPLES_DIR / "sample_export.zip"
EXPECTED_BUNDLE = EXAMPLES_DIR / "sample_course__blueprint_bundle"

# The exact command documented in examples/README.md, minus the venv wrapper.
SAMPLE_ARGS = [
    "--label", "sample_course",
    "--course-number", "SAMPLE 100",
    "--course-title", "Sample Course",
    "--term", "Demo Term",
]


def run_pipeline(output_dir: Path, extra_args: list[str] | None = None,
                 export: Path = SAMPLE_EXPORT) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "build_blueprint_bundle.py"),
        str(export),
        *SAMPLE_ARGS,
        "--output-dir", str(output_dir),
        *(extra_args or []),
    ]
    return subprocess.run(
        cmd, cwd=BUNDLE_ROOT, capture_output=True, text=True, timeout=600, check=False
    )


@pytest.fixture(scope="session")
def golden_run(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    output_dir = tmp_path_factory.mktemp("golden_run")
    proc = run_pipeline(output_dir)
    return SimpleNamespace(
        proc=proc,
        bundle_dir=output_dir / "sample_course__blueprint_bundle",
    )
