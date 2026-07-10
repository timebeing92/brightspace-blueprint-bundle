# Pipeline progress events (`coursecraft.progress/1`)

Status: contract ratified 2026-07-09. Schema: `schemas/progress_events_schema.json`.

## What this is

`scripts/build_blueprint_bundle.py` reports run progress two ways:

- **Default — human step banners.** One line before each pipeline step:

  ```text
  == [3/7] Reconstruct course structure ==
  ```

- **Opt-in — NDJSON events** with `--progress-events`. Each milestone is one
  JSON object per stdout line, and the human banners are suppressed. This is
  the contract wrapper tools (the blueprint runner wizard, a future local web
  UI) consume to render live progress without scraping human-oriented text.

## Event flow

```text
run_start                       (once; carries schema id, planned step labels, total)
  step_start → step_end         (per step, 1-based index; step_end carries seconds)
  ...
run_end                         (once; status ok|error)
```

A successful `run_end` carries the **actual output paths** (`bundle_dir`,
markdown/json/docx/workbook/qa paths) and a summary (`weeks`, `diagnostics`,
`needs_review` count, QA `breaks`/`warnings`/`notes` counts). Consumers must
use these paths rather than re-deriving output locations from the label —
label-derivation rules are the pipeline's own business and may change.

On failure the failing step emits `step_end` with `status: "error"` and a
`message`, followed by a terminal `run_end` with `status: "error"`.

## Rules for consumers

1. Parse each stdout line as JSON; any line that does not parse (or lacks an
   `"event"` key) is pass-through output from a step — display or log it,
   don't error on it.
2. Ignore unknown keys and unknown event types; additions are minor-version
   moves within `coursecraft.progress/1`.
3. Breaking changes (renamed/removed fields, reordered flow) bump the schema
   id to `coursecraft.progress/2`.

## Rules for maintainers

- The emitting code is `StepProgress` + `_emit_event` in
  `scripts/build_blueprint_bundle.py`. Keep it, the schema file, and this note
  in step.
- Without `--progress-events` the stream must stay exactly: banners plus the
  historical step/summary output — existing scripted consumers rely on it.
- `tests/test_pipeline_features.py::test_progress_events_stream` guards the
  flow shape; extend it with any new event or field.
