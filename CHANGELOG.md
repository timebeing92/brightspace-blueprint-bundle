# Changelog

## 2026-07-16 — v1.2.0 normal runs no longer need LibreOffice

Published from commit `ec0ba6aad29cd24b0b54094ea69d6546648e526d`.
Release asset SHA-256:
`58edb98063f1466b963a127a824e56aacccabe7d64ecc991d4ca365693a95be8`.

- Pure-Python structural DOCX QA remains default-on and is now the complete
  normal-run verification path.
- Removed `pdf2image` from `requirements.txt`; optional preview dependencies
  live in `requirements-render.txt`.
- Retained `--render-docx-check` for backward compatibility as an explicit
  maintainer preview that converts DOCX to PDF/PNG for human inspection.
- Clarified that conversion success does not automatically detect clipping,
  overflow, page-break, or layout defects.
- The local Wizard no longer checks for, offers to install, or asks ordinary
  users about LibreOffice or Poppler.

## 2026-07-16 — v1.1.1 rubric QA and partial-delivery hotfix

Published from commit `63efd8d1828533657c6f9a223c38b3d55c746b2e`.
Release asset SHA-256:
`a7b905253bd7c2994b670b03ce0e01811f2836f99c4568be7d53dee027deb9e2`.

- Fixed a real rubric appendix false positive: the renderer collapses repeated
  and non-breaking whitespace in rubric headings, while structural QA formerly
  compared the rendered heading against the unnormalized source name.
  Structural QA now uses the renderer's visible-text normalization.
- Verified the fix against the SSWO 565 export that exposed the defect: all 19
  rubrics rendered, and DOCX structure QA completed with 0 breaks and
  0 warnings.
- Recoverable component failures no longer terminate the bundle before
  delivery. Structure/activity extraction can fall back to explicit empty
  evidence envelopes; rubric, QA, DOCX, structural-QA, and visual-render
  failures are retained as component findings.
- Malformed or newly shaped rubric JSON is moved to
  `<label>__rubrics_unparsed.json` for evidence preservation, excluded from
  rubric rendering, and exposed explicitly through progress outputs.
- Every run now emits `<label>__pipeline_status.md` and `.json`. A run is
  `partial` when a usable Markdown or DOCX blueprint exists alongside one or
  more component findings; `error` is reserved for runs with no primary
  deliverable.
- `coursecraft.progress/1` adds `run_end.status: partial`, structured `issues`,
  status-report paths, and DOCX-structure report paths. Existing fields remain
  additive and unchanged.

## 2026-07-15 — Release provenance and checksums

- Added `VERSION` and `scripts/make_release_asset.py` for building a bundle
  release asset from one explicit git ref.
- Release assets contain `RELEASE_MANIFEST.json` with the source repository,
  ref, commit, schema versions, and schema hashes.
- The builder refuses dirty worktrees by default, normalizes archive metadata,
  and writes a sidecar SHA-256 checksum.
- Published `v1.1.0`; the hosted workshop now verifies and runs that exact
  release asset by commit and checksum.

Implementation history for `brightspace-blueprint-bundle`. Newest first.

---

## 2026-07-15 — Rubric DOCX appendix and review document (done)

Rubric grids now render into human-review DOCX surfaces. When
`rubrics_d2l.xml` is present and DOCX output is enabled, the main
`<label>__blueprint.docx` includes a Rubric Appendix generated from
`coursecraft.rubrics/1`, and the pipeline also emits a standalone
`<label>__rubrics.docx`. Both surfaces use the same renderer as the rubric
JSON/XLSX extraction path and include "Used by" lines when course activity
joins expose assignment, discussion, or quiz rubric associations.
`coursecraft.progress/1` adds `outputs.rubrics_docx`; the DOCX structure QA
now validates the appendix table count and rubric headings instead of treating
the extra tables as drift. Quiz metadata now mirrors assignments/discussions
by showing the brief `Rubric:` line beside points and gradebook item when a
quiz rubric association is present.

---

## 2026-07-15 — Rubric layer promotion (done)

Promoted rubric-grid extraction into the standalone blueprint bundle. The
pipeline now detects `rubrics_d2l.xml` and, when present, runs
`extract_rubrics_to_workbook.py` as its own optional step, producing
`<label>__rubrics.xlsx` and `<label>__rubrics.json` beside the blueprint,
activity, structure, inventory, manifest, and QA artifacts. The JSON uses the
vendored `coursecraft.rubrics/1` schema, matching the downstream
`coursecraft-catalog` ingest contract. `coursecraft.progress/1` now exposes
`rubrics_json`, `rubrics_workbook`, and a `summary.rubrics` count as additive
fields so the runner/TUI can display rubric outputs without globbing or
parsing D2L XML. Worked examples and tests were updated with the rubric
artifacts.

