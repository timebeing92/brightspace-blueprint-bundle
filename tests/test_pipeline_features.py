"""Feature tests: wrapped-folder exports and the NDJSON progress-event stream."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import zipfile

import jsonschema

from conftest import BUNDLE_ROOT, SAMPLE_ARGS, SAMPLE_EXPORT, SAMPLE_EXPORT_ARG, run_pipeline

sys.path.insert(0, str(BUNDLE_ROOT / "scripts"))
import build_blueprint_bundle as pipeline


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
    schema = json.loads(
        (BUNDLE_ROOT / "schemas" / "progress_events_schema.json").read_text(
            encoding="utf-8"
        )
    )
    for event in events:
        jsonschema.validate(event, schema)
    run_start = events[0]
    assert run_start["event"] == "run_start"
    assert run_start["schema"] == "coursecraft.progress/1"
    total = run_start["total"]
    assert len(run_start["steps"]) == total
    assert "Extract rubrics" in run_start["steps"]
    assert "Render rubrics DOCX" in run_start["steps"]

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
    assert run_end["outputs"]["rubrics_docx"], "rubric DOCX path missing from run_end outputs"
    assert run_end["outputs"]["status_report"], "pipeline status Markdown missing"
    assert run_end["outputs"]["status_json"], "pipeline status JSON missing"
    assert run_end["outputs"]["run_identity"], "run identity receipt missing"
    assert run_end["issues"] == []
    assert run_end["delivery"] == {"usable": True, "empty": False, "core_failures": []}
    assert run_end["summary"]["weeks"] == 2
    assert run_end["summary"]["rubrics"] == 1
    assert run_end["summary"]["qa"] == {"breaks": 0, "warnings": 2, "notes": 7}

    bundle = Path(run_end["bundle_dir"])
    receipt = json.loads(Path(run_end["outputs"]["run_identity"]).read_text(encoding="utf-8"))
    jsonschema.validate(
        receipt,
        json.loads((BUNDLE_ROOT / "schemas" / "run_identity_schema.json").read_text(encoding="utf-8")),
    )
    activities = json.loads((bundle / "sample_course__course_activities.json").read_text(encoding="utf-8"))
    structure = json.loads((bundle / "sample_course__course_structure.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == "coursecraft.run/1"
    assert receipt["status"] == "ok"
    assert receipt["run_id"] == activities["run_id"] == structure["run_id"]
    assert receipt["source"] == activities["source"] == structure["source"]
    assert {row["contract"] for row in receipt["emitted_files"]} >= {
        "coursecraft.activities/1",
        "coursecraft.structure/1",
        "coursecraft.blueprint/4",
        "coursecraft.rubrics/1",
    }

    # No human banners when events are on.
    assert "== [1/" not in proc.stdout


def test_docx_qa_failure_yields_partial_deliverable(
    tmp_path, monkeypatch, capsys
):
    original = pipeline.run_workbench_script

    def fail_docx_qa(script_name, args, quiet=False, timeout=None):
        if script_name == "docx_structure_qa.py":
            raise SystemExit("injected structural QA failure for recovery test")
        return original(script_name, args, quiet=quiet, timeout=timeout)

    monkeypatch.setattr(pipeline, "run_workbench_script", fail_docx_qa)
    monkeypatch.chdir(BUNDLE_ROOT)
    output_dir = tmp_path / "partial"
    code = pipeline.main(
        [
            str(SAMPLE_EXPORT_ARG),
            *SAMPLE_ARGS,
            "--output-dir", str(output_dir),
            "--progress-events",
        ]
    )

    assert code == 0
    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "event" in payload:
            events.append(payload)
    run_end = events[-1]
    assert run_end["event"] == "run_end"
    assert run_end["status"] == "partial"
    jsonschema.validate(
        run_end,
        json.loads(
            (BUNDLE_ROOT / "schemas" / "progress_events_schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert any(issue["step"] == "Check DOCX structure" for issue in run_end["issues"])
    # A failed DOCX check is not a core evidence step: the partial stays usable.
    assert run_end["delivery"]["usable"] is True
    assert run_end["delivery"]["core_failures"] == []
    receipt = json.loads(Path(run_end["outputs"]["run_identity"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "partial"
    assert any(
        step["name"] == "Check DOCX structure" and step["status"] == "failed"
        for step in receipt["steps"]
    )

    bundle = output_dir / "sample_course__blueprint_bundle"
    assert (bundle / "sample_course__blueprint.md").is_file()
    assert (bundle / "sample_course__blueprint.docx").is_file()
    status_report = bundle / "sample_course__pipeline_status.md"
    assert status_report.is_file()
    status_text = status_report.read_text(encoding="utf-8")
    assert "Status: PARTIAL" in status_text
    assert "Check DOCX structure" in status_text
    assert "injected structural QA failure" in status_text


def test_malformed_rubric_json_does_not_block_blueprint(
    tmp_path, monkeypatch, capsys
):
    original = pipeline.run_workbench_script

    def emit_malformed_rubrics(script_name, args, quiet=False, timeout=None):
        if script_name == "extract_rubrics_to_workbook.py":
            json_output = Path(args[args.index("--json-output") + 1])
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text("{not valid json", encoding="utf-8")
            return None
        return original(script_name, args, quiet=quiet, timeout=timeout)

    monkeypatch.setattr(
        pipeline,
        "run_workbench_script",
        emit_malformed_rubrics,
    )
    monkeypatch.chdir(BUNDLE_ROOT)
    output_dir = tmp_path / "malformed-rubrics"
    code = pipeline.main(
        [
            str(SAMPLE_EXPORT_ARG),
            *SAMPLE_ARGS,
            "--output-dir", str(output_dir),
            "--progress-events",
        ]
    )

    assert code == 0
    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "event" in payload:
            events.append(payload)
    run_end = events[-1]
    assert run_end["status"] == "partial"
    jsonschema.validate(
        run_end,
        json.loads(
            (BUNDLE_ROOT / "schemas" / "progress_events_schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert any(issue["step"] == "Extract rubrics" for issue in run_end["issues"])
    assert run_end["delivery"]["usable"] is True
    assert run_end["outputs"]["rubrics_json"] is None
    assert run_end["outputs"]["rubrics_unparsed"]
    receipt = json.loads(Path(run_end["outputs"]["run_identity"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "partial"
    assert any(
        step["name"] == "Extract rubrics" and step["status"] == "failed"
        for step in receipt["steps"]
    )

    bundle = output_dir / "sample_course__blueprint_bundle"
    assert (bundle / "sample_course__blueprint.md").is_file()
    assert (bundle / "sample_course__blueprint.docx").is_file()
    assert not (bundle / "sample_course__rubrics.docx").exists()
    assert (bundle / "sample_course__rubrics_unparsed.json").is_file()
    status_text = (bundle / "sample_course__pipeline_status.md").read_text(
        encoding="utf-8"
    )
    assert "Rubric JSON is malformed" in status_text


def test_unreadable_input_reports_unusable_delivery(tmp_path, monkeypatch, capsys):
    """Deliverables emitted for an unreadable source must say so: the run
    stays `partial` (documents exist) but `delivery.usable` is false and the
    failed core evidence steps are named."""
    monkeypatch.chdir(BUNDLE_ROOT)
    not_an_export = tmp_path / "notes.zip"
    not_an_export.write_text("this is not a zip archive", encoding="utf-8")
    output_dir = tmp_path / "unusable"
    code = pipeline.main(
        [
            str(not_an_export),
            "--label", "unusable_demo",
            "--output-dir", str(output_dir),
            "--progress-events",
        ]
    )

    assert code == 0
    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "event" in payload:
            events.append(payload)
    run_end = events[-1]
    assert run_end["event"] == "run_end"
    assert run_end["status"] == "partial"
    jsonschema.validate(
        run_end,
        json.loads(
            (BUNDLE_ROOT / "schemas" / "progress_events_schema.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    delivery = run_end["delivery"]
    assert delivery["usable"] is False
    assert delivery["empty"] is True
    assert "Probe manifest" in delivery["core_failures"]
    assert "Reconstruct course structure" in delivery["core_failures"]

    status_json = json.loads(
        Path(run_end["outputs"]["status_json"]).read_text(encoding="utf-8")
    )
    assert status_json["delivery"] == delivery
    status_text = Path(run_end["outputs"]["status_report"]).read_text(encoding="utf-8")
    assert "NOT USABLE" in status_text
