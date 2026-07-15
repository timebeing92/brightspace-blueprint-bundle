# Brightspace Blueprint Bundle

Turn a Brightspace/D2L course export into a source-traceable course blueprint:
Markdown, DOCX, JSON, activity workbook, optional rubric grids, inventory files,
and QA reports.

This bundle can run entirely from normal command-line scripts. No AI service is
required to use it. You can also point an agent at the bundle and ask it to run
the scripts for you; it will likely ask permission to install the required
Python packages and system libraries, then execute the same pipeline described
below. `AGENTS.md` is optional maintainer guidance for a human contributor or an
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
  - `pdf2image` for optional DOCX visual render QA.
  - `jsonschema` to validate the generated model against the blueprint schema.
- Optional render-QA system tools:
  - LibreOffice/`soffice` to convert DOCX to PDF.
  - Poppler utilities on `PATH` for PDF-to-PNG rendering.

`bootstrap.sh` installs the Python packages into `.venv`. LibreOffice and
Poppler are system tools and must be installed separately.

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
- `<label>__rubrics.xlsx` and `<label>__rubrics.json` - rubric review workbook
  and canonical `coursecraft.rubrics/1` grids when `rubrics_d2l.xml` is present.
- `<label>__course_qa.md` and `.json` - QA warnings, notes, and diagnostics.
- `<label>__course_structure.md` and `.json` - reconstructed module/topic tree.
- `<export>__inventory.md` and `.json` - package file inventory.
- `<export>__manifest_probe.md` and `.json` - manifest/resource inspection.
- `<label>__docx_structure.md` and `.json` - structural check of the rendered
  DOCX (relationships, hyperlinks, tables, titles); pure Python, on by default.
- `README.md` - short per-run guide to the generated files.
- `render_qa/` - optional PDF/PNG render check output when requested.

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
| 8 | `blueprint_to_docx.py` | Render DOCX from the same model used for Markdown. |
| 9 | `docx_structure_qa.py` | Structural check of the rendered DOCX against the model (relationships, hyperlinks, tables, titles). Pure Python, on by default; `--skip-docx-structure-check` opts out. |
| 10 | `render_blueprint_docx.py` | Optional visual deep check: DOCX to PDF/PNG pages plus render summary (needs LibreOffice + Poppler). |

Markdown is always produced. DOCX is produced when `python-docx` is available.
Detected source callouts, notes, cards, and styled highlight sections are kept
as review cues; when the source HTML wraps a full section, the generated
Markdown/DOCX wraps the section content, not only the callout title.

## Common Commands

Run with default QA:

```bash
bash run_blueprint.sh /path/to/export.zip --label course-review
```

Run visual DOCX render QA:

```bash
bash run_blueprint.sh /path/to/export.zip \
  --label course-review \
  --render-docx-check
```

Opt in to live external-link checks:

```bash
bash run_blueprint.sh /path/to/export.zip \
  --label course-review \
  --check-external-links
```

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
| `--no-docx` | Produce Markdown and JSON only. |
| `--render-docx-check` | Render the generated DOCX to PDF/PNG pages in `render_qa/`. |
| `--docx-section-layout top\|left` | DOCX weekly section-label layout; `top` is the default. |
| `--quiet` | Suppress companion script output. |
| `--step-timeout N` | Per-step timeout in seconds (default 900; `0` disables). |
| `--progress-events` | Emit NDJSON progress events (`coursecraft.progress/1`) instead of step banners. See `knowledge/PROGRESS_EVENTS_CONTRACT.md`. |

During a normal run each step prints a one-line banner (`== [3/7] Reconstruct
course structure ==`). Wrapper tools that want structured live progress should
use `--progress-events` and read one JSON event per stdout line; the final
`run_end` event carries the actual output paths and summary counts, including
rubric JSON/workbook paths when rubric XML was present.

## How To Review A Run

Start with the generated DOCX or Markdown blueprint. Then check the QA report:

- `Warnings` identify likely review issues, such as missing alt text with the
  topic title and asset path when available.
- `Notes` identify package-scope findings, such as hidden manifest-linked files
  and unlinked package files that may explain large exports or indirect assets.
- Front-matter diagnostics say which candidate sources were checked when a field
  remains `Needs review`.
- External links are inventoried by default. They are fetched only when
  `--check-external-links` is used.

For visual layout QA, run `--render-docx-check` and inspect
`render_qa/render_summary.json` plus the generated PNG pages.

## Repository Layout

```text
brightspace-blueprint-bundle/
├── README.md                  <- user guide
├── LICENSE                    <- AGPL-3.0-or-later license text
├── COMMERCIAL.md              <- commercial licensing and services note
├── CHANGELOG.md               <- implementation history
├── AGENTS.md                  <- optional maintainer/assistant guidance
├── requirements.txt           <- Python dependencies for .venv
├── requirements-dev.txt       <- adds pytest for the test suite
├── bootstrap.sh               <- macOS/Linux setup
├── bootstrap.ps1              <- Windows setup
├── run_blueprint.sh           <- normal pipeline wrapper
├── scripts/                   <- pipeline, model builder, renderers, QA tools
├── tests/                     <- pytest suite (golden run over examples/ + unit tests)
├── knowledge/                 <- pipeline and Brightspace package references
├── schemas/blueprint_schema.json
├── schemas/progress_events_schema.json
├── schemas/rubrics_schema.json
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
