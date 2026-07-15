"""Feature tests: wrapped-folder exports and the NDJSON progress-event stream."""
from __future__ import annotations

import json
import zipfile

from conftest import SAMPLE_EXPORT, run_pipeline


def make_wrapped_export(tmp_path):
    """Re-zip the sample export with everything under one wrapping folder —
    the common shape of a re-zipped download that used to kill the pipeline."""
    wrapped = tmp_path / "wrapped_export.zip"
    with zipfile.ZipFile(SAMPLE_EXPORT) as src, zipfile.ZipFile(wrapped, "w") as dst:
        for info in src.infolist():
            if info.is_dir():
                continue
            dst.writestr(f"course_download/{info.filename}", src.read(info.filename))
    return wrapped


def test_wrapped_export_still_builds(tmp_path):
    export = make_wrapped_export(tmp_path)
    output_dir = tmp_path / "out"
    proc = run_pipeline(output_dir, export=export)
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    bundle_dir = output_dir / "sample_course__blueprint_bundle"
    blueprint_md = bundle_dir / "sample_course__blueprint.md"
    assert blueprint_md.is_file()
    text = blueprint_md.read_text(encoding="utf-8")
    assert "Week 1" in text
    assert "found below the export root" in text  # surfaced as a diagnostic, not hidden


def test_default_run_prints_step_banners(golden_run):
    stdout = golden_run.proc.stdout
    assert "== [1/" in stdout
    assert "Assemble blueprint model and Markdown" in stdout


def test_progress_events_stream(tmp_path):
    output_dir = tmp_path / "out"
    proc = run_pipeline(output_dir, extra_args=["--progress-events"])
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    events = []
    for line in proc.stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "event" in payload:
            events.append(payload)

    assert events, "no NDJSON events found on stdout"
    run_start = events[0]
    assert run_start["event"] == "run_start"
    assert run_start["schema"] == "coursecraft.progress/1"
    total = run_start["total"]
    assert len(run_start["steps"]) == total
    assert "Extract rubrics" in run_start["steps"]

    starts = [e for e in events if e["event"] == "step_start"]
    ends = [e for e in events if e["event"] == "step_end"]
    assert len(starts) == total
    assert len(ends) == total
    assert all(e["status"] == "ok" for e in ends)

    run_end = events[-1]
    assert run_end["event"] == "run_end"
    assert run_end["status"] == "ok"
    assert run_end["bundle_dir"].endswith("sample_course__blueprint_bundle")
    assert run_end["outputs"]["docx"], "docx path missing from run_end outputs"
    assert run_end["outputs"]["rubrics_json"], "rubric JSON path missing from run_end outputs"
    assert run_end["outputs"]["rubrics_workbook"], "rubric workbook path missing from run_end outputs"
    assert run_end["summary"]["weeks"] == 2
    assert run_end["summary"]["rubrics"] == 1
    assert run_end["summary"]["qa"] == {"breaks": 0, "warnings": 2, "notes": 7}

    # No human banners when events are on.
    assert "== [1/" not in proc.stdout
