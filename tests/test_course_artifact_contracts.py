from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import zipfile

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.course_artifact_contracts import (
    add_entity_identity,
    annotate_activity_payload,
    build_run_identity,
    build_source_identity,
    logical_file_set_fingerprint,
    new_run_id,
    validate_contract,
    verify_run_identity,
)


EXAMPLE_ROOT = REPO_ROOT / "schemas" / "examples"
SCHEMA_ROOT = REPO_ROOT / "schemas"


def write_source(root: Path, *, lineage: bool = True, body: str = "alpha") -> Path:
    root.mkdir(parents=True)
    (root / "imsmanifest.xml").write_text(
        f"<manifest identifier='M1'><metadata>{body}</metadata></manifest>\n",
        encoding="utf-8",
    )
    if lineage:
        orgunit = root / "orgunitconfig"
        orgunit.mkdir()
        (orgunit / "orgunitconfig.xml").write_text(
            "<orgunit identifier='stable-course-lineage'><code>EXAMPLE-101</code></orgunit>\n",
            encoding="utf-8",
        )
    return root


def contract_errors(payload: dict) -> list[str]:
    return [issue.render() for issue in validate_contract(payload) if issue.severity == "error"]


def test_schemas_are_valid_and_shared_definitions_stay_coherent() -> None:
    schemas = {
        name: json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
        for name in ("activities_schema.json", "structure_schema.json", "run_identity_schema.json")
    }
    for schema in schemas.values():
        jsonschema.Draft7Validator.check_schema(schema)

    definitions = [schema["definitions"] for schema in schemas.values()]
    assert definitions[0]["fingerprint"] == definitions[1]["fingerprint"] == definitions[2]["fingerprint"]
    assert definitions[0]["sourceIdentity"] == definitions[1]["sourceIdentity"] == definitions[2]["sourceIdentity"]
    assert definitions[0]["identity"] == definitions[1]["identity"]


def test_examples_validate_and_preserve_unseen_shapes() -> None:
    payloads = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(EXAMPLE_ROOT.glob("*.json"))
    }

    assert payloads
    for name, payload in payloads.items():
        assert contract_errors(payload) == [], name

    activities = payloads["unseen_activity_shape.example.json"]
    assert activities["quizzes"][0]["kind"] == "adaptive_scenario"
    assert activities["future_typed_activity_collection"][0]["source_kind"] == "FutureEvidenceObject"
    structure = payloads["unseen_structure_shape.example.json"]
    assert structure["tree"][0]["children"][0]["kind"] == "vendor_interactive_container"


def test_required_identity_envelope_fails_loudly() -> None:
    payload = json.loads(
        (EXAMPLE_ROOT / "unseen_activity_shape.example.json").read_text(encoding="utf-8")
    )
    broken = copy.deepcopy(payload)
    del broken["quizzes"][0]["entity_key"]

    errors = contract_errors(broken)
    assert any("entity_key" in error for error in errors)


def test_logical_fingerprint_matches_folder_and_zip_transport(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source")
    zip_path = tmp_path / "same-source.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())

    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extracted)

    folder_identity = build_source_identity(source, source)
    zip_identity = build_source_identity(zip_path, extracted)

    assert folder_identity["logical_fingerprint"] == zip_identity["logical_fingerprint"]
    assert folder_identity["source_instance_key"] == zip_identity["source_instance_key"]
    assert folder_identity["source_lineage_key"] == zip_identity["source_lineage_key"]
    assert folder_identity["transport_fingerprint"] is None
    assert zip_identity["transport_fingerprint"]["scope"] == "transport_file"


def test_refresh_identity_separates_lineage_from_exact_source(tmp_path: Path) -> None:
    first = write_source(tmp_path / "first")
    refreshed = tmp_path / "refreshed"
    shutil.copytree(first, refreshed)
    (refreshed / "imsmanifest.xml").write_text(
        "<manifest identifier='M1'><metadata>changed</metadata></manifest>\n",
        encoding="utf-8",
    )

    first_identity = build_source_identity(first, first)
    refreshed_identity = build_source_identity(refreshed, refreshed)

    assert first_identity["lineage_state"] == "resolved"
    assert first_identity["source_lineage_key"] == refreshed_identity["source_lineage_key"]
    assert first_identity["source_instance_key"] != refreshed_identity["source_instance_key"]


