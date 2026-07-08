# Worked example

`sample_course__blueprint_bundle/` is a **real end-to-end run** of this bundle
against the tiny synthetic Brightspace-style fixture at `sample_export.zip`. It
is here so you can see the output shape before running your own export.

It was produced with:

```bash
bash run_blueprint.sh examples/sample_export.zip \
  --label sample_course \
  --course-number "SAMPLE 100" --course-title "Sample Course" --term "Demo Term" \
  --output-dir examples
```

## What to look at

- **`sample_course__blueprint.md`** — the flat Markdown blueprint.
- **`sample_course__blueprint.docx`** — the same blueprint rendered as DOCX.
- `sample_course__blueprint.json` — the structured model both renderers consume
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

The fixture is deliberately small but exercises the main joins:

- two weekly modules,
- multiple HTML content topics,
- an explicit learning-objectives topic,
- resource/reading content routed into Assigned Reading and Multimedia,
- a graded assignment joined through D2L dropbox/grade data,
- a discussion topic joined through D2L discussion data,
- an unplaced discussion surfaced rather than dropped.

Most front-matter fields show `Needs review` because this fixture has no matching
course-level topics. That is intentional: missing data is surfaced, not invented.
