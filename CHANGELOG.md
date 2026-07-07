# Bundle log & handoff notes

Working log for `brightspace-blueprint-bundle`. Newest first. The **Requested next
changes** section at the bottom is the active to-do for the next session.

---

## 2026-07-07 — Page-aware Other section dividers (done)

Markdown and DOCX blueprints now use `source_page` provenance when rendering
weekly `Other course sections`. Horizontal dividers mark transitions between
separate source pages, while multiple subsections from the same page stay
grouped together without an extra rule between every heading.

## 2026-07-07 — Before-week grouping cleanup (done)

Before-week material now renders as page groups instead of repeated full
breadcrumb labels. A page such as `Project Overview and Roadmap` appears once,
with internal headings rendered as local labels like `Course Project:` and
`What the Project Entails:`. Course Introduction pages that already populate the
global Course Introduction section are skipped in before-week material.

The visible section title is now `Before Week 1: Additional Resources and
Information`, and the page groups render inside a table to match the rest of the
blueprint.

## 2026-07-07 — Before-week pages and visual structure cues (done)

Schema is now `coursecraft.blueprint/4`.

Top-level orientation/resource modules that appear before the first detected
week/module are preserved in `before_week_1` and rendered under:

`Before Week 1: Additional Resources and Information`

The HTML structure pass now also keeps lightweight visual cues from source
pages: callout/note/card-like containers, dropdown summaries, video/media
embeds, and horizontal rules. Markdown renders these as explicit cue lines;
DOCX renders them as shaded cue paragraphs or dividers. This is intentionally a
review aid, not a pixel-level recreation of Brightspace CSS.

## 2026-07-07 — Overview routing and Creator+ practice metadata cleanup (done)

Fixed the BUMG/MGT 660 routing issue where week-level overview resources could
be mistaken for global `TEXTBOOK/S OR REQUIRED MATERIALS` front matter. Weekly
overview pages now keep their authored overview subsections inside the Overview
row unless a heading clearly belongs to Learning Objectives, Resources, or
Checklist. Lesson pages still flow to `Other course sections`, and separate
Other entries now get visual dividers in Markdown and DOCX.

Creator+ Practice iframes are no longer reduced to generic "Embedded media"
when the HTML wrapper points to a local `.practice.json` file. The structure
pass now resolves the practice metadata and surfaces a lightweight review
summary in place: practice title, type, source file, id, question/item/category
counts, scoring status, and authored description/instructions/prompts. Full
answer-key and feedback review remain outside the blueprint bundle.

## 2026-07-07 — Quiz-level instructions/settings added to blueprints (done)

Audited the BUMG/MGT 660 share-packet example and confirmed the previous gap:
module quiz quicklinks were present, but quiz instructions from `quiz_d2l_*.xml`
were not carried into the blueprint. The activity pass now extracts
manifest-linked quiz payloads and joins them by `resource_code`/`rCode`.

Blueprint assignment rows can now include quiz-level instructions, gradebook
points/joins, attempts allowed, time-limit/enforcement settings, section counts,
drawn-question/candidate-question counts, and question-type summaries. Full
question text, answer keys, question-library matching, and pool-origin review
remain intentionally outside the blueprint bundle; use the dedicated quiz
review extractor for that reviewer-facing layer.

## 2026-07-07 — DOCX layout option and XML checklist extraction (done)

Added `--docx-section-layout top|left` to the normal bundle command. `top`
remains the default stacked-section DOCX layout; `left` renders the same
schema-backed blueprint model with shaded section labels in a left column. The
left layout uses full-page-width weekly tables, and the front-matter tables
remain full width.

Checklist extraction now treats `checklist_d2l.xml` as the typical source. The
activity pass extracts checklist payloads and joins them to module checklist
quicklinks by `resource_code`/`rCode`; HTML checklist page headings are still
preserved when present. If a manifest checklist link exists but the XML payload
is unavailable, the blueprint keeps a visible checklist entry instead of
dropping it.

## 2026-07-07 — Activity metadata rendering cleanup (done)

Assignment and discussion entries now keep the activity title clean and render
points, gradebook item, and rubric metadata as separate bold lines at the top of
the section body. The Markdown and DOCX renderers also normalize trailing colons
on section labels before adding their own colon, preventing labels such as
`Learning Materials::`. This was a render/model-format cleanup within the
existing block structure.

Requirements documentation now also names the one-time dependencies explicitly:
`openpyxl` for workbook output and `python-docx` for DOCX rendering. `bootstrap.sh`
installs them into `.venv` once; later exports reuse that environment.

## 2026-07-07 — Pipeline/package context guide (done)

Added `knowledge/SCRIPT_PIPELINE_AND_PACKAGE_CONTEXT.md` so the shareable bundle
now explains the script pipeline in both layperson and technical layers. The new
guide covers:

- what happens when a colleague runs the bundle
- which scripts run in what order
- which companion artifacts each script writes
- the `coursecraft.blueprint/4` JSON schema contract
- the distinction between the owned blueprint schema and observed Brightspace
  package XML
