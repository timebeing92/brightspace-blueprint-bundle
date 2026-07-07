# Agent contract — Brightspace → Blueprint bundle

You are working inside a **self-contained bundle** that converts a Brightspace/D2L
course export into a flat-file course blueprint (Markdown + DOCX) shaped after
`reference/Course Blueprint Template 2020 CGPS.docx`.

> **⏭ Read `CHANGELOG.md` first.** As of 2026-07-06 the SME-facing blueprint
> architecture is: full-width stacked DOCX section headers, and weekly content
> ordered Overview → Learning Objectives → Learning Materials/Resources →
> Assignments → Discussions → Checklist → Other. Schema is
> `coursecraft.blueprint/3`.

## Operating mode

This is **extraction + review**, never generation of instructional content.
- Preserve extracted wording. Do not paraphrase or invent course content.
- Keep missing data visible (`Needs review` / `None found`). Do not fill gaps
  with plausible text.
- Distinguish extracted fact from inference when you summarize for the user.
- Never edit the only raw copy of an export. Work from copies; outputs go under
  `output/`.

## How to run

```bash
bash bootstrap.sh                       # once: create .venv, install deps
bash run_blueprint.sh <export.zip|dir> [--course-number .. --course-title .. --term ..]
```

Direct equivalent (after bootstrap):
```bash
.venv/bin/python scripts/build_blueprint_bundle.py <export> --label NAME
```

Outputs: `output/<label>__blueprint_bundle/` containing `*.blueprint.md`,
`*.blueprint.docx`, `*.blueprint.json` (the model), plus inventory/manifest/
structure/activities/QA companions.

## Pipeline (what runs, in order)

`export_inventory` → `manifest_probe` → `reconstruct_course_structure --extract-html`
→ `extract_course_activities` → `course_qa_report` → build JSON model →
render Markdown + DOCX. Both renderers consume one model
(`schemas/blueprint_schema.json`); change the model shape in **both** the schema
and `blueprint_to_docx.py` if you extend it.

## Where to look

- `knowledge/HOW_EXTRACTION_MAPS_TO_BLUEPRINT.md` — source→section mapping,
  join logic, and known limitations. **Read this before changing extraction.**
- `knowledge/BRIGHTSPACE_PACKAGE_STRUCTURE_AND_IMPORT_NOTES.md` — package shape,
  joins, and the UTC due-date caveat.
- `knowledge/brightspace-export-triage_SKILL.md` — triage posture.
- `schemas/blueprint_schema.json` — the contract between extraction and rendering.
- `examples/` — a real worked run to compare against.

## Editing rules

- The pipeline scripts under `scripts/` come from the upstream
  `coursecraft_workbench` master. Prefer changing the blueprint layer
  (`build_blueprint_bundle.py`, `blueprint_to_docx.py`) over the extractors; if
  you must change an extractor, note it so it can be reconciled upstream.
  - **Known divergences (both additive; original fields unchanged):**
    - `reconstruct_course_structure.py` adds `body_segments` to each HTML topic —
      heading-split chunks parsed into formatting-preserving **blocks**
      (`{heading, level, blocks:[{kind, level, runs:[{text, href}]}], text}`) via
      `html_to_segments` / `html_fragment_to_blocks`. Enables the
      mirror-don't-reconstruct week model and preserves paragraphs/bullets/links.
    - `extract_course_activities.py` imports `html_fragment_to_blocks` and adds
      `instructions_blocks` (dropbox folders) and `description_blocks`
      (discussions) so activity content keeps its formatting too.
- DOCX section/field layout must keep matching the CGPS template. Markdown is the
  canonical flat file; DOCX is rendered to resemble the template, not byte-match
  its branding.
- Do not revert the weekly DOCX layout to left-label tables. Section labels are
  top header rows over full-width content rows.
- Do not move Learning Materials/Resources below assessments. They belong
  immediately after Learning Objectives.

## When unsure

Surface ambiguity explicitly to the user. A blueprint with honest `Needs review`
markers is correct; a confident-looking blueprint with invented content is not.
