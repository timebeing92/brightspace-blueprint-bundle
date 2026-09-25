"""Synthetic run/2 evidence and compatibility regressions; no live intake."""
import copy
import json
from pathlib import Path
import sys

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import course_artifact_contracts as contracts
import build_blueprint_bundle as blueprint
import reconstruct_course_structure as structure


def package(root, *, manifest="D2L_123", uuid="export-a", title="TST 101 Template"):
    root.mkdir()
    (root / "orgunitconfig").mkdir()
    (root / "orgunitconfig/orgunitconfig.xml").write_text(
        f'<orgunit identifier="{uuid}"><code>TST-101</code></orgunit>'
    )
    (root / "imsmanifest.xml").write_text(
        f'<manifest xmlns="urn:ims" identifier="{manifest}"><metadata><lom xmlns="urn:lom">'
        f'<general><title><langstring>{title}</langstring></title>'
        '<keyword><langstring>TST-101-legacy</langstring></keyword></general></lom></metadata>'
        '<organizations><organization><item><title>Not the course title</title></item></organization></organizations></manifest>'
    )
    return root


def source(root, **kwargs):
    p = package(root, **kwargs)
    return contracts.build_source_identity(p, p)


def test_uuid_rotation_retains_lineage_and_all_observed_bases(tmp_path):
    a = source(tmp_path / "a")
    b = source(tmp_path / "b", uuid="export-b")
    assert a["source_instance_key"] != b["source_instance_key"]
    assert a["source_lineage_key"] == b["source_lineage_key"]
    assert a["lineage_basis"] == "org_unit_id+manifest_identifier"
    assert a["observed_identity"]["manifest_title"] == "TST 101 Template"
    assert a["observed_identity"]["organization_title"] is None
    classes = {row["basis"]: row["stability_class"] for row in a["lineage_candidates"]}
    assert classes == {"orgunit_identifier": "export_instance_alias", "org_unit_id": "course_identifier", "manifest_identifier": "package_lineage_identifier", "orgunit_code": "mutable_label", "manifest_course_code": "mutable_label"}
    assert contracts.verify_source_identity_v2(a) == []


def test_changed_org_unit_or_manifest_changes_primary_key(tmp_path):
    a = source(tmp_path / "a")
    b = source(tmp_path / "b", manifest="D2L_124")
    p = package(tmp_path / "D2LExport_123_TST-101_20260925", manifest="OTHER-MANIFEST")
    c = contracts.build_source_identity(p, p)
    assert len({row["source_lineage_key"] for row in (a, b, c)}) == 3
    assert all(row["lineage_state"] == "resolved" for row in (a, b, c))


@pytest.mark.parametrize("name,manifest,diagnostic", [
    ("renamed", "M1", "primary_identifiers_incomplete"),
    ("D2LExport_999_TST-101_20260925", "D2L_123", "numeric_org_unit_conflicts_with_manifest"),
])
def test_weak_or_conflicting_evidence_stays_unresolved(tmp_path, name, manifest, diagnostic):
    identity = source(tmp_path / name, manifest=manifest)
    assert identity["lineage_state"] == "unresolved"
    assert identity["lineage_basis"] == "logical_fingerprint"
    assert identity["lineage_diagnostics"] == [diagnostic]
    assert contracts.verify_source_identity_v2(identity) == []


def test_partial_old_observation_cannot_erase_package_evidence(tmp_path):
    p = package(tmp_path / "renamed")
    identity = contracts.build_source_identity(p, p, observed_identity={"org_unit_id": None, "orgunit_code": "TST-101"})
    assert identity["observed_identity"]["org_unit_id"] == "123"
    assert identity["observed_identity"]["manifest_identifier"] == "D2L_123"
    assert identity["lineage_state"] == "resolved"


def test_run2_schema_and_semantic_validation_leave_run1_intact(tmp_path):
    identity = source(tmp_path / "source")
    receipt = contracts.build_run_identity(
        schema_id="coursecraft.run/2", run_id=contracts.new_run_id(), source=identity,
        bundle_dir=tmp_path, receipt_name="run.json", started_at=None, steps=[],
        parameters={"linked_syllabus_fetch_requested": True}, contract_by_name={},
    )
    schema = json.loads(contracts.SCHEMA_REGISTRY["coursecraft.run/2"].read_text())
    jsonschema.Draft7Validator.check_schema(schema)
    assert contracts.validate_contract(receipt) == []
    altered = copy.deepcopy(receipt)
    altered["source"]["source_lineage_key"] = "cc:lineage:forged"
    assert any(row.code == "source_identity" for row in contracts.validate_contract(altered))
    altered = copy.deepcopy(receipt)
    altered["source"]["lineage_candidates"].pop()
    assert any(row.code == "source_identity" for row in contracts.validate_contract(altered))
    legacy = json.loads((contracts.SCHEMA_ROOT / "examples/run_identity.example.json").read_text())
    assert contracts.validate_contract(legacy) == []


