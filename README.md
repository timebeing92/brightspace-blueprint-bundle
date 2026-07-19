# Brightspace Blueprint Bundle

Current release: **1.3.0**

Turn a Brightspace/D2L course export into a source-traceable course blueprint:
Markdown, DOCX, JSON, activity workbook, optional rubric grid documents,
inventory files, and QA reports.

This bundle can run entirely from normal command-line scripts. No AI service is
required to use it. You can also point an agent at the bundle and ask it to run
the scripts for you; it will likely ask permission to install the required
Python packages, then execute the same pipeline described below. `AGENTS.md` is
optional maintainer guidance for a human contributor or an
AI coding assistant working on the scripts; it is not part of the runtime
pipeline.

## Quickstart

```bash
# 1. One-time setup in this folder (macOS/Linux).
bash bootstrap.sh

# 2. Run a Brightspace export through the blueprint pipeline.
bash run_blueprint.sh /path/to/course-export.zip \
  --label my-course \
  --course-number "ABC 123" \
  --course-title "Course Title" \
  --term "Fall 2026"
```

By default, the output folder is:

```text
workspace/review/<label>__blueprint_bundle/
```

The wrapper reuses the bundle-local `.venv` on later runs. If `.venv` is missing,
`run_blueprint.sh` starts `bootstrap.sh` automatically.

## What You Need

- Python 3.11 or newer.
- A Brightspace/D2L export ZIP, or an unpacked export folder with
  `imsmanifest.xml` at the export root.
- Python packages from `requirements.txt`:
  - `openpyxl` for the activity workbook.
  - `python-docx` for DOCX rendering.
  - `jsonschema` to validate the generated model against the blueprint schema.

`bootstrap.sh` installs the complete normal-run environment into `.venv`.
LibreOffice, Poppler, and `pdf2image` are not required.

Windows setup:

```powershell
.\bootstrap.ps1
.\.venv\Scripts\python.exe scripts\build_blueprint_bundle.py C:\path\to\export.zip
```

## What The Output Contains

A normal run creates one course-specific bundle folder. The main reviewer-facing
files are:

- `<label>__blueprint.docx` - Word review document.
- `<label>__blueprint.md` - flat Markdown version of the same blueprint.
- `<label>__blueprint.json` - structured model used by both renderers.
- `<label>__course_activities.xlsx` - extracted activities workbook.
- `<label>__rubrics.docx`, `<label>__rubrics.xlsx`, and
  `<label>__rubrics.json` - rubric review document, workbook, and canonical
  `coursecraft.rubrics/1` grids when `rubrics_d2l.xml` is present. The main
  blueprint DOCX also includes a Rubric Appendix.
- `<label>__course_qa.md` and `.json` - QA warnings, notes, and diagnostics.
- `<label>__course_structure.md` and `.json` - reconstructed module/topic tree.
- `<export>__inventory.md` and `.json` - package file inventory.
- `<export>__manifest_probe.md` and `.json` - manifest/resource inspection.
- `<label>__docx_structure.md` and `.json` - structural check of the rendered
  DOCX (relationships, hyperlinks, tables, titles); pure Python, on by default.
- `<label>__pipeline_status.md` and `.json` - completion or partial-delivery
  status, successful artifacts, failed/degraded components, and review
  guidance. Start here when a run is marked `partial`.
- `<label>__run_identity.json` - portable `coursecraft.run/1` receipt recording
  logical and transport source fingerprints, producer release/commit, contract
  checksums, step outcomes, emitted files, and artifact checksums.
- `README.md` - short per-run guide to the generated files.
- `render_qa/` - advanced maintainer PDF/PNG preview output when explicitly
  requested.

Generated bundles are course-specific review artifacts. The repository source
does not need to include generated `workspace/` or `output/` folders.

## Normal Pipeline

`run_blueprint.sh` calls `scripts/build_blueprint_bundle.py`, which runs the
steps below in order:

