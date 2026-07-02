# Bundle log & handoff notes

Working log for `brightspace-blueprint-bundle`. Newest first. The **Requested next
changes** section at the bottom is the active to-do for the next session.

---

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

## Requested next changes (ACTIVE — for the Fable session, 2026-07-02)

the author reviewed the output and asked for these before we zip + backport. Order is
roughly the suggested implementation order. Keep the design rules above.

### 1. Polish the DOCX formatting
The DOCX is correct but could look better. Candidate improvements (confirm which
matter with the author):
- Use Word's **native list styles** (`List Bullet` / numbering) instead of a
  literal `"• "` text prefix in `_emit_block` — gives proper hanging indents and
  real nesting by `level`.
- Add paragraph **spacing** (space-after on paragraphs; tighter within bullet
  lists) so cells aren't cramped; the current cells run paragraphs together.
- Style the verbose **row-label cells** (e.g. "Overview: (add an introduction…)")
  as distinct/shaded/smaller so extracted content stands out from scaffolding.
- Set **table column widths** for the two-column rows; confirm merged full-width
  rows read cleanly.
- Confirm body runs inherit the template's **Open Sans** (Normal style); set
  explicitly if not.
- Nested lists: verify `Pt(12 * level)` indentation reads well, or switch to list
  style levels.
Files: `blueprint_to_docx.py` (`_emit_block`, `write_blocks`,
`write_value_labeled`, `add_week_table`, `add_front_matter_table`, style helpers).

### 2. Fidelity + flexible labeling of "Other course sections"
Symptom the author saw: a **separate content/"learning materials" page gets lumped in
with the Checklist** under one "Other course sections" blob. Goal: each distinct
Brightspace page/section should be preserved as its **own clearly-labeled**
section, faithful to the course structure.
- Root cause to investigate in `build_blueprint_bundle.py`:
  - `route_topic` yields per-segment `(bucket, label, blocks)`; segments from
    *different topics/pages* all pour into one flat `other_sections` list. A page
    with a generic/blank heading can visually merge with the next.
  - Consider carrying **page/topic provenance** (the topic title) into segments so
    "other" labels can be `Page Title › Heading` (or just the page title when a
    page has no sub-headings). That keeps a standalone "Learning Materials" page
    distinct from a "Checklist" section on another page.
  - Respect `<h2>` vs `<h3>` **hierarchy** via `level`: a parent `h2`
    ("Learning Materials") with child `h3`s ("Readings", "Multimedia") is
    currently flattened to siblings — consider nesting/labeling by level.
- Keep it heading/title-driven; no per-course config.

### 3. Make Checklist its own section
Add a dedicated `checklist` bucket rather than folding it into "other".
- `build_blueprint_bundle.py`: add `CHECKLIST_KEYS = ("checklist",)`; in
  `classify_heading` return `"checklist"` for it (check early — no collision with
  resource keys). Accumulate a `checklist` list in `build_week_model`; add to the
  week dict.
- Schema: add `checklist` (array of `labeled_section`) to the week item.
- Renderers: add a **Checklist row** to the week table (md + docx), rendered only
  when present (like `other_sections`). Decide placement — likely after
  Assignments/Discussions or right before "Other course sections".

### 4. Flexible allowance for pages not codified in the schema
Generalize so **arbitrary additional Brightspace pages/sections** are carried
with fidelity, beyond the fixed template rows.
- The existing `other_sections` (open-ended `[{label, blocks}]`) is the container;
  strengthen it per #2 so each unmapped page is its own labeled section, and
  document in the schema that labels/content are open (the course's own
  structure), while keys stay closed (`additionalProperties: false`).
- Optional: rename/reshape to make intent clear (e.g. `additional_sections` with
  `{label, blocks, source_page, level}`), or keep `other_sections` and just add
  provenance + nesting. Coordinate with #2 and #3.

### Still queued AFTER the above (was paused mid-formatting review)
5. Zip the bundle (exclude `.venv`, `output`; keep `reference/` template).
6. Copy the zip into `../coursecraft_workbench/share_packets/`.
7. Backport scripts to `../coursecraft_workbench/scripts/` (merge — keep workbench
   paths; the extractor changes in #formatting are additive; note divergences).
8. Add schema → workbench schemas area, knowledge doc → knowledge base, a
   `VERIFIED_WORKFLOWS.md` entry, and a `logs/` worklog note in the workbench.
   (Do these only once #1–#4 are approved — don't backport mid-change.)
