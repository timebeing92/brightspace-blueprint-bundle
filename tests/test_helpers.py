"""Unit tests for the small shared helpers the pipeline scripts rely on."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_blueprint_bundle as bbb  # noqa: E402
import extract_course_activities as eca  # noqa: E402
import reconstruct_course_structure as rcs  # noqa: E402


class TestSafeLabel:
    def test_replaces_disallowed_runs_with_single_underscore(self):
        assert rcs.safe_label("BUMG 660: Org Behavior!") == "BUMG_660_Org_Behavior"

    def test_keeps_dots_dashes_underscores(self):
        assert rcs.safe_label("course-1.2_final") == "course-1.2_final"

    def test_empty_and_symbol_only_fall_back_to_export(self):
        assert rcs.safe_label("") == "export"
        assert rcs.safe_label("!!!") == "export"

    def test_copies_agree_across_scripts(self):
        for sample in ("A B", "", "weird//name", "ok_name-1.0"):
            assert rcs.safe_label(sample) == bbb.safe_label(sample)


class TestHtmlToText:
    def test_strips_tags_and_collapses_whitespace(self):
        raw = "<p>Hello   <b>world</b></p>\n<p>again</p>"
        assert rcs.html_to_text(raw) == "Hello world again"

    def test_unescapes_entities(self):
        assert rcs.html_to_text("Fish &amp; Chips &gt; soup") == "Fish & Chips > soup"

    def test_structure_variant_strips_script_and_style_content(self):
        raw = "<style>p{color:red}</style><p>Visible</p><script>alert('x')</script>"
        assert rcs.html_to_text(raw) == "Visible"

    def test_activities_variant_strips_script_and_style_content(self):
        # Reconciled behavior: both html_to_text implementations must drop
        # script/style payloads, not surface them as course text.
        raw = "<style>p{color:red}</style><p>Visible</p><script>alert('x')</script>"
        assert eca.html_to_text(raw) == "Visible"

    def test_nested_inline_heading_markup_survives_segmentation_and_routing(self):
        raw = """
        <h2><strong>Student Learning Outcomes:</strong></h2>
        <ul><li>Explore the literature related to the proposed study.</li></ul>
        <h2><span>Learning Materials</span></h2>
        <p>Read the assigned chapter.</p>
        """

        segments = rcs.html_to_segments(raw)

        assert [(row["heading"], row["level"]) for row in segments] == [
            ("Student Learning Outcomes:", 2),
            ("Learning Materials", 2),
        ]
        routed = list(
            bbb.route_topic(
                {
                    "manifest_title": "Week 1 Overview & Course Materials",
                    "html_title": "Week 1",
                    "href": "Week 1.html",
                    "body_segments": segments,
                }
            )
        )
        assert [row["bucket"] for row in routed] == ["objectives", "resources"]
        assert rcs.blocks_to_text(routed[0]["blocks"]) == (
            "Explore the literature related to the proposed study."
        )

    def test_linked_syllabus_supplements_missing_fields_but_export_stays_primary(
        self, tmp_path, monkeypatch
    ):
        syllabus_html = b"""<html><body>
        <h2><strong>Description</strong></h2>
        <p>Supplemental linked-syllabus description.</p>
        <h2>Materials</h2><h3><span>Required:</span></h3>
        <ul><li>One required text.</li></ul>
        <h2>Learning Objectives and Outcomes</h2>
        <h4><strong>Course Outcomes:</strong></h4>
        <ul><li>Analyze evidence from multiple sources.</li></ul>
        </body></html>"""

        class FakeResponse:
            status = 200
            headers = {"Content-Type": "text/html; charset=UTF-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return syllabus_html

        monkeypatch.setattr(rcs, "urlopen", lambda *_args, **_kwargs: FakeResponse())
        nodes = [
            {
                "title": "Welcome, Syllabus, and Getting Started",
                "identifier": "MODULE-1",
                "identifierref": "RES-MODULE-1",
                "href": "",
                "is_hidden": False,
                "children": [
                    {
                        "title": "Course Syllabus",
                        "identifier": "ITEM-SYLLABUS",
                        "identifierref": "RES-SYLLABUS",
                        "href": "https://syllabi.une.edu/example/course/",
                        "is_hidden": False,
                        "children": [],
                    }
                ],
            }
        ]
        diagnostics = []
        references = rcs.collect_syllabus_supplements(
            nodes,
            output_dir=tmp_path,
            stem="fixture__course_structure",
            fetch_enabled=True,
            timeout=1,
            allowed_hosts={"syllabi.une.edu"},
            diagnostics=diagnostics,
        )
        assert references[0]["sha256"] == hashlib.sha256(syllabus_html).hexdigest()
        assert set(references[0]["front_matter"]) == {
            "course_description",
            "required_materials",
            "course_learning_outcomes",
        }
        assert (tmp_path / references[0]["artifact_path"]).read_bytes() == syllabus_html

        structure_payload = {
            "tree": [],
            "html_topics": [],
            "diagnostics": diagnostics,
            "extensions": {"syllabus_references": references},
        }
        model = bbb.build_blueprint_model(
            structure_payload,
            {},
            label="fixture",
            course_number="COURSE 101",
            course_title="Fixture Course",
            term="Fall 2026",
            template_reference="test",
        )
        assert "Supplemental linked-syllabus description" in bbb._blocks_text(
            model["front_matter"]["course_description"]
        )
        assert any("supplemented verbatim" in note for note in model["diagnostics"])

        primary = [{"kind": "p", "level": 0, "runs": [{"text": "Primary export text.", "href": ""}]}]
        structure_payload["html_topics"] = [
            {
                "manifest_title": "Course Overview",
                "html_title": "Course Overview",
                "body_segments": [
                    {"heading": "Description", "level": 2, "blocks": primary}
                ],
            }
        ]
        primary_model = bbb.build_blueprint_model(
            structure_payload,
            {},
            label="fixture",
            course_number="COURSE 101",
            course_title="Fixture Course",
            term="Fall 2026",
            template_reference="test",
        )
        assert primary_model["front_matter"]["course_description"] == primary
        assert any(
            "package-local content retained as primary" in note
            for note in primary_model["diagnostics"]
        )

    def test_nested_package_html_syllabus_link_is_discovered_and_fetched(
        self, tmp_path, monkeypatch
    ):
        syllabus_html = b"""<html><body>
        <h2>Course Description</h2><p>Nested-link description.</p>
        <h2>Course Outcomes</h2><ul><li>Interpret nested evidence.</li></ul>
        </body></html>"""

        class FakeResponse:
            status = 200
            headers = {"Content-Type": "text/html; charset=UTF-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return syllabus_html

        monkeypatch.setattr(rcs, "urlopen", lambda *_args, **_kwargs: FakeResponse())
        nodes = [
            {
                "title": "Welcome and Getting Started",
                "identifier": "MODULE-1",
                "identifierref": "RES-MODULE-1",
                "href": "",
                "is_hidden": False,
                "children": [
                    {
                        "title": "Course Resources and Student Resources",
                        "identifier": "ITEM-RESOURCES",
                        "identifierref": "RES-RESOURCES",
                        "href": "Course Resources.html",
                        "is_hidden": False,
                        "children": [],
                    }
                ],
            }
        ]
        html_topics = [
            {
                "manifest_title": "Course Resources and Student Resources",
                "href": "Course Resources.html",
                "body_segments": [
                    {
                        "heading": "Course Resources",
                        "blocks": [
                            {
                                "kind": "p",
                                "runs": [
                                    {
                                        "text": "EDU 615 Syllabus and Schedule",
                                        "href": "https://syllabi.une.edu/edu/edu-615/",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        diagnostics = []

        references = rcs.collect_syllabus_supplements(
            nodes,
            html_topics=html_topics,
            output_dir=tmp_path,
            stem="fixture__course_structure",
            fetch_enabled=True,
            timeout=1,
            allowed_hosts={"syllabi.une.edu"},
            diagnostics=diagnostics,
        )

        assert len(references) == 1
        reference = references[0]
        assert reference["discovery"] == "package_html_link"
        assert reference["container_href"] == "Course Resources.html"
        assert reference["manifest_item_identifier"] == "ITEM-RESOURCES"
        assert reference["manifest_path"] == (
            "Welcome and Getting Started > Course Resources and Student Resources > "
            "EDU 615 Syllabus and Schedule"
        )
        assert reference["fetch_status"] == "fetched"
        assert set(reference["front_matter"]) == {
            "course_description",
            "course_learning_outcomes",
        }

    def test_linked_syllabus_fetch_failure_remains_diagnostic(
        self, tmp_path, monkeypatch
    ):
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("fixture unexpected shape")

        monkeypatch.setattr(rcs, "urlopen", unavailable)
        diagnostics = []
        references = rcs.collect_syllabus_supplements(
            [
                {
                    "title": "Course Syllabus",
                    "identifier": "ITEM-SYLLABUS",
                    "identifierref": "RES-SYLLABUS",
                    "href": "https://syllabi.une.edu/example/course/",
                    "is_hidden": False,
                    "children": [],
                }
            ],
            output_dir=tmp_path,
            stem="fixture__course_structure",
            fetch_enabled=True,
            timeout=1,
            allowed_hosts={"syllabi.une.edu"},
            diagnostics=diagnostics,
        )

        assert references[0]["fetch_status"] == "fetch_error"
        assert references[0]["front_matter"] == {}
        assert "Unexpected linked-syllabus RuntimeError" in references[0]["diagnostics"][0]
        assert any("fetch_error" in note for note in diagnostics)


class TestXmlSafeText:
    def test_keeps_ordinary_text_and_newlines(self):
        assert bbb.xml_safe_text("line1\nline2\tend") == "line1\nline2\tend"

    def test_replaces_control_chars_with_spaces(self):
        assert bbb.xml_safe_text("bad\x00char\x0b") == "bad char "

    def test_none_becomes_empty(self):
        assert bbb.xml_safe_text(None) == ""


class TestCleanTextAndLabel:
    def test_clean_text_collapses_to_single_line(self):
        assert bbb.clean_text("  a\n\n  b\tc ") == "a b c"

    def test_clean_label_drops_trailing_colon(self):
        assert bbb.clean_label("Learning Objectives:  ") == "Learning Objectives"


class TestImagesMissingAlt:
    def test_counts_only_images_without_meaningful_alt(self):
        html = (
            '<img src="a.png" alt="A diagram">'
            '<img src="b.png">'
            '<img src="c.png" alt="">'
        )
        assert eca.images_missing_alt(html) == 2
