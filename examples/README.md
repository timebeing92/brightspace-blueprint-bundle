# Worked example

`sandbox_demo__blueprint_bundle/` is a **real end-to-end run** of this bundle
against a small Brightspace sandbox export (the author's own test course — safe
to share). It is here so you can see the output shape before running your own.

It was produced with:

```bash
bash run_blueprint.sh path/to/sample-sandbox-export.zip \
  --label sandbox_demo \
  --course-number "SBX 100" --course-title "Sample Sandbox" --term "Summer 2026"
```

## What to look at

- **`sandbox_demo__blueprint.md`** — the flat Markdown blueprint (CGPS layout).
- **`sandbox_demo__blueprint.docx`** — the same blueprint styled like the template.
- `sandbox_demo__blueprint.json` — the structured model both renderers consume
  (`schemas/blueprint_schema.json`).
- The `*_inventory`, `*_manifest_probe`, `*_course_structure`, `*_course_activities`,
  and `*_course_qa` files are the companion extraction artifacts.

## Try the bundled sample export

This folder also includes `sample_export.zip`, a tiny safe Brightspace-style
fixture. Use it to confirm setup before running a real course export:

```bash
bash run_blueprint.sh examples/sample_export.zip \
  --label sample_course \
  --course-number "SAMPLE 100" --course-title "Sample Course" --term "Demo Term"
```

## What this example demonstrates

The sandbox export is deliberately small but exercises the main joins:

- a Week 1 module with two HTML content topics (overview + reading),
- a graded dropbox assignment with a rubric
  (`RTv1 Essay One` → `10 pts`, rubric),
- a graded discussion (`RTv1 Week 1 Discussion`),
- an **ungraded** discussion that isn't joined to the module, which lands in the
  **Unplaced Activities** section rather than being dropped,
- the reading topic mirrored into **Resources** under its own label
  (`Reading Notes`).

It is also a clean demo of the **Learning Objectives fallback**: this week's
overview page has no objectives heading, so Learning Objectives shows
`Needs review` and the intro text stays in Overview — the tool does not guess
which sentences are objectives. (For the opposite case — a course whose weekly
pages *do* use an `Objectives` heading, so the LO row fills in and Resources
split into `Readings` / `Multimedia` — run the tool on a fuller export.)

Most front-matter fields show `Needs review` because this sandbox has no matching
course-level topics — an honest example of how missing data is surfaced, not
invented.