| Step | Script | Purpose |
| --- | --- | --- |
| 1 | `export_inventory.py` | List files in the export and summarize package contents. |
| 2 | `manifest_probe.py` | Inspect `imsmanifest.xml`, resources, visibility, and links. |
| 3 | `reconstruct_course_structure.py --extract-html` | Rebuild the module/topic structure and extract HTML page content, headings, visual cues, styled callout/card sections, media placeholders, file placeholders, image alt evidence, hidden/faculty-facing notices and content where readable, and Creator+ practice metadata. |
| 4 | `extract_course_activities.py` | Extract assignments, discussions, quiz-level settings/instructions, checklists, grade/rubric joins, and activity metadata. |
| 5 | `extract_rubrics_to_workbook.py` | Optional: extract rubric grids to `<label>__rubrics.xlsx` and `<label>__rubrics.json` when `rubrics_d2l.xml` is present. |
| 6 | `course_qa_report.py` | Produce QA warnings and notes; external URL fetching is opt-in. |
| 7 | `build_blueprint_bundle.py` | Assemble the `coursecraft.blueprint/4` JSON model. |
| 8 | `blueprint_to_docx.py` | Render DOCX from the same model used for Markdown; appends a Rubric Appendix when rubric JSON exists. |
| 9 | `rubrics_to_docx.py` | Optional: render `<label>__rubrics.docx` from the same `coursecraft.rubrics/1` JSON. |
| 10 | `docx_structure_qa.py` | Structural check of the rendered DOCX against the model and optional rubric appendix (relationships, hyperlinks, tables, titles). Pure Python, on by default; `--skip-docx-structure-check` opts out. |
| 11 | `render_blueprint_docx.py` | Advanced maintainer preview only: DOCX to PDF/PNG pages for human inspection (needs `requirements-render.txt`, LibreOffice, and Poppler). |

Markdown is always produced. DOCX is produced when `python-docx` is available.
If a recoverable extractor, rubric, QA, DOCX, or render component fails, the
pipeline continues with conservative fallbacks where possible, marks the run
`partial`, preserves every recoverable artifact, and states in
`run_end.delivery` whether the primary documents usably mirror the export —
`status` alone is a completion verdict, not a usability claim. Missing
output remains
unresolved evidence; it is never interpreted as proof that the source course
lacked that component. Malformed or unfamiliar rubric JSON is retained as
`<label>__rubrics_unparsed.json` instead of being discarded or passed into the
DOCX renderer as if it were valid.
Activity and structure JSON use the additive `coursecraft.activities/1` and
`coursecraft.structure/1` envelopes. Unknown vendor kinds and future fields
remain allowable evidence; breaking field meaning or join semantics requires a
new contract version. Both artifacts share the run and source identity recorded
in `coursecraft.run/1`, including on conservative partial-delivery fallbacks.
Detected source callouts, notes, cards, and styled highlight sections are kept
as review cues; when the source HTML wraps a full section, the generated
Markdown/DOCX wraps the section content, not only the callout title.

## Common Commands

Run with default QA:

```bash
bash run_blueprint.sh /path/to/export.zip --label course-review
```

Advanced maintainer render preview:

```bash
./.venv/bin/python -m pip install -r requirements-render.txt

bash run_blueprint.sh /path/to/export.zip \
  --label course-review \
  --render-docx-check
```

This conversion proves that LibreOffice can render the file and creates pages
for manual review. It does not automatically detect clipping, overflow, or
awkward pagination.

Opt in to live external-link checks:

```bash
bash run_blueprint.sh /path/to/export.zip \
  --label course-review \
  --check-external-links
```

The course's syllabus path is handled separately from broad link QA. During
normal HTML extraction, the bundle inventories both direct manifest syllabus
items and external syllabus anchors nested inside package-local welcome or
resources pages, then makes a non-fatal best-effort fetch from the recognized
`syllabi.une.edu` host. Exact fetched bytes, URL, SHA-256, discovery shape,
manifest placement, containing HTML href, and extracted
description/outcome/material headings are preserved. Package-local course
content remains primary; syllabus text fills a field only when that field was
otherwise empty. Use `--no-syllabus-fetch` for an inventory-only offline run.
Other syllabus hosts require an explicit `--syllabus-host` value.

