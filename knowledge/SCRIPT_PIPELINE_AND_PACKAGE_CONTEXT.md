# Script Pipeline and Brightspace Package Context

This note explains how the blueprint bundle works at two levels:

- a plain-language walkthrough for a colleague who wants to know what happens
  when they run it
- a technical map for someone maintaining the scripts or interpreting the
  companion JSON/XML outputs

The short version: the bundle reads a Brightspace/D2L course export, inventories
the package, reconstructs the course module/page/activity evidence, builds one
schema-backed JSON model, and renders that same model to Markdown and DOCX.

Runtime dependencies are intentionally small: `openpyxl` writes the
course-activities workbook and `python-docx` renders DOCX blueprints. They are
listed in `requirements.txt` and should be installed once into the local
environment, not reinstalled for every export.

## Plain-Language Version

A Brightspace export is a ZIP file full of course evidence. It usually contains:

- a table of contents file (`imsmanifest.xml`)
- course HTML pages and their assets
- D2L XML files for assignments, discussions, grades, rubrics, quizzes,
  checklists, and related tools
- local Creator+ practice JSON files when pages embed Brightspace Practice
  activities

The script pipeline treats that ZIP like evidence, not like a finished blueprint.
It asks:

1. What files are in this export?
2. What module/week structure does Brightspace report?
3. What HTML pages belong under each module?
4. What assignments, discussions, quizzes, and checklists exist, and where do
   they appear in the course?
5. What text can be safely copied into a blueprint review surface?
6. What is missing, ambiguous, or not joined cleanly?

The pipeline then writes a folder of outputs. The main files are:

- `<label>__blueprint.docx` for SME/reviewer sharing
- `<label>__blueprint.md` for a readable flat-file version
- `<label>__blueprint.json` for the structured data model
- companion inventory, manifest, structure, activity, workbook, and QA files
  for anyone who wants to audit where the blueprint came from

The tool does not log in to Brightspace, change the course, upload anything, or
use AI to fill gaps. If the export does not clearly contain something, the
blueprint marks it as `Needs review` or `None found` instead of inventing text.

## Mental Model

Think of the pipeline as three passes:

1. **Read the package.** Count and classify what Brightspace exported.
2. **Build evidence tables.** Turn manifest items, HTML pages, and D2L object XML
   into inspectable JSON/Markdown/workbook artifacts.
3. **Render one model.** Assemble a `coursecraft.blueprint/4` JSON model and
   render both Markdown and DOCX from it.

The blueprint is only one view of the extracted evidence. The companion files
are deliberately kept beside it so reviewers can check why a row appears, why an
activity was unplaced, or why a field needs review.

## Technical Dataflow

`scripts/build_blueprint_bundle.py` is the orchestrator. It runs these scripts in
order:

| Step | Script | Purpose | Main outputs |
| --- | --- | --- | --- |
| 1 | `export_inventory.py` | Classify package files and count D2L XML, HTML, documents, media, assets, and likely quiz files. | `*__inventory.json`, `*__inventory.md` |
| 2 | `manifest_probe.py` | Read `imsmanifest.xml`, summarize organizations/items/resources, identifierref usage, likely quiz resources, and suspicious hrefs. | `*__manifest_probe.json`, `*__manifest_probe.md` |
| 3 | `reconstruct_course_structure.py --extract-html` | Rebuild the module/topic tree, resolve manifest items to resources, read HTML pages, split page bodies by real headings, preserve paragraphs/lists/links plus lightweight visual cues as blocks, and expand Creator+ practice iframes from local `.practice.json` metadata when available. | `<label>__course_structure.json`, `<label>__course_structure.md` |
| 4 | `extract_course_activities.py` | Read assignment/dropbox, discussion, quiz-level instructions/settings, checklist, grade, rubric, condition, and quicklink evidence; resolve joins by `resource_code` where possible. | `<label>__course_activities.json`, `.md`, `.xlsx` |
| 5 | `course_qa_report.py` | Run read-only integrity checks over joins, missing files, malformed XML, dated fields, image alt text, and other review risks. | `<label>__course_qa.json`, `<label>__course_qa.md` |
| 6 | `build_blueprint_bundle.py` model builder | Combine course structure + activities into the blueprint model. | `<label>__blueprint.json` |
| 7 | Markdown renderer + `blueprint_to_docx.py` | Render the model into human-readable review outputs. | `<label>__blueprint.md`, `<label>__blueprint.docx` |

