# Roadmap / backlog

Candidate improvements, roughly ordered. Sourced from the 2026-07-09 audit;
items graduate to `CHANGELOG.md` when done. Receipts over milestones: an item
is only done when a real run demonstrates it.

## Output & UX

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

## Delivery

(Decision record 2026-07-13 in the workbench `DEVELOPMENT_ROADMAP.md`: the
local/TUI runner and a web version are dual-track peers — neither replaces
the other.)

- **Web version — decision deferred until an avenues review (user request
  2026-07-13).** Before committing to a stack, review the options across
  configuration, UX/UI, and pipeline hosting: Gradio on Hugging Face Spaces
  is one candidate; the previously discussed split (Hugging Face for script
  execution + GitHub Pages for the UX/UI front end) is another; aesthetics
  matter — produce a mockup for review before any decision. Also open, for
  BOTH web and TUI: one central multi-tool hub vs separate single-tool
  interfaces as more functionality (beyond blueprints) folds in later.
  Privacy posture review (private hosting, no retention — course exports
  are institutional content) happens when the web track starts.
- **LibreOffice streamlining.** LibreOffice is needed only for the optional
  DOCX visual render QA (DOCX→PDF; Poppler rasterizes). No faithful
  pure-Python DOCX renderer exists. Plan: web version bakes soffice into
  its container image (zero user install); the TUI keeps the current
  optional prompt-gated install; candidate improvement for both — a
  pure-Python structural DOCX QA (python-docx read-back: styles, tables,
  hyperlinks, images) as a lightweight default so the visual pass is rarely
  needed.
- **One-download install — tooling DONE 2026-07-13 (runner-side).**
  `scripts/make_release_bundle.py` builds `dist/blueprint-wizard-vX.Y.zip`
  from both repos' git HEADs (sibling folders, top-level double-click
  launchers, START_HERE.txt); verified end-to-end from a fresh unzip incl.
  first-run venv + dependency installs. `install_blueprint_wizard.sh` is the
  curl-able alternative (clones both repos, ff-only update on re-run);
  runner README documents all three install pathways with rationale.
  Remaining: push and cut the GitHub release; the installer becomes broadly
  useful if the repos go public (user open to this).

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
- **Review the rest of the package-scope exclusion list.** `orgunitconfig.xml`
  was added to `CORE_PACKAGE_FILES` 2026-07-10 (it was flagged as an unlinked
  file in every package). `syllabus_d2l.xml` and `conditionalrelease_d2l.xml`
  are also standard never-manifest-linked files the pipeline itself consumes —
  decide whether they belong in the exclusion too.