Write to a different base output folder:

```bash
bash run_blueprint.sh /path/to/export.zip \
  --label course-review \
  --output-dir output
```

Use the alternate DOCX table layout:

```bash
bash run_blueprint.sh /path/to/export.zip \
  --label course-review \
  --docx-section-layout left
```

## Common Options

| Flag | Purpose |
| --- | --- |
| `--label NAME` | Output filename stem and bundle folder name. |
| `--course-title "Title"` | Visible blueprint title. |
| `--course-number "ABC 123"` | Optional metadata in the JSON model. |
| `--term "Fall 2026"` | Optional metadata in the JSON model. |
| `--output-dir DIR` | Base output directory; default is `workspace/review`. |
| `--bundle-dir DIR` | Exact output bundle directory. |
| `--skip-qa` | Skip `course_qa_report.py`. |
| `--check-external-links` | Fetch and check external URLs during QA. Offline inventory remains the default. |
| `--external-link-timeout N` | Per-URL timeout in seconds for external-link checks. |
| `--no-syllabus-fetch` | Inventory the manifest-linked syllabus without fetching supplemental evidence. |
| `--syllabus-timeout N` | Per-syllabus best-effort fetch timeout; default is 8 seconds. |
| `--syllabus-host HOST` | Add an allowed syllabus hostname; may be repeated. |
| `--no-docx` | Produce Markdown and JSON only. |
| `--render-docx-check` | Advanced maintainer preview: render the DOCX to PDF/PNG pages for human inspection. |
| `--docx-section-layout top\|left` | DOCX weekly section-label layout; `top` is the default. |
| `--quiet` | Suppress companion script output. |
| `--step-timeout N` | Per-step timeout in seconds (default 900; `0` disables). |
| `--progress-events` | Emit NDJSON progress events (`coursecraft.progress/1`) instead of step banners. See `knowledge/PROGRESS_EVENTS_CONTRACT.md`. |

During a normal run each step prints a one-line banner (`== [3/7] Reconstruct
course structure ==`). Wrapper tools that want structured live progress should
use `--progress-events` and read one JSON event per stdout line; the final
`run_end` event carries the actual output paths and summary counts, including
rubric JSON/workbook/DOCX paths when rubric XML was present. Its status is
`ok`, `partial`, or `error`; partial runs include structured component issues
and paths to the pipeline-status report.

## Maintainer Release Asset

Build a release tarball from one explicit, committed ref:

```bash
python3 scripts/make_release_asset.py --ref <full-bundle-commit>
```

The command refuses a dirty worktree by default. It writes a reproducible
`dist/brightspace-blueprint-bundle-vX.Y.Z.tar.gz`, a sidecar SHA-256 file, and
an embedded `RELEASE_MANIFEST.json` recording the source commit, contract
hashes, critical extractor hashes, and the gated linked-syllabus capability.
The release builder refuses a selected ref that advertises that procedure but
does not contain its primary-authority, non-fatal-fetch, and provenance hooks.

## How To Review A Run

Start with the generated DOCX or Markdown blueprint. Then check the QA report:

- `Warnings` identify likely review issues, such as missing alt text with the
  topic title and asset path when available.
- `Notes` identify package-scope findings, such as hidden manifest-linked files
  and unlinked package files that may explain large exports or indirect assets.
- Front-matter diagnostics say which candidate sources were checked when a field
  remains `Needs review`.
- External links are inventoried by default. They are fetched only when
  `--check-external-links` is used. The manifest-linked syllabus is the bounded
  exception described above: it is supplemental course evidence, fetched by
  default from recognized hosts, and can be disabled independently.