def test_unresolved_lineage_never_guesses_across_changed_sources(tmp_path: Path) -> None:
    first = write_source(tmp_path / "renamed-snapshot-a", lineage=False, body="alpha")
    second = write_source(tmp_path / "renamed-snapshot-b", lineage=False, body="beta")

    first_identity = build_source_identity(first, first)
    second_identity = build_source_identity(second, second)

    assert first_identity["lineage_state"] == "unresolved"
    assert second_identity["lineage_state"] == "unresolved"
    assert first_identity["source_lineage_key"] != second_identity["source_lineage_key"]


def test_entity_key_is_stable_across_refresh_when_alias_is_stable(tmp_path: Path) -> None:
    first = write_source(tmp_path / "first")
    refreshed = tmp_path / "refreshed"
    shutil.copytree(first, refreshed)
    (refreshed / "imsmanifest.xml").write_text("<manifest identifier='M2'/>\n", encoding="utf-8")
    first_source = build_source_identity(first, first)
    refreshed_source = build_source_identity(refreshed, refreshed)

    first_record = {"resource_code": "CODE-ACTIVITY-1", "title": "Old title"}
    refreshed_record = {"resource_code": "CODE-ACTIVITY-1", "title": "New title"}
    add_entity_identity(
        first_record,
        source=first_source,
        entity_kind="assignment",
        aliases=("resource_code", "id"),
        fallback_value="Old title",
    )
    add_entity_identity(
        refreshed_record,
        source=refreshed_source,
        entity_kind="assignment",
        aliases=("resource_code", "id"),
        fallback_value="New title",
    )

    assert first_record["entity_key"] == refreshed_record["entity_key"]
    assert first_record["identity"]["quality"] == "durable_source_id"


def test_relationship_key_and_endpoints_ignore_unrelated_join_insertion(tmp_path: Path) -> None:
    source_root = write_source(tmp_path / "source")
    source = build_source_identity(source_root, source_root)
    base = {
        "source": source,
        "condition_sets": [],
        "dropbox_folders": [{"resource_code": "ASSIGN-1", "name": "Assignment"}],
        "discussions": [],
        "checklists": [],
        "quizzes": [],
        "grade_items": [{"resource_code": "GRADE-1", "name": "Grade"}],
        "joins": [
            {
                "source_kind": "dropbox_folder",
                "source_code": "ASSIGN-1",
                "join_type": "grade_item",
                "target": "GRADE-1",
                "resolved": "yes",
            }
        ],
    }
    first = annotate_activity_payload(copy.deepcopy(base))
    with_unrelated = copy.deepcopy(base)
    with_unrelated["joins"].insert(
        0,
        {
            "source_kind": "grade_item",
            "source_code": "GRADE-1",
            "join_type": "linked_activity",
            "target": "none",
            "resolved": "none",
        },
    )
    second = annotate_activity_payload(with_unrelated)

    first_join = first["joins"][0]
    second_join = second["joins"][1]
    assert first_join["relationship_key"] == second_join["relationship_key"]
    assert first_join["source_entity_key"] == first["dropbox_folders"][0]["entity_key"]
    assert first_join["target_entity_key"] == first["grade_items"][0]["entity_key"]
    assert first_join["identity"]["quality"] == "resolved_endpoints"


def test_run_receipt_hashes_artifacts_and_detects_tampering(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source")
    source_identity = build_source_identity(source, source)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    artifact = bundle / "example__course_activities.json"
    artifact.write_text("{}\n", encoding="utf-8")
    receipt_name = "example__run_identity.json"

    receipt = build_run_identity(
        run_id=new_run_id(),
        source=source_identity,
        bundle_dir=bundle,
        receipt_name=receipt_name,
        started_at="2026-07-15T20:00:00Z",
        steps=[
            {
                "name": "course_activities",
                "status": "completed",
                "started_at": None,
                "finished_at": None,
                "artifact_paths": [artifact.name],
                "diagnostic_ids": [],
                "notes": [],
                "extensions": {},
            }
        ],
        parameters={"docx_requested": False},
        contract_by_name={artifact.name: "coursecraft.activities/1"},
    )

    assert contract_errors(receipt) == []
    assert [row["path"] for row in receipt["emitted_files"]] == [artifact.name]
    assert verify_run_identity(receipt, bundle) == []
    artifact.write_text("changed\n", encoding="utf-8")
    assert "checksum mismatch" in verify_run_identity(receipt, bundle)[0]


def test_logical_fingerprint_skips_symlinks(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source")
    outside = tmp_path / "outside.txt"
    outside.write_text("not part of the package", encoding="utf-8")
    (source / "outside-link.txt").symlink_to(outside)

    fingerprint = logical_file_set_fingerprint(source)

    assert fingerprint["extensions"]["skipped_symlink_count"] == 1
    assert fingerprint["file_count"] == 2