def title_structure(package_title="TST 101 Template", headings=("TST 101: Evidence Design – Summer A 2026",)):
    candidates = structure.extract_syllabus_title_candidates([{"heading": heading} for heading in headings])
    return {
        "source": {"observed_identity": {"manifest_title": package_title, "course_code": "TST-101-old"}},
        "extensions": {"syllabus_references": [{"fetch_status": "fetched", "is_hidden": False,
            "url": "https://example.org/syllabus", "sha256": "a" * 64, "artifact_path": "syllabus/source.html",
            "course_title_candidates": candidates}]},
    }


def select(payload, explicit=""):
    return blueprint.select_course_title(payload, explicit=explicit, label="tst_101", course_number="TST 501")


def test_syllabus_title_fills_placeholder_with_provenance_and_no_session_layer():
    result = select(title_structure())
    assert result["selected"]["value"] == "Evidence Design"
    assert result["selected"]["basis"] == "supplemental_linked_syllabus"
    assert result["selected"]["source_heading"] == "TST 101: Evidence Design – Summer A 2026"
    assert result["selected"]["excluded_session_suffix"] == " – Summer A 2026"
    assert result["package_candidates"][0]["value"] == "TST 101 Template"
    assert "term" not in result


def test_package_and_explicit_title_precedence():
    payload = title_structure("Package Evidence Design")
    assert select(payload)["selected"]["value"] == "Package Evidence Design"
    assert "package_syllabus_title_difference" in select(payload)["diagnostics"]
    assert select(payload, "Operator Title")["selected"]["basis"] == "explicit_course_title"


@pytest.mark.parametrize("status", ["inventory_only", "fetch_error", "skipped_hidden"])
def test_failed_or_disabled_fetch_keeps_package_title(status):
    payload = title_structure()
    payload["extensions"]["syllabus_references"][0]["fetch_status"] = status
    assert select(payload)["selected"]["basis"] == "manifest_title"


def test_wrong_course_hidden_and_conflicting_syllabus_titles_do_not_override():
    assert select(title_structure(headings=("TST 999: Wrong Course",)))["selected"]["basis"] == "manifest_title"
    payload = title_structure()
    payload["extensions"]["syllabus_references"][0]["is_hidden"] = True
    assert select(payload)["selected"]["basis"] == "manifest_title"
    result = select(title_structure(headings=("TST 101: Alpha", "TST 101: Beta")))
    assert result["selected"]["basis"] == "manifest_title"
    assert result["diagnostics"] == ["conflicting_syllabus_titles"]


def test_no_metadata_retains_label_fallback():
    assert select({})["selected"] == {"value": "tst 101", "basis": "label_fallback"}


def test_later_course_coded_assignment_heading_is_not_a_course_title():
    candidates = structure.extract_syllabus_title_candidates([
        {"heading": "TST 101 Evidence Design – Spring 2026", "level": 2},
        {"heading": "Description", "level": 2},
        {"heading": "TST 101 Course Project", "level": 3},
    ])
    assert len(candidates) == 1
    assert candidates[0]["title"] == "Evidence Design"


def test_promoted_cli_title_evidence_and_fetch_off(tmp_path):
    import subprocess
    package_root = package(tmp_path / "cli-source", title="Package Evidence Design")
    output = tmp_path / "bundle"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_blueprint_bundle.py"),
         str(package_root), "--bundle-dir", str(output), "--label", "fixture",
         "--no-syllabus-fetch", "--no-docx", "--skip-qa", "--quiet"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((output / "fixture__run_identity.json").read_text())
    model = json.loads((output / "fixture__blueprint.json").read_text())
    assert receipt["schema"] == "coursecraft.run/2"
    assert model["course_title"] == "Package Evidence Design"
    assert receipt["parameters"]["course_title_evidence"]["selected"] == {
        "value": "Package Evidence Design", "basis": "manifest_title",
    }
    assert receipt["parameters"]["linked_syllabus_fetch_requested"] is False
    assert contracts.validate_contract(receipt) == []