---

## 2026-07-13 — Portable provenance (done)

Provenance fields no longer embed absolute machine paths. Every recorded
source/export path — `source` in the inventory, `manifest_path` in the
manifest probe, `export` in the structure/activities/QA JSON, and the
"Source export" line of the bundle README — now carries the path exactly as
given on the CLI; paths are resolved only for file access. Concretely:
`export_inventory.build_inventory()` gained a `display_source` parameter,
`manifest_probe.load_manifest_root()` a `display` parameter, and the
orchestrator passes the as-given export through to the step scripts (its
subprocesses now inherit the caller's cwd instead of pinning the repo root,
so as-given relative paths mean the same thing in every process). The worked
example was regenerated with the documented command, so
`examples/sample_course__blueprint_bundle/` records
`examples/sample_export.zip` instead of a `/Users/...` home-directory path —
previously a public-release leak — and the golden suite (conftest now feeds
the same relative path) passes on any machine at any checkout location
instead of being bound to one absolute path. `export_inventory.py` and
`manifest_probe.py` are mirror-policy files — the same change landed in the
workbench originals and the staged snapshot; the orchestrator divergence was
re-pinned (drift check: 0 actionable on both bundles; bundle 44 and
workbench 86 tests green). Closes the portable-provenance follow-up flagged
2026-07-13 in the workbench development roadmap.

---

## 2026-07-13 — Structural DOCX QA (done)

