# Brightspace → Course Blueprint (flat file)

Turn a **Brightspace/D2L course export** into a flat-file **course blueprint**
as **Markdown + DOCX**, shaped after the *2020 CGPS Course Blueprint* template.

This is a self-contained bundle: scripts, dependencies list, a one-command
setup, knowledge docs, the JSON schema, the format-reference template, and a
worked example. Hand it to a colleague (or point an AI agent at it) and it runs
without any other repo.

---

## Design philosophy: mirror, don't reconstruct

The blueprint template encodes a backwards-design intent — LOs first, then
assessments aligned to them, then resources chosen to support them. A built
course is the forward artifact, and by the time it's in Brightspace that design
logic is baked into pages, not labeled. Trying to reverse it back into the
framework means the script has to infer instructional intent ("is this text an
objective or an overview?"), and that's exactly the guessing that (a) needs the
sprawling config logic you don't want, and (b) produces confident-looking but
wrong output — the worst failure mode for a review surface.

So: mirror the course structure faithfully and present it clearly, inside the
blueprint's frame. Don't try to re-derive the backwards-design. If someone wants
to use this to drive a redesign, a faithful, well-organized mirror is a far
better starting point than a lossy reconstruction — they can see what's actually
there and restructure deliberately.

**In practice:** the course front matter and per-week frame come from the CGPS
template, but each week's inner structure is taken from the *course's own page
headings*. A small alias table pulls the few universal buckets (Learning
Objectives, Resources, Checklist) into consistent rows; every other page or
heading is preserved under its own label in an "Other course sections" row,
labeled with its "Page › Heading" path so distinct pages stay distinct. Every
extracted section also carries provenance (`source_page`, heading `level`) in
the JSON model. Learning Objectives are split into their own row only when the
course actually uses an objectives heading — otherwise that text stays in
Overview rather than being guessed at.

---

## Quickstart

```bash
# 1. one-time setup (creates .venv, installs deps)
bash bootstrap.sh

# 2. run it on an export (ZIP or unpacked export folder)
bash run_blueprint.sh /path/to/course-export.zip \
  --course-number "ABC 123" --course-title "Intro to Whatever" --term "Fall 2026"
```

Outputs land in `output/<label>__blueprint_bundle/`:

- **`<label>__blueprint.md`** — the flat Markdown blueprint
- **`<label>__blueprint.docx`** — DOCX styled like the CGPS template
- `<label>__blueprint.json` — the structured model both renderers use
- companion artifacts: inventory, manifest probe, course structure, activities
  (JSON/MD/xlsx), QA report, and a per-run `README.md`

A worked example is in `examples/` so you can see the output before running your
own.

New to the internals? Read
`knowledge/SCRIPT_PIPELINE_AND_PACKAGE_CONTEXT.md` for a plain-language
walkthrough of the pipeline, then a technical map of the scripts, JSON schema,
D2L XML evidence files, package structure, and joins.

> **Windows:** run `./bootstrap.ps1` in PowerShell, then
> `./.venv/Scripts/python.exe scripts/build_blueprint_bundle.py C:\path\to\export.zip`.

---

## What the blueprint contains

Follows the template's frame, populated from the export:

- Header: `Course # - Course Blueprint - Term` + course title
- **Course Description**, **Textbooks / Required Materials**, **Course Learning
  Outcomes** (single-column tables)
- **Course Introduction**
- One **table per week/module**:
  Overview · Learning Objectives · Assigned Reading and Multimedia (resources /
  learning materials, with the course's own sub-labels) · Assignment(s) and
  Instructions · Discussion Board Prompts · a **Checklist** row when the week
  has one · and an **Other
  course sections** row when the week has pages/headings that don't map to a
  standard bucket (each under its own "Page › Heading" label). Learning
  Objectives are split out only when the course used an objectives heading (see
  *Design philosophy* above). Numeric due dates aren't encoded (they're
  term-relative); the day-of-week cadence rides along in the extracted
  assignment/discussion text.
- In the DOCX, each weekly module is a full-width, single-column table.
  Scaffold labels sit in shaded header rows above the extracted content, not in
  a left column; bullets are native Word lists and links are clickable.

It is a **review surface**: extracted wording is source-derived, and anything not
found is marked `Needs review` / `None found` rather than invented. See
`knowledge/SCRIPT_PIPELINE_AND_PACKAGE_CONTEXT.md` for the layered pipeline and
package explanation, and `knowledge/HOW_EXTRACTION_MAPS_TO_BLUEPRINT.md` for the
full source→section map and known limitations (headers, due dates,
heading-driven mirroring).

---

## Common options

`scripts/build_blueprint_bundle.py` (called by `run_blueprint.sh`):

| Flag | Purpose |
| --- | --- |
| `--course-number` / `--course-title` / `--term` | Fill the header (not reliably in the package). |
| `--label NAME` | Output filename stem (defaults from the export name). |
| `--output-dir DIR` | Base output directory (default `output/`). |
| `--skip-qa` | Skip the QA report pass. |
| `--no-docx` | Markdown + JSON only. |
| `--quiet` | Suppress sub-script chatter. |

Markdown is always produced. If `python-docx` is missing, DOCX is skipped with a
warning and everything else still works.

---

## Requirements

- Python **3.11+**
- Dependencies (installed by bootstrap): `openpyxl`, `python-docx`
  (see `requirements.txt`)

---

## Layout

```text
brightspace-blueprint-bundle/
├── README.md                  ← you are here
├── AGENTS.md / CLAUDE.md      ← contract for an AI agent
├── requirements.txt
├── bootstrap.sh / bootstrap.ps1
├── run_blueprint.sh
├── scripts/                   ← pipeline + renderers (self-contained)
├── knowledge/                 ← pipeline guide, package structure, export→blueprint mapping, triage skill
├── schemas/blueprint_schema.json
├── reference/                 ← Course Blueprint Template 2020 CGPS.docx (format anchor)
├── examples/                  ← a real export run, end-to-end
└── output/                    ← generated bundles (created on first run)
```

---

## Provenance

The pipeline scripts are copied from the `coursecraft_workbench` master and run
unmodified here; `build_blueprint_bundle.py` and `blueprint_to_docx.py` are the
blueprint-specific layer built for this flat-file workflow. The workbench is the
upstream source of truth for the extractors.
