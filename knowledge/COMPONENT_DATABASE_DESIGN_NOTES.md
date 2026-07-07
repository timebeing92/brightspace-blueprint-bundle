# Design notes: the cross-course component database (future spin-off)

**Status: design note only — not active work in this bundle.** Written
2026-07-02 while building the fidelity/provenance changes, so the thinking is
captured before the spin-off starts.

## The idea

Export many course packages from Brightspace, deconstruct them with these
extractors, and load the labeled component parts into a queryable database of
course **assignments, assessments, discussions, activities, checklists, and
content sections** — so someone can pull up, say, every assignment in a
program, every quiz-style assessment, or every "case study" activity across
courses, and see its instructions, points, rubric, and where it lives.

## Why this bundle already does most of the work

The architecture is **extraction → course-agnostic JSON → rendered views**.
The blueprint (md/docx) is just one *view* of the JSON. A database is simply
another consumer of the same JSON — ingestion is "run the pipeline per export,
concatenate the models," not a re-architecture.

The per-course records that matter:

- `<label>__blueprint.json` (`schemas/blueprint_schema.json`, currently
  `coursecraft.blueprint/3`) — the assembled model: weeks → labeled sections.
- `<label>__course_activities.json` — the raw activity records (dropbox
  folders, discussions, grade/rubric joins) before blueprint shaping.
- `<label>__course_structure.json` — the module/topic tree with
  `body_segments` (formatting-preserving blocks).

## What the extractors must keep guaranteeing

These are the contracts the future project depends on. Break them knowingly.

1. **Provenance on every record.** As of schema `/3`, every page-derived
   section carries `source_page` + heading `level`; activities carry
   `resource_code` and grade/rubric joins; weeks carry the module title.
   A database row should always be traceable to *course → module → page →
   heading*. Whatever changes, never emit an anonymous blob.
2. **Typed identity.** Components keep their kind (assignment / discussion /
   quiz link / checklist / resource / other-section) rather than being
   flattened to text. The `classify_heading` alias table + the activity XML
   types are the seed taxonomy.
3. **Small, documented classifier vocabulary.** The alias table
   (`OBJECTIVE_KEYS` / `RESOURCE_KEYS` / `OVERVIEW_KEYS` / `CHECKLIST_KEYS`) is
   deliberately tiny and heading-driven, with no per-course config — that is
   what lets it scale across many courses unattended. Additions should be
   universal terms, not one course's quirks; everything else stays mirrored
   under its own label.
4. **Versioned schema.** The `schema` field (`coursecraft.blueprint/N`) is the
   compatibility contract. Bump it on shape changes so a mixed corpus of old
   and new runs stays interpretable.
5. **Fidelity over inference.** Extracted wording is preserved; missing data
   stays visible (`Needs review`); content is never invented. A database of
   confident-looking guesses is worse than one with honest gaps.

## What the spin-off will need to add (not here)

- **Cross-course identity**: a course/offering key (org unit id + term label
  are in the export names and manifest) and stable per-component ids —
  `resource_code` is only unique within an export.
- **Corpus ingestion**: walk N bundle outputs → one store (SQLite or DuckDB
  over the JSON is likely enough to start; both query nested JSON well).
- **Search/browse layer**: by course, program, component type, points, rubric
  presence, label text, activity structure.
- **Dedup/versioning** across re-offerings of the same course (same
  `resource_code` lineage, different terms).
- **Taxonomy governance**: when program-level labels are wanted (e.g. tagging
  "signature assignments"), tag in the database — do not push per-program
  vocabulary back into the extractors.

## Where it should live

Its own repo, consuming this bundle (or the workbench) as the extraction
engine. Keep the extractors upstream in `coursecraft_workbench`; the database
project should pin a schema version and read JSON, never parse D2L XML itself.
