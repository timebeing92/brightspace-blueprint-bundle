# Roadmap / backlog

Candidate improvements, roughly ordered. Sourced from the 2026-07-09 audit;
items graduate to `CHANGELOG.md` when done. Receipts over milestones: an item
is only done when a real run demonstrates it.

## Output & UX

- **Quiet the step dumps.** `export_inventory.py` and `manifest_probe.py`
  print their entire markdown reports (every file/resource/item) to stdout
  during a pipeline run. Default should be a short summary — the full
  documents already land in the bundle folder. Keep a `--print-full` opt-in.
- **End-of-run summary in plain runs.** The `run_end` progress event carries
  weeks / QA counts / needs-review; print the same summary as text at the end
  of a default (banner-mode) run.
- **Remove the legacy `__cps_blueprint.md` alias** once no consumer needs it
  (it is a byte-identical duplicate of `__blueprint.md`).

## Packaging

- **`pyproject.toml` + console entry points** (`blueprint-bundle build …`),
  replacing the implicit `sys.path` package. Keep `run_blueprint.sh` as a
  wrapper.
- **Unify `--label`/output-dir behavior** of `export_inventory.py` and
  `manifest_probe.py` with the other scripts (today one bundle mixes
  `sample_export__*` and `sample_course__*` stems).

## Extraction quality

- **Log dedupe suppressions.** `topic_skip_match_key` drops a weekly topic at
  ≥0.92 token overlap with a before-week page; when it fires, name the
  suppressed page in diagnostics so boilerplate-heavy real pages aren't lost
  silently.
- **UTC date rollover in QA.** `parse_iso_date` strips `Z` and takes the
  date; D2L stores UTC, so near-midnight deadlines can produce false
  "outside term window" breaks (see
  `knowledge/BRIGHTSPACE_PACKAGE_STRUCTURE_AND_IMPORT_NOTES.md`).
- **Word-boundary heading routing.** `RESOURCE_KEYS` match bare substrings —
  "Social Media Policy" routes to Assigned Reading & Multimedia via "media".
- **Week detection beyond `week|module|unit`.** Courses using
  "Topic/Session/Lesson N" fall to manifest order with no week numbers.
- **External-link check exception coverage.** Catch SSL/connection-reset
  variants that don't subclass `URLError` so one bad host can't crash QA.
- **h5/h6 segmentation.** Deep semantic headings currently under-segment into
  Overview.

## Runner (sibling repo)

- Windows launcher (`blueprint_wizard.ps1`) — the wizard itself is pure
  Python and already computes the Windows venv path.
