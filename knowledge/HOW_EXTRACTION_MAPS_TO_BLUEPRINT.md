# How the export maps to the blueprint

This workbench workflow turns a Brightspace/D2L **course export** into a
flat-file **course blueprint** shaped after
`workspace/reference/blueprints/templates/Course Blueprint Template 2020 CGPS.docx`.
It is an *extraction + review* tool, not a generator: it surfaces what is in the
package and marks what it cannot find. It never invents instructional content.

## Design stance: mirror, don't reconstruct

The blueprint template encodes a *backwards-design* intent (LOs → aligned
assessments → supporting resources). A built course is the forward artifact and
that design logic is baked into pages, not labeled. Rather than guess intent, we
**mirror the course's own structure inside the blueprint frame**: the per-week
inner structure comes from the course's own page headings, with a small alias
table normalizing only the few universal buckets. See the README's *Design
philosophy* section for the full rationale.

## Pipeline

`scripts/build_blueprint_bundle.py` orchestrates the extraction scripts, then renders one
structured model to Markdown and DOCX:

1. `export_inventory.py` — component counts, recognizable D2L XML, asset counts.
2. `manifest_probe.py` — organizations/items/resources, identifierref usage,
   likely quiz/HTML resources, suspicious hrefs.
3. `reconstruct_course_structure.py --extract-html` — the module/topic tree plus
   HTML topic bodies as **`body_segments`**: each page is split by its own
   `<h1>`–`<h4>` headings, and each segment is parsed into formatting-preserving
   **blocks** — paragraphs and list items with link-aware runs
   (`{kind, level, runs:[{text, href}], meta?}`). Paragraphs, bullet lists,
   links, horizontal rules, dropdown summaries, selected callout/card visual
   cues, image placeholders, attached-file placeholders, and video/iframe embeds
   survive; Creator+ practice iframes that reference a local `.practice.json`
   via `data-file` are expanded into lightweight practice metadata blocks.
   Embedded images are not parsed, OCR'd, or embedded in the blueprint: they
   render as stand-in blocks with alt text when available, otherwise a no-alt
   placeholder plus the source path. Non-HTML course files such as `.docx`,
   `.pdf`, or spreadsheet/presentation files render as attached-file references
   instead of decoded body text. Hidden manifest items render as short
   hidden-item placeholders naming the object/type/path where available, but
   their bodies/details are not unpacked. `<script>`/`<style>` and
   page-template artifacts (e.g. "Basic Page - No Banner") are dropped.
4. `extract_course_activities.py` — dropbox folders, discussions, D2L
   checklists, quiz-level instructions/settings, and grade joins. Assignment
   instructions, discussion descriptions, quiz instructions, and checklist
   items are parsed into the same blocks (`instructions_blocks`,
   `description_blocks`, quiz `instructions_blocks`, checklist `blocks`), so
   their formatting is preserved too.
5. `course_qa_report.py` — severity-tiered export QA (optional, `--skip-qa`).
6. **model build** → `<label>__blueprint.json` (see
   `workspace/reference/schemas/blueprint/blueprint_schema.json`).
7. **render** → `<label>__blueprint.md` and `<label>__blueprint.docx`.

## How each week is assembled

Each detected week/module gathers its HTML topics, and every topic's
`body_segments` are routed by heading:

| Blueprint row | Fed by (heading alias, case-insensitive substring) |
| --- | --- |
| **Overview** | intro text before the first heading + headings matching `overview / introduction / welcome / start here / intro / orientation` |
| **Learning Objectives** | headings matching `objective / outcome(s) / goals / competenc / students will be able / swbat` |
| **Assigned Reading and Multimedia** (Resources) | headings matching `reading / resource / material / multimedia / media / video / watch / listen / textbook / reference / required / optional / explore` — each kept under its **own** course label (e.g. "Required Resources", "Multimedia") |
| **Assignment(s) and Instructions** | dropbox folders joined to the module by `resource_code`; quiz quicklinks joined to `quiz_d2l_*.xml` by rCode/resource code, with quiz-level instructions and settings when present. Numeric due dates are NOT encoded (see below) |
| **Discussion Board Prompts** | discussion topics joined by `resource_code` |
| **Checklist** | D2L checklist tool quicklinks joined to `checklist_d2l.xml` payloads by `resource_code`/`rCode`; HTML headings matching `checklist` are also preserved here. Only shown when present. |
| **Other course sections** | any remaining page or heading (Next Steps, Case Study, Instructions…) preserved under its own **"Page › Heading" path label**, built from the page title plus the h1–h4 heading hierarchy, so sections from different pages never merge. Markdown and DOCX add a horizontal divider between different source pages, while keeping multiple subsections from the same page grouped without extra rules. Lesson/practice pages usually live here; embedded Creator+ practices are shown inside the section where their iframe appears. Only shown when present. |

Ordering matters in the alias table: **checklist is checked first**, then
**objectives before resources** (so "Learning Objectives" doesn't match the
`material` substring), and resources before overview.

There is one module-level structural override: if a week/module has both an
explicit overview page and a separate learning-materials/resources page (for
example, `Week 1 Overview` plus `Week 1 Learning Materials and Resources`),
resource-like headings inside the explicit overview page stay in **Overview**.
This prevents overview pages with embedded videos or resource callouts from
bleeding into Assigned Reading and Multimedia when the module already has a
dedicated materials page. Combined pages such as `Week 1 Overview and Learning
Materials` are not locked; they still split internal resource headings into the
resources row because that single page is carrying both roles.