- common D2L export files, package structure, and joins (`identifierref`,
  `href`, `resource_code`, grade/rubric references, relative assets)
- how to debug unexpected DOCX output by reading the manifest, structure,
  activities, and QA artifacts

Linked the guide from `README.md` and `AGENTS.md`.

## 2026-07-06 — SME blueprint layout/order decision (done)

The architecture decision was made explicit after reviewing the EDU
741 SME output:

- Weekly DOCX module tables are **full-width, single-column stacked sections**.
  Section scaffold labels sit in shaded header rows above the content, not in a
  left label column.
- Weekly section order is now:
  **Overview → Learning Objectives → Assigned Reading and Multimedia / Learning
  Materials → Assignment(s) and Instructions → Discussion Board Prompts →
  Checklist → Other course sections**.
- Rationale: learning materials should be reviewed immediately after LOs so the
  SME can judge whether resources support the outcomes before reading
  assessment/discussion detail.
- `overview` blocks may include internal `label` blocks, used for source
  subsections that belong in overview flow (for example, "Independent
  Research:"). Schema allows `kind: "label"` as of this decision.

Updated `build_blueprint_bundle.py`, `blueprint_to_docx.py`,
`schemas/blueprint_schema.json`, `README.md`, and
`knowledge/HOW_EXTRACTION_MAPS_TO_BLUEPRINT.md`. Regenerate any worked examples
after this point before sharing them externally.

## 2026-07-02 (later) — Section fidelity, checklist row, DOCX/MD styling (done)

Implements the four requested changes below (schema bump `/2` → `/3`). Bundle
layer only — no extractor changes, so no new workbench divergences.

**Model / routing (`build_blueprint_bundle.py`):**

- `route_topic` now yields `{bucket, label, blocks, source_page, level}`. Every
  page-derived section carries provenance (source page title + heading level).
- Unmapped headings get a **"Page › Heading" path label** built from the page
  title plus the h1–h4 heading stack (`_path_label`), so sections from
  different pages never merge into one "other" blob (the author's lumping symptom).
- **Checklist is its own bucket** (`CHECKLIST_KEYS`, checked first in
  `classify_heading`) and its own week row in both renderers, shown only when
  present (placed after Reading, before Other).
- Headless pages with a distinct title now become their own labeled "other"
  section (`_page_default_bucket`); week-titled/untitled pages still read as
  Overview. Intro content before a page's first heading follows the page's own
  classification (a "Learning Materials" intro joins resources, not Overview).
- Removed dead `md_bullets` / unused `Iterable` import.

**Schema (`blueprint_schema.json`):** expanded the schema contract so
`labeled_section` gains optional `source_page` + `level`; week gains
`checklist`; documented that labels/content are open, keys closed.

**DOCX styling (`blueprint_to_docx.py`):**

- Week tables are now **two-column: shaded 9pt scaffold-label cell | content
  cell** (1.9in / 4.6in, fixed layout, cell margins) — extracted content stands
  out from template scaffolding; matches the Markdown layout.
- **Native Word bullets**: List Bullet styles when the style base defines them,
  else a `numPr` reference to the template's own bullet numbering
  (`_find_bullet_num_id` / `_apply_native_bullet` — the CGPS template has 5
  bullet defs), else manual hanging-indent fallback. Real nesting by `level`.
- Paragraph spacing (6pt after prose, 2pt after bullets, 8pt before each
  labeled section after the first) so cells aren't cramped; leading empty cell
  paragraphs trimmed (`_trim_leading_empty`).
- Removed dead `write_value_block` / `write_value_bullets`.

**Markdown styling:** paragraphs separated by blank lines inside cells while
consecutive bullets stay tight; nested bullets indent by level; labeled
sections separated by a blank line; `---` divider between weeks; Checklist row.

**Verified:** chem-1020 (16 weeks) → 16/16 weeks with Checklist + LOs, 320 live
hyperlinks, 773 native-numbered bullets, 0 literal-bullet fallbacks, path
labels like "Week 4: Quiz › Conformation Analysis Quiz › Instructions";
sandbox → LO fallback intact, "Reading Notes" page intro now correctly joins
resources. Both models validate against the schema (jsonschema); both DOCX
round-trip open. Example bundle regenerated; a chem review copy is in
`output/chem-1020-review__blueprint_bundle/`.

## 2026-07-02 — Formatting fidelity rebuild (done)

Extraction no longer flattens pages to one line. HTML is parsed into
**blocks** (paragraphs / list items with link-aware runs) so paragraphs, bullet
lists, and links survive into both renderers.

- `reconstruct_course_structure.py`: added `html.parser`-based `_BlockExtractor`,
  `html_fragment_to_blocks`, `blocks_to_text`; `html_to_segments` now returns
  `{heading, level, blocks, text}` per segment. Preserves `<p>`, `<ul>/<ol><li>`,
  `<a href>` (live), `<iframe>`/video embeds; strips `<script>/<style>` and
  page-template artifacts ("Basic Page - No Banner"). `body_text` unchanged.
- `extract_course_activities.py`: imports `html_fragment_to_blocks`; dropbox
  folders gain `instructions_blocks`, discussions gain `description_blocks`
  (so assignment/discussion instructions keep their formatting too).
- `build_blueprint_bundle.py`: model now carries blocks. `overview` /
  `learning_objectives` are block lists; `resources` / `other_sections` /
  `assignments` / `discussions` are `[{label, blocks}]`. Markdown renders live
  `[text](url)` links and `•` bullets; `md_inline` / `md_blocks` / `md_labeled`.
- `blueprint_to_docx.py`: real clickable `w:hyperlink` runs (`add_hyperlink`),
  kind-aware `_emit_block` (only `li` gets a bullet, paragraphs stay prose),
  `write_blocks` / `write_value_labeled`.
- Schema bumped to `coursecraft.blueprint/2` with `run` / `block` / `blocks` /
  `labeled_section` definitions.
- Verified: chem-1020 (16 weeks) → 320 live `w:hyperlink`s, readings as bulleted
  live links, objectives as bullets, assignments structured; sandbox → LO
  fallback intact. All scripts `py_compile` clean.

## 2026-07-01 — Due dates de-scoped (done)

Numeric coded due dates are term-relative, so they are no longer encoded. Removed
`assignment_due` / `format_due` and the injected "Due: Sun / Fri-Sun"
placeholders. Day-of-week cadence rides along in the extracted instruction text.

## 2026-06-30 → 07-01 — Mirror-model redesign (done)

Reversed the original rigid "reconstruct the backwards-design" approach. Now
**mirrors the course's own page-heading structure** inside the blueprint frame,
with a small alias table normalizing only Learning Objectives + Resources. LOs
split out only when an objectives heading exists (else stay in Overview). See the
README "Design philosophy" section. Added heading-driven segmentation. Dropped
the artificial "Lecture topics" row; added "Other course sections".

## 2026-06-30 — Initial build (done)

Standalone bundle assembled: 7 workbench pipeline scripts copied verbatim +
custom `build_blueprint_bundle.py` (reverse: export → flat blueprint) and new
`blueprint_to_docx.py`. requirements.txt + bootstrap.sh/.ps1 + run_blueprint.sh,
knowledge/, schemas/, reference/ (CGPS template), examples/. Both DOCX + Markdown.

---

## Orientation for the next session

- **What this is:** Brightspace/D2L export → flat-file course blueprint
  (Markdown + DOCX), shaped after `reference/Course Blueprint Template 2020 CGPS.docx`.
  Read `README.md` (esp. *Design philosophy*), then `AGENTS.md`, then
  `knowledge/HOW_EXTRACTION_MAPS_TO_BLUEPRINT.md`.
- **Run it:** `bash bootstrap.sh` once, then
  `bash run_blueprint.sh <export.zip> --course-number .. --course-title .. --term ..`.
  Outputs land in `output/<label>__blueprint_bundle/`.
- **Test exports on this machine** (under the sibling workbench):
  - full, formatting-rich: `../coursecraft_workbench/workspace/exports/raw/20260423-091649__chem-1020-2021-tng-full-d2l-export.zip`
  - small, privacy-safe (worked example): `../coursecraft_workbench/workspace/exports/raw/20260616-100257__d2lexport_00000_sample-sandbox_202661651.zip`
- **Pipeline:** `build_blueprint_bundle.py` orchestrates `export_inventory` →
  `manifest_probe` → `reconstruct_course_structure --extract-html` →
  `extract_course_activities` → `course_qa_report`, builds a JSON model
  (`schemas/blueprint_schema.json`), and renders `.md` + `.docx`.
- **Design rules to keep:** mirror, don't reconstruct; no per-course config
  (labels come from the course's own headings/titles); never invent content;
  keep missing fields visible; Markdown is canonical, DOCX resembles the template.

---

## Requested next changes (ACTIVE)

The four 2026-07-02 requested changes (DOCX polish; other-section fidelity;
Checklist row; flexible uncodified pages) are **done** — see the entry at the
top. **Awaiting the author's review** of the regenerated output before backporting:
open `output/chem-1020-review__blueprint_bundle/chem-1020-review__blueprint.docx`
(formatting-rich) and the regenerated `examples/sandbox_demo__blueprint_bundle/`.

Once the output is approved:

1. Zip the bundle (exclude `.venv`, `output`; keep `reference/` template).
2. Copy the zip into `../coursecraft_workbench/share_packets/`.
3. Backport scripts to `../coursecraft_workbench/scripts/` (merge — keep
   workbench paths). This session changed only the bundle layer
   (`build_blueprint_bundle.py`, `blueprint_to_docx.py`, schema) — the
   extractors are untouched, so the known divergences list is unchanged.
4. Add schema → workbench schemas area, knowledge doc → knowledge base, a
   `VERIFIED_WORKFLOWS.md` entry, and a `logs/` worklog note in the workbench.