Markdown and DOCX both render from the same JSON model. If those two views ever
disagree, treat that as a renderer problem, not as two separate sources of
truth.

## The Stable Schema in This Bundle

The stable contract is the JSON model:

- file: `schemas/blueprint_schema.json`
- current schema id: `coursecraft.blueprint/4`
- producer: `scripts/build_blueprint_bundle.py`
- consumers: Markdown renderer inside `build_blueprint_bundle.py` and
  `scripts/blueprint_to_docx.py`

The model contains:

- course header fields: course number, course title, term, template reference
- course-level front matter: description, materials, outcomes, introduction
- `before_week_1`: top-level orientation/resource pages before the first week
- `weeks`: one entry per detected week/module
- week sections: overview, learning objectives, resources, assignments,
  discussions, checklist, other course sections
- `unplaced_activities`: activities found in object XML but not joined to a
  module
- diagnostics carried from the structure/activity passes

Text content is stored as formatting-preserving blocks:

- `p` = paragraph
- `li` = list item, with nesting level
- `label` = internal subsection label used by the renderer
- `visual`, `dropdown`, `embed`, `divider` = lightweight visual-structure cues
  from the source HTML
- each block contains link-aware text runs: `{text, href}`

Labeled sections preserve provenance where the evidence comes from course pages:

- `source_page`: the Brightspace topic/page title
- `level`: the source heading level, or `0` for whole-page sections

## Brightspace XML: Practical Shapes, Not Official XSDs

This bundle does not ship or depend on official Brightspace XML schemas. In
practice, Brightspace exports vary by tool, version, institution settings, and
course history. The scripts therefore treat the D2L XML files as observed source
evidence and parse the small set of fields needed for a review surface.

Useful distinction:

- **Blueprint schema:** the versioned JSON contract this bundle owns
  (`coursecraft.blueprint/4`).
- **Brightspace package XML:** source files the bundle inspects. These are not
  treated as a stable public API.

Common export files the scripts know how to recognize include:

| File pattern | Role in the pipeline |
| --- | --- |
| `imsmanifest.xml` | Course structure, organizations, items, resource references, quicklink placement, resource hrefs. |
| `orgunitconfig/orgunitconfig.xml` | Course/package metadata context. Not usually where instructional content lives. |
| HTML/HTM files | Student-facing content pages. These supply overview text, learning materials, objectives, checklists, and other sections. |
| `practice/*.practice.json` or `*.practice.json` | Creator+ Practice payloads referenced by HTML iframe `data-file` attributes. The blueprint carries lightweight practice metadata and authored prompts/instructions, not full answer-key review. |
| `dropbox_d2l.xml` | Assignment/dropbox folders, instructions, points, activity resource codes. |
| `discussion_d2l_*.xml` | Discussion forums/topics, prompts/descriptions, points, activity resource codes. |
| `grades_d2l.xml` | Grade items and grade joins for assignments, discussions, and quizzes. |
| `rubrics_d2l.xml` | Rubric names/ids used to resolve rubric references on activities. |
| `quiz_d2l_*.xml` | Quiz-level instructions, grade joins, attempts/time-limit settings, section/question-count summaries, and draw-count checks in QA. Linked quiz instructions/settings can appear in the blueprint; full question-bank review stays separate. |
| `questiondb.xml` | Question-library payload. Counted and checked as quiz evidence, but not converted into blueprint assessment content. Use the dedicated quiz review extractor for question/pool evidence. |
| `checklist_d2l.xml` | Checklist payload and dated checklist fields. Weekly checklist rows normally come from D2L checklist payloads joined to manifest checklist quicklinks by `resource_code`; HTML checklist headings are also preserved when present. |
| `conditionalrelease_d2l.xml` | Condition sets that help resolve activity release-condition joins. |
| `intelligentagents_d2l.xml` | Recognized as D2L XML evidence; not central to the blueprint render. |

When adding support for another D2L file type, keep the parser conservative:
extract fields with clear evidence, emit diagnostics for unresolved joins, and
avoid silently converting ambiguous XML into confident blueprint prose.

## Package Structure and Joins

The most important Brightspace joins are:

| Join | What it means | Where it appears |
| --- | --- | --- |
| `identifierref` | Manifest item points to a manifest resource. | `imsmanifest.xml` item -> resource |
| `href` | Manifest resource points to a package file or a tool/quicklink target. | `imsmanifest.xml` resources |
| `resource_code` / `rcode` / `rCode` | D2L object identity used to connect manifest placement and object XML records. | manifest items/quicklinks, dropbox, discussions, grades, conditions |
| `data-file` | HTML iframe points to a local Creator+ practice JSON payload. | HTML pages -> `practice/*.practice.json` |
| grade item code/reference | Assignment/discussion/quiz links to gradebook items. | activity XML and `grades_d2l.xml` |
| rubric id/reference | Assignment/discussion links to rubric records. | activity XML and `rubrics_d2l.xml` |
| relative asset reference | HTML page points to local images, PDFs, CSS, scripts, or other files. | HTML pages and package files |

The weekly blueprint placement depends mostly on the manifest tree and
`resource_code` joins:

- modules/weeks come from `imsmanifest.xml` organizations/items
- top-level non-week modules before the first detected week become
  `before_week_1`, rendered as `before week 1 - additional resources/
  information`
- HTML topic pages are found through manifest resource `href` values
- assignment, discussion, quiz, and checklist objects are placed in the week whose
  manifest quicklinks share the same `resource_code`/`rCode`
- Creator+ practice blocks stay inside the HTML page section where their iframe
  appears; they are not separate manifest activities in typical exports
- activities that cannot be placed are kept under `Unplaced Activities`

This is why the companion outputs matter. If something looks wrong in the DOCX,
check the evidence in this order:

1. `*__manifest_probe.md` to see whether the manifest has the expected structure.
2. `<label>__course_structure.md/json` to see which pages were attached to each
   module and how headings were split.
3. `<label>__course_activities.xlsx/json` to see assignment/discussion/quiz/
   checklist/grade/rubric joins.
4. `<label>__course_qa.md` to see unresolved references, missing files, malformed
   XML, or other integrity notes.

## Heading-Driven Blueprint Mapping

The script mirrors course headings rather than reconstructing instructional
design intent.

The small alias table in `build_blueprint_bundle.py` maps headings into common
blueprint buckets:

- objectives/outcomes/goals -> Learning Objectives
- readings/resources/materials/media/video/textbook/etc. -> Assigned Reading
  and Multimedia
- overview/introduction/welcome/start here/orientation -> Overview
- quiz quicklinks + `quiz_d2l_*.xml` payloads -> Assignment(s) and Instructions
- D2L checklist quicklinks + `checklist_d2l.xml` payloads -> Checklist
- HTML headings matching checklist -> Checklist
- Creator+ iframe `data-file` + local `.practice.json` payloads -> lightweight
  practice metadata blocks inside the current page section
- selected source visual structure -> block cues (`visual`, `dropdown`,
  `embed`, `divider`) rendered in Markdown and DOCX

Everything else stays visible under `Other course sections` using a
`Page > Heading` style provenance label. This keeps unusual course content from
being lost or merged into generic rows.

## What This Pipeline Can and Cannot Promise

Works well when:

- the export has a normal `imsmanifest.xml`
- modules/weeks are represented in the manifest
- content pages use real HTML headings (`h1` through `h4`)
- assignments/discussions are linked into modules by D2L resource codes
- course metadata is supplied through command flags when the export does not
  include it clearly

Known limits:

- course number, course title, and term are best supplied by CLI flags
- styled-but-not-semantic headings may not segment cleanly
- coded due dates are not rendered because they are term-relative and can be
  misleading outside the course offering
- quiz-level instructions/settings are surfaced when linked, but full
  quiz/question-bank contents, answer keys, and pool-origin evidence are not
  translated into blueprint rows
- Creator+ practices are surfaced only when an HTML iframe points to a readable
  local `.practice.json`; the blueprint includes title/type/count/scoring/source
  metadata and authored instructions/prompts, not full answer/feedback review
- visual styling is translated into review cues, not exact CSS recreation;
  source callouts, dropdowns, embeds, and separators are preserved when detected,
  but decorative layout wrappers are intentionally ignored
- LTI/external-tool payloads are not deeply interpreted
- missing learning-objective alignment is not inferred

The intended result is a deterministic, auditable review surface. It is not a
final instructional-design approval and not a full course rebuild.