Output order is a blueprint architecture choice: **Overview → Learning
Objectives → Assigned Reading and Multimedia / Learning Materials →
Assignments → Discussions → Checklist → Other course sections**. Learning
materials sit immediately after the LOs so a SME can review whether the selected
resources actually support the stated outcomes before moving into assessment
and discussion details.

Renderer dividers are intentionally scoped. Assignment(s) and Discussion Board
rows add horizontal dividers between separate D2L activity objects, such as two
dropbox folders, a dropbox folder followed by a quiz, or two discussion topics.
The tool does **not** use that object divider to split headings inside one
content page or resource page.

Top-level non-week modules/pages that appear before the first detected week are
preserved in a separate **Before Week 1: Additional Resources and Information**
section between Course Introduction and Course Content. This keeps orientation,
syllabus, project overview, roadmap, and other setup pages visible without
forcing them into Week 1. Before-week material is grouped by page: the page
title is shown once in the section table, and the page's internal headings
become local labels inside that page section. Course Introduction pages that
already populate the global Course Introduction field are not repeated in the
before-week section.

If the same source topic is also linked or copied into a later week/module, the
blueprint keeps the before-week copy and suppresses the weekly duplicate. The
dedupe key is either the same href or the same page title plus matching body
signature, which covers D2L courses that place a global roadmap or assignment
overview in both "Getting Started" and Week 1; the duplicate is noted in
diagnostics instead of being split into Week 1 `Other course sections`.

Every section routed from a page also carries provenance in the JSON model:
`source_page` (the page title) and `level` (the heading level, 0 for a whole
page). Intro content that sits *before* a page's first heading follows the
page's own classification (a "Learning Materials" page intro belongs with
resources, not the week overview).

### The Learning Objectives fallback

If a week's pages have **no** objectives heading, Learning Objectives is left
empty (`Needs review`) and that text stays in **Overview** — the tool does not
guess which sentences are objectives. When the course *does* use an objectives
heading, that block is split out cleanly into the LO row.

### Topics with no headings

A page with no `<h1>`–`<h4>` headings is a single chunk. It is classified by the
**topic title** (e.g. a "Learning Materials" page → Resources). A week-titled or
untitled page reads as the week's own narrative (Overview); any other distinctly
titled page is preserved as its **own labeled section** under "Other course
sections" rather than merged anonymously into Overview. Content is never
dropped.

## Course-level front matter

Description / Materials / Outcomes / Introduction are pulled course-wide:
Materials and Outcomes prefer a matching **segment** (a resources or objectives
heading anywhere in the course) and fall back to a whole topic whose title
matches; Description and Introduction match by topic title. Empty → `Needs review`.

## Joins

- **Module ↔ activity join is by `resource_code` / `rCode`.** Dropbox folders,
  discussions, quizzes, and D2L checklists are placed under the week whose
  manifest quicklinks share their resource code. Assignments and discussions
  that are not joined land in an **Unplaced Activities** section. For normal
  course exports, checklist and quiz XML are filtered to manifest-linked
  payloads so stale objects do not flood the review artifacts; component-only
  packages with no manifest quicklink evidence can still expose their payloads,
  and any included-but-unplaced activity remains visible for review.

## Known limitations (tell the reviewer)

- **Headers are not in the package.** Course number / term come from CLI flags.
- **Segmentation depends on real headings.** A course that styles headings as
  bold paragraphs instead of `<h1>`–`<h4>` will under-segment (content stays in
  Overview). This is intentional: we follow real structure, not guesswork.
- **Numeric due dates are deliberately not encoded.** The coded date fields in
  the XML are term-relative (they shift every time the course is offered), so
  encoding them would be misleading. The day-of-week cadence that assignments,
  discussions, quizzes, and checklists actually communicate is written into the
  page / instructions HTML, so it is already carried in the extracted
  assignment, discussion, quiz, and overview text. (The UTC-timestamp caveat in
  `BRIGHTSPACE_PACKAGE_STRUCTURE_AND_IMPORT_NOTES.md` is why those coded fields
  are not trustworthy to surface directly.)
- **Question-bank contents are not blueprint content.** The blueprint includes
  quiz-level instructions, gradebook/points joins, attempts/time-limit settings,
  and section/question-count summaries. Full question text, answer keys,
  question-library matching, and pool-origin evidence belong in the dedicated
  quiz review extractor.
- **Creator+ practice details are metadata-level only.** If an HTML iframe points
  to a local `.practice.json`, the blueprint includes the practice title, type,
  item/question/category counts, scoring status, source file, and authored
  description/instructions/prompts. Full answer-key or feedback review belongs
  in a dedicated Creator+ review pass, not the blueprint.
- **Visual styling is translated into cues, not recreated.** The extractor
  preserves semantic visual structure that helps a reviewer read long pages:
  callouts/notes/card-like sections, dropdown summaries, video/media embeds,
  and horizontal rules. It does not attempt to reproduce exact Brightspace CSS,
  fonts, layout, or every decorative wrapper.
- **Formatting is preserved, not reflowed.** Paragraphs, bullet lists, and links
  are carried through as authored (links render live in both Markdown and DOCX).
  Fine inline styling (bold/italic, fonts, colors) and images are not carried —
  images become an `[image: alt]` marker when they have alt text. Very unusual
  page markup can still mis-block, but the common D2L shapes are handled.
- The blueprint is a **review surface**, not an instructional-design approval.

## Reading the output

- `Needs review: not found in export extraction.` — a single field had no
  confident source.
- `None found in export extraction.` — a list field had no items.
- `Extraction Notes` — diagnostics carried from the structure/activity passes.
  Package-scope notes about hidden-linked or unlinked files are completeness
  diagnostics; they are not inserted as instructional blueprint content.