New `scripts/docx_structure_qa.py`: a pure-Python (python-docx) structural
check of the rendered DOCX against its blueprint model — the package opens,
every `r:id`/`r:embed` relationship reference resolves, every hyperlink
target is a URL the model actually contains (with a count comparison that
mirrors the renderer's link-emission rules), the table census and per-week
table shape match the section layout, and the course/week titles survive as
body paragraphs. Runs by default as pipeline step "Check DOCX structure"
immediately after Render DOCX (`--skip-docx-structure-check` opts out);
writes `<label>__docx_structure.{md,json}`; breaks fail the step, warnings
do not. This makes the LibreOffice/Poppler visual render QA a rarely-needed
deep pass rather than the only DOCX verification — zero extra installs.
Worked example regenerated with the new artifacts. Four new tests (golden
pass, dangling-relationship break, wrong-layout warning, model-mismatch
break); suites green (bundle 44, workbench 86); drift 0 after the
orchestrator divergence re-pin.

---

## 2026-07-13 — Quiet step stdout (done)

`export_inventory.py` and `manifest_probe.py` no longer print their entire
markdown reports to stdout on every pipeline run. The default is now a short
summary (counts plus the path of the full report when `--output-dir` is set);
`--print-full` restores the old full-report stdout. Written artifacts are
unchanged. Graduated from the ROADMAP "Quiet the step dumps" item; a
representative runner log shrank from 725 to 49 lines. Both scripts are
mirror-policy files — the same change landed in the workbench originals and
the staged snapshot (drift check: 0 actionable on both bundles; workbench 86
and bundle 38 tests green).

---

## 2026-07-09 — Test suite, shared helpers, hardened pipeline, progress contract (done)

**Tests.** New `tests/` pytest suite (install `requirements-dev.txt`): a golden
run over `examples/sample_export.zip` compared artifact-by-artifact against the
committed worked example (text, JSON, DOCX visible text, workbook cells, schema
validation), unit tests for the shared helpers, a wrapped-folder export test,
and a progress-event stream test. The suite immediately caught that the
committed worked example predated the visual-QA and callout-wrapping commits;
the example was regenerated from the current scripts and the golden test now
pins that alignment.

**Shared helpers.** `common_xml.py` now owns the previously copy-pasted
`safe_label`, `load_export_root`, `html_to_text` (the two divergent copies are
reconciled — script/style payloads are always dropped now, including in
activity instructions), `xml_safe_text`/`clean_text`/`clean_label`, the
image/alt/rcode regexes and alt-text helpers, and the renderer-lockstep
`should_divide_labeled_sections` used by both the Markdown and DOCX renderers.

**Wrapped exports.** New `find_manifest`/`resolve_export_root` locate
`imsmanifest.xml` anywhere under the export root (shallowest wins; multiples
warn instead of aborting) and re-root extraction at the manifest's folder, so
re-zipped downloads with a wrapping folder now build instead of dying at step
3; a diagnostic records the re-rooting.

**Hardening.** `jsonschema` is now a runtime dependency and the assembled model
is validated against `schemas/blueprint_schema.json` on every run (loud stderr
warning on mismatch). Extraction subprocesses now honor `--step-timeout`
(default 900 s; `0` disables). Dead imports removed from `export_inventory.py`.

**Progress contract.** Each step prints a `== [3/7] … ==` banner by default.
New `--progress-events` emits one NDJSON event per line
(`coursecraft.progress/1`: `run_start` → `step_start`/`step_end` pairs →
`run_end` with real output paths and summary counts). Contract:
`schemas/progress_events_schema.json` + `knowledge/PROGRESS_EVENTS_CONTRACT.md`.
Built for wrapper tools — the sibling `brightspace-blueprint-runner` wizard v2
is the first consumer.

---

## 2026-07-09 — Visual callout section wrapping (done)

Source callout/card/highlight containers now preserve the full wrapped section
when the HTML structure supports it. Markdown renders the cue and child content
inside a highlighted review block, and DOCX shades both the cue paragraph and
the section text so visual hierarchy is clearer during review.

The blueprint schema now allows visual blocks to carry nested child blocks for
this purpose while retaining the older single-cue fallback for simple wrappers.

---

## 2026-07-09 — User-facing documentation and knowledge refresh (done)

The README now leads with the script-run workflow, bundle contents, setup steps,
virtual-environment usage, pipeline order, QA/render options, share hygiene, and
how to suggest improvements for review and roadmap consideration.

The `knowledge/` folder was refreshed to match the current bundle behavior:
`workspace/review` output defaults, `coursecraft.blueprint/4`, optional DOCX
render QA, hidden/faculty-facing material handling, specific image-alt QA
details, package-scope diagnostics, front-matter source diagnostics, optional
external-link checks, Creator+ practice handling, and current local bundle
references.

The provenance note now clarifies that many core scripts are derived from a more
substantive Brightspace XML and course tooling suite that has been in
development since 2024 and is available upon request.

## 2026-07-08 — Public license and commercial licensing note (done)

The bundle now uses `AGPL-3.0-or-later` as its public software license and adds
`COMMERCIAL.md` for organizations that need commercial licenses, hosted
deployments, implementation support, institutional integrations, training,
maintenance, warranty, or procurement support.

## 2026-07-08 — Blueprint title uses course title directly (done)

Markdown and DOCX blueprints now use the actual course title as the visible
top-level title. The old placeholder-style heading (`Course # - Course Blueprint
- Term`) is no longer rendered, and the course title is no longer demoted to a
subtitle. Course number and term remain optional metadata in the JSON model.

## 2026-07-08 — Hidden/faculty-facing manifest content and package-scope diagnostics (done)

Hidden/faculty-facing manifest items now remain visible in review output with a
clear note. Hidden modules get a visible notice, hidden HTML bodies are
extracted when readable, hidden non-HTML files are preserved as file references,
and hidden D2L object links such as quizzes, assignments, discussions,
checklists, quicklinks, and LTI links keep object/type/path evidence where
available.

Large or messy exports also get package-scope extraction notes: hidden
manifest-linked files skipped from body extraction, and package files not
directly linked from the visible manifest, summarized by count, type, size, and
largest paths.

## 2026-07-08 — Suppress weekly duplicates of pre-week roadmap pages (done)

Some D2L courses link or copy the same global roadmap/setup page in both the
pre-week orientation module and Week 1. The model builder now renders the page
once in `Before Week 1: Additional Resources and Information` and skips later
weekly copies with the same href or the same title/body signature, with a
routing diagnostic explaining the skip.

## 2026-07-08 — D2L activity object dividers in section rows (done)

Markdown and DOCX renderers now place horizontal dividers between separate D2L
activity objects inside the Assignment(s) and Discussion Board rows. This makes
multiple assignments, quizzes, and discussion topics scan as separate objects
instead of running together in one large cell.

The divider is applied by object list only; it is not used to infer extra
separation inside a single content page or resource page.

## 2026-07-08 — Sibling-aware overview/resource routing (done)

Stress-testing BUMG 520 found a split-page module pattern: weeks can have both
`Week N Overview` and `Week N Learning Materials and Resources` as separate
sibling pages. The router now detects that module-level structure before
routing headings. In those modules, resource-like headings inside the explicit
overview page stay in the Overview row instead of bleeding into Assigned Reading
and Multimedia. Combined pages such as `Week N Overview and Learning Materials`
still split internally because there is no separate resource sibling.

The model also adds a routing diagnostic when this protection is applied, so
stress-test output makes the structural decision visible.

## 2026-07-07 — XML-safe source text normalization (done)

Stress-testing against the latest Downloads exports found two packages whose
decoded page text included XML-invalid control characters/noncharacters. The
blueprint model builder now filters source-derived strings to the
XML-compatible character set, and the DOCX renderer applies the same guard when
rendering older JSON models. Markdown/JSON/DOCX output now completes for those
packages.

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

## 2026-07-08 — Binary course-file and image placeholders (done)

Manifest `content` items that point at non-HTML course files now render as
attached-file reference blocks instead of being decoded as page text. This keeps
Office/PDF/spreadsheet/presentation payloads out of the JSON, Markdown, and DOCX
blueprints while preserving the object as a reviewable course-file reference.

HTML page images now render as stand-in image blocks with alt text when present,
or a no-alt placeholder plus the source path when alt text is missing. The
pipeline does not parse, OCR, convert, or embed the image itself.

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

The blueprint architecture decision was made after reviewing a SME-facing
course output:

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
  different pages never merge into one "other" blob (the section-lumping
  symptom).
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
  out from scaffold labels; matches the Markdown layout.
- **Native Word bullets**: List Bullet styles when the style base defines them,
  else a `numPr` reference to the document's own bullet numbering
  (`_find_bullet_num_id` / `_apply_native_bullet`), else manual hanging-indent
  fallback. Real nesting by `level`.
- Paragraph spacing (6pt after prose, 2pt after bullets, 8pt before each
  labeled section after the first) so cells aren't cramped; leading empty cell
  paragraphs trimmed (`_trim_leading_empty`).
- Removed dead `write_value_block` / `write_value_bullets`.

**Markdown styling:** paragraphs separated by blank lines inside cells while
consecutive bullets stay tight; nested bullets indent by level; labeled
sections separated by a blank line; `---` divider between weeks; Checklist row.

**Verified:** chem-1020 (16 weeks) → 16/16 weeks with Checklist + LOs, 320 live
hyperlinks, 773 native-numbered bullets, 0 literal-bullet fallbacks, path
labels like "Week 4: Quiz › Conformation Analysis Quiz › Instructions"; sample
fixture → LO fallback intact, reading page intro correctly joins resources.
Both models validate against the schema (jsonschema); both DOCX round-trip open.
Example bundle regenerated.

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
- Verified: a formatting-rich private export produced 320 live `w:hyperlink`s,
  readings as bulleted live links, objectives as bullets, and structured
  assignments; the sample fixture kept the LO fallback intact. All scripts
  `py_compile` clean.

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
knowledge/, schemas/, examples/. Both DOCX + Markdown.

---

## Orientation for the next session

- **What this is:** Brightspace/D2L export → flat-file course blueprint
  (Markdown + DOCX) for source-traceable course review.
  Read `README.md` (esp. *Design philosophy*), then `AGENTS.md`, then
  `knowledge/HOW_EXTRACTION_MAPS_TO_BLUEPRINT.md`.
- **Run it:** `bash bootstrap.sh` once, then
  `bash run_blueprint.sh <export.zip> --course-number .. --course-title .. --term ..`.
  Outputs land in `workspace/review/<label>__blueprint_bundle/` unless
  `--output-dir` is supplied.
- **Test exports:** use `examples/sample_export.zip` for a privacy-safe smoke
  test, then validate against private Brightspace exports outside the repo when
  changing extraction behavior.
- **Pipeline:** `build_blueprint_bundle.py` orchestrates `export_inventory` →
  `manifest_probe` → `reconstruct_course_structure --extract-html` →
  `extract_course_activities` → `course_qa_report`, builds a JSON model
  (`schemas/blueprint_schema.json`), renders `.md` + `.docx`, and can run
  optional DOCX visual render QA with `--render-docx-check`.
- **Design rules to keep:** mirror, don't reconstruct; no per-course config
  (labels come from the course's own headings/titles); never invent content;
  keep missing fields visible; Markdown is canonical, DOCX is the Word rendering
  of the same model.

---

## Requested next changes

No active carryover items are recorded here. Use the newest dated entries above
for current behavior and rerun `examples/sample_export.zip` before publishing or
sharing a refreshed package.