The default structural DOCX report is the normal verification surface. When a
renderer, template, or layout change warrants a manual compatibility preview,
install `requirements-render.txt`, run `--render-docx-check`, and inspect
`render_qa/render_summary.json` plus the generated pages.

## Repository Layout

```text
brightspace-blueprint-bundle/
├── README.md                  <- user guide
├── LICENSE                    <- AGPL-3.0-or-later license text
├── COMMERCIAL.md              <- commercial licensing and services note
├── CHANGELOG.md               <- implementation history
├── AGENTS.md                  <- optional maintainer/assistant guidance
├── requirements.txt           <- Python dependencies for .venv
├── requirements-render.txt    <- optional maintainer PDF/PNG preview dependency
├── requirements-dev.txt       <- adds pytest for the test suite
├── bootstrap.sh               <- macOS/Linux setup
├── bootstrap.ps1              <- Windows setup
├── run_blueprint.sh           <- normal pipeline wrapper
├── scripts/                   <- pipeline, model builder, renderers, QA tools
├── tests/                     <- pytest suite (golden run over examples/ + unit tests)
├── knowledge/                 <- pipeline and Brightspace package references
├── schemas/blueprint_schema.json
├── schemas/activities_schema.json
├── schemas/progress_events_schema.json
├── schemas/run_identity_schema.json
├── schemas/rubrics_schema.json
├── schemas/structure_schema.json
└── examples/                  <- sample export, worked output, config example
```

Ignored local folders:

- `.venv/` - local Python environment.
- `workspace/` - default generated run output.
- `output/` - optional generated run output.
- `__pycache__/`, `*.pyc`, `.DS_Store` - local runtime/system files.

## Running The Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
```

The suite runs the full pipeline against `examples/sample_export.zip` and
compares every artifact to the committed worked example, then covers the
shared helpers, wrapped-folder exports, and the progress-event stream. If an
intentional pipeline change alters the outputs, regenerate the worked example
with the command in `examples/README.md` and review the diff.

## Design Philosophy

The blueprint mirrors the exported course; it does not try to reconstruct an
ideal design model. A built Brightspace course already has design decisions
baked into pages, links, and activity objects. Guessing backwards from that
structure can produce confident but wrong output.

The stable review frame is:

- course front matter,
- Course Introduction,
- before-week orientation or setup material,
- repeatable week/module sections,
- extraction notes.

Inside each week, the tool preserves the course's own headings and page
provenance. A small alias table routes common material such as Learning
Objectives, Resources, Practice, Checklist, Assignments, and Discussions into
consistent rows. Material that does not fit those buckets stays visible under
`Other course sections`.

Missing fields remain `Needs review` or `None found`; the scripts do not invent
course descriptions, learning outcomes, due dates, or instructional language.

## Share Hygiene

For sharing the source bundle, use the repository files and exclude ignored local
folders such as `.venv/`, `workspace/`, and `output/`.

For sharing a course-specific result, share only the intended generated bundle
folder for that course. Do not add internal sidecar notes such as authoring
provenance, assistant activity logs, or maintenance review notes unless the
recipient explicitly asked for them.

## Suggesting Improvements

If you find a bug, confusing output, or a workflow improvement while using the
bundle, ask to push the improvement back to the repository. Proposed changes can
then be reviewed and added to `ROADMAP.md`.

## License

This repository is licensed under the GNU Affero General Public License,
version 3 or later (`AGPL-3.0-or-later`). See `LICENSE`.

Commercial licenses, hosted deployments, implementation support, institutional
integrations, training, maintenance, warranty, and procurement support are
available by agreement. See `COMMERCIAL.md`.

## Provenance

Many of the core scripts are derived from a more substantive Brightspace XML and
course tooling suite that has been in development since 2024 and is available
upon request. This repository packages the relevant extractors with the
blueprint-specific builder, DOCX renderer, schema, knowledge notes, and setup
wrappers needed for a standalone share bundle.

Current blueprint model schema: `coursecraft.blueprint/4`.
Current rubric grid schema: `coursecraft.rubrics/1`.
