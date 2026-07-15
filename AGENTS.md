# Optional Contributor And Assistant Guide

This bundle converts a Brightspace/D2L course export into a flat-file course
blueprint for source-traceable review.

The bundle runs without AI. Normal users should follow `README.md` and run:

```bash
bash bootstrap.sh
bash run_blueprint.sh <export.zip|unpacked-dir> [options]
```

This file is for maintainers, human contributors, and optional coding
assistants working on the scripts or helping a user run the bundle. It is not a
runtime requirement, not an authorship statement, and not something the pipeline
uses.

## Non-Authoring Rule

Do not add AI-authorship or assistant-provenance notes to generated bundles or
source files. In particular, do not create or include assistant-vs-script
pipeline reports, script-improvement review memos, vendor-specific provenance
notes, or assistant activity logs in reviewer-facing output.

If the user asks what a human or assistant did versus what scripts did, answer
in the conversation or place that analysis in a clearly non-shared scratch
location. Do not include it in a reviewer-facing course bundle unless the user
explicitly asks for that file to be shared.

## Operating Mode

This is extraction and review, not instructional-content generation.

- Preserve extracted wording. Do not paraphrase or invent course content.
- Keep missing data visible as `Needs review` or `None found`.
- Distinguish extracted fact from inference when summarizing results.
- Work from copies of exports. Do not edit the only raw copy of an export.
- Default generated output belongs under `workspace/review/`.

## Current Runtime Flow

`run_blueprint.sh` calls `scripts/build_blueprint_bundle.py`, which runs:

1. `export_inventory.py`
2. `manifest_probe.py`
3. `reconstruct_course_structure.py --extract-html`
4. `extract_course_activities.py`
5. `extract_rubrics_to_workbook.py --json` when `rubrics_d2l.xml` is present
6. `course_qa_report.py` unless `--skip-qa` is used
7. model assembly into `coursecraft.blueprint/4`
8. Markdown rendering
9. DOCX rendering unless `--no-docx` is used; rubric JSON, when present, is
   appended as a Rubric Appendix in the blueprint DOCX
10. standalone rubric DOCX rendering unless `--no-docx` is used
11. structural DOCX QA unless `--skip-docx-structure-check` is used
12. optional DOCX visual render QA when `--render-docx-check` is used

External URLs are inventoried by default. Fetching/checking them requires
`--check-external-links`.

## Current Blueprint Behavior

- The default DOCX layout is `--docx-section-layout top`, using full-width
  stacked section rows. `--docx-section-layout left` is still supported.
- Weekly content order is Overview, Learning Objectives, Assigned Reading and
  Multimedia, Assignment(s) and Instructions, Discussion Board Prompts,
  Checklist, then Other course sections.
- Top-level pre-week pages render under `Before Week 1: Additional Resources
  and Information`.
- Duplicate pre-week material that also appears in Week 1 is suppressed with a
  routing diagnostic.
- Hidden/faculty-facing manifest items remain visible: hidden modules get a
  notice, hidden HTML bodies are extracted where readable, hidden non-HTML files
  are preserved as file references, and hidden tool links keep object type,
  title, and path where available.
- Manifest-linked non-HTML files render as attached-file references, not decoded
  body text.
- HTML images render as image placeholders with alt text when available, or a
  no-alt placeholder with source path when missing.
- Creator+ practice iframes that reference local `.practice.json` payloads
  render as compact practice metadata and source prompt/instruction summaries.
- Visual cues such as callout/note/card-like containers, styled highlight
  sections, dropdown summaries, media embeds, and horizontal rules are preserved
  as review cues. When source HTML wraps a full callout/card section, the
  generated Markdown/DOCX wraps the child content too. No attempt is made to
  reproduce Brightspace CSS pixel-for-pixel.
- Repeated low-value visual wrappers, such as timeline item/card containers, are
  suppressed so output does not become wrapper noise.
- QA reports include specific image-alt locations, package-scope diagnostics,
  front-matter source diagnostics, and optional external-link checking.

## Where To Look

- `README.md` - user-facing setup, run, output, and review instructions.
- `CHANGELOG.md` - implementation history.
- `knowledge/SCRIPT_PIPELINE_AND_PACKAGE_CONTEXT.md` - plain-language pipeline
  walkthrough plus technical dataflow.
- `knowledge/HOW_EXTRACTION_MAPS_TO_BLUEPRINT.md` - source-to-section mapping,
  joins, and known limitations.
- `knowledge/BRIGHTSPACE_PACKAGE_STRUCTURE_AND_IMPORT_NOTES.md` - Brightspace
  package shape, joins, and date caveats.
- `schemas/blueprint_schema.json` - model contract used by Markdown and DOCX.
- `examples/` - worked sample output and config example.

## Editing Rules

- Prefer existing script patterns and local helper functions.
- Keep Markdown and DOCX rendering aligned with the same JSON model.
- If model shape changes, update `schemas/blueprint_schema.json`,
  Markdown rendering, and `blueprint_to_docx.py` together.
- If an extractor change belongs upstream in `coursecraft_workbench`, call that
  out in the summary so it can be reconciled.
- Keep generated folders (`workspace/`, `output/`, render pages, `.venv/`) out
  of source commits unless the user explicitly asks otherwise.
- Do not move Learning Materials/Resources below assessments; they belong right
  after Learning Objectives.

## When Unsure

Surface ambiguity explicitly. A blueprint with honest `Needs review` markers is
correct; a confident-looking blueprint with invented content is not.
