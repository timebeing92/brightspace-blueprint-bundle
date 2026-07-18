from __future__ import annotations

import hashlib
import importlib.util
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "make_release_asset.py"
SPEC = importlib.util.spec_from_file_location("make_release_asset", MODULE_PATH)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class BundleReleaseAssetTests(unittest.TestCase):
    def test_remote_normalization_removes_credentials(self) -> None:
        self.assertEqual(
            release.normalized_remote("https://token@github.com/example/repo.git"),
            "https://github.com/example/repo.git",
        )

    def test_normalized_archive_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_text("alpha\n", encoding="utf-8")
            (source / "nested").mkdir()
            (source / "nested" / "b.txt").write_text("beta\n", encoding="utf-8")
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            release.normalized_tar_gz(source, first, "release")
            release.normalized_tar_gz(source, second, "release")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as tf:
                self.assertIn("release/nested/b.txt", tf.getnames())

    def test_contract_receipt_records_schema_ids_and_hashes(self) -> None:
        rows = release.contract_receipt(ROOT)
        self.assertEqual(
            [row["schema"] for row in rows],
            [
                "coursecraft.activities/1",
                "coursecraft.blueprint/4",
                "coursecraft.run/1",
                "coursecraft.rubrics/1",
                "coursecraft.structure/1",
                "coursecraft.progress/1",
            ],
        )
        for row in rows:
            expected = hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
            self.assertEqual(row["sha256"], expected)

    def test_runtime_receipt_and_linked_syllabus_capability(self) -> None:
        rows = release.runtime_receipt(ROOT)
        self.assertEqual(
            [row["path"] for row in rows],
            [
                "scripts/build_blueprint_bundle.py",
                "scripts/reconstruct_course_structure.py",
            ],
        )
        for row in rows:
            expected = hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
            self.assertEqual(row["sha256"], expected)

        capability = release.release_capabilities(ROOT)[
            "linked_syllabus_supplement"
        ]
        self.assertEqual(capability["status"], "enabled_by_default")
        self.assertEqual(capability["primary_authority"], "package_local_export")

    def test_linked_syllabus_capability_requires_runtime_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in release.RUNTIME_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "lacks linked-syllabus"):
                release.release_capabilities(root)


if __name__ == "__main__":
    unittest.main()
