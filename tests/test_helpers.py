"""Unit tests for the small shared helpers the pipeline scripts rely on."""
from __future__ import annotations

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
