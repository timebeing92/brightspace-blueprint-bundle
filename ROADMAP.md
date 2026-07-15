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

- **Web version — DECIDED 2026-07-13 after the avenues review** (mockups +
  matrix delivered; full record in the workbench `DEVELOPMENT_ROADMAP.md`
  addendum): **Gradio on a Hugging Face Space** (amended 2026-07-13: plain
  SDK Space first, Docker on record as the custom-front-end/hard-pinning
  escape hatch), wrapping this bundle's CLI as a subprocess and streaming
  `coursecraft.progress/1` into a themed step board. The Space pins a
  bundle **release tag** (first tag: v1.0.0) and fetches it at startup —
  no vendored pipeline copies. **Space v1 BUILT 2026-07-13** (repo
  `coursecraft-workshop-space/`, sibling of this one; pushed to the
  private github.com/timebeing92/coursecraft-workshop-space so any
  machine can pull it): hub + wizard bench, live step board from progress
  events, sample-course demo run, zip download, failure card; then four
  design passes the same evening — full-screen journey UX (hero splash
  whose plan lines draft themselves, sticky rail, phased reveal, upload
  peek card with title prefill), TUI-style step pacing (≥1.7 s dwell),
  the rail wizard as a real progress indicator (his table's blueprint
  fills with step completion under a spectral aura), and a rebuilt
  results card. Verified end-to-end locally against the real v1.0.0 tag
  (success + failure paths). **DEPLOYED 2026-07-13:**
  hf.co/spaces/timebeing92/coursecraft-workshop (private; flip public =
  launch). Live E2E verified through the Space API: sample course ran all
  8 steps on the Space, bundle v1.0.0 fetched from GitHub inside the
  container, blueprint zip (DOCX included) downloaded back through the
  API. Deploy note for the record: HF's free tier now hosts ONLY ZeroGPU
  hardware (cpu-basic is PRO-gated), and ZeroGPU's supervisor kills any
  app with no `@spaces.GPU` function — the app registers a never-called
  probe function to satisfy the watchdog; the pipeline itself runs pure
  CPU and consumes no GPU quota. Registers: midnight for the
  wizard tool page, vellum for the hub shell. Hub: web = one Space with the
  "CourseCraft Workshop" hub shell from day one (blueprint bench first);
  TUI = separate wizards sharing `ui.py`, launcher deferred to the Textual
  trigger. Access model DECIDED: PUBLIC Space (no HF accounts needed for
  anyone; portfolio-friendly; uploads stay session-scoped/unretained;
  flippable to private if posture changes). Build note: include a
  "try it with the sample course" demo button using the committed sample
  export.
- **Alternatives on record (revision paths, user request 2026-07-13):**
  (a) self-hosted FastAPI — university VM (best institutional privacy) or
  small VPS; progress contract maps to SSE; doubles as the localhost-web
  fallback. (b) Pyodide fully client-side — privacy endgame, $0 static
  hosting, export never leaves the browser; blocker: the orchestrator is
  subprocess-per-step (WASM has no subprocess) so it needs an in-process
  step adapter; no LibreOffice ever (structural DOCX QA still works).
  (c) Rejected permanently: HF-for-scripts + GitHub-Pages-for-UI split —
  a private Space API needs a token that cannot live in static JS, and a
  custom front end can be served from the Space itself.
- **LibreOffice streamlining.** LibreOffice is needed only for the optional
  DOCX visual render QA (DOCX→PDF; Poppler rasterizes). No faithful
  pure-Python DOCX renderer exists. **Structural DOCX QA landed 2026-07-13**
  (`docx_structure_qa.py`, default-on — see CHANGELOG), so the visual pass
  is now a rarely-needed deep check. Remaining: the web version bakes
  soffice into its container image (zero user install); the TUI keeps the
  optional prompt-gated install for the visual pass.
- **One-download install — tooling DONE 2026-07-13 (runner-side).**
  `scripts/make_release_bundle.py` builds `dist/blueprint-wizard-vX.Y.zip`
  from both repos' git HEADs (sibling folders, top-level double-click
  launchers, START_HERE.txt); verified end-to-end from a fresh unzip incl.
  first-run venv + dependency installs. `install_blueprint_wizard.sh` is the
  curl-able alternative (clones both repos, ff-only update on re-run);
  runner README documents all three install pathways with rationale.
  Remaining: push and cut the GitHub release; the installer becomes broadly
  useful if the repos go public (user open to this).

## Catalog Support Contracts

Decision note 2026-07-14: `../coursecraft-catalog/` is moving toward a
user-friendly Catalog Reader plus Archivist/Catalog Wizard, with this bundle as
the portable extraction engine. The bundle should expose stable facts and
progress; it should not own catalog tags, relation judgments, program maps, or
reader UI.

Macro ecosystem note 2026-07-15: `../ECOSYSTEM.md` now records the cross-repo
map, evolutionary contract posture, run-identity target, release/pin checklist,
and future tool branches. For this repo, the next coherence work is to make
catalog-relevant artifact contracts explicit without freezing current package
shapes too early.

Course-as-a-whole catalog note, 2026-07-14: the catalog reader should be able to
assemble a whole-course evidence view from bundle outputs: modules, activities,
grade items, rubrics, resources, LTI stubs, release conditions, QA diagnostics,
and source provenance. The bundle responsibility is to make those facts
complete, stable, and discoverable through explicit artifacts/progress outputs;
the downstream catalog decides how to join, filter, review, and present them.

Backlog items needed before broad catalog intake:

- **Rubric JSON promotion complete, 2026-07-15.** The standalone bundle now
  ships `extract_rubrics_to_workbook.py`, `schemas/rubrics_schema.json`, an
  optional rubric extraction step, `<label>__rubrics.json`, and
  `<label>__rubrics.xlsx` when `rubrics_d2l.xml` is present. The additive
  `coursecraft.progress/1` fields are `outputs.rubrics_json`,
  `outputs.rubrics_workbook`, and `summary.rubrics`.
- **Rubric DOCX review surfaces complete, 2026-07-15.** The main blueprint
  DOCX now appends a Rubric Appendix when rubric JSON exists, and the pipeline
  also emits `<label>__rubrics.docx`. `coursecraft.progress/1` exposes
  `outputs.rubrics_docx`, so runner/web/catalog intake can find the document
  without deriving filenames.
- **Version activity and structure contracts without over-freezing them.**
  Catalog ingest depends directly on `<label>__course_activities.json` and
  `<label>__course_structure.json`. Add schemas or explicit contract notes for
  those shapes, but keep the contracts evolutionary: version the envelope, keep
  required fields minimal, allow additive optional fields and extension objects,
  document unknown/diagnostic/stub behavior, and bump versions only when field
  meaning, join semantics, or required structure changes in a downstream-breaking
  way.
- **Make artifact discovery explicit.** Continue expanding
  `coursecraft.progress/1` `run_end.outputs` when new catalog-relevant
  artifacts are added, so intake tools do not glob. Rubric paths are now
  explicit.
- **Expose run identity.** Provide enough version data for the catalog
  `ingest_runs.script_versions` field and future refresh matching: bundle
  release tag and/or git SHA, schema versions, source export/package identity,
  emitted artifact list, optional step status, and any workbench-origin contract
  version. This can be a separate `run_identity.json`, an explicit
  `coursecraft.progress/1` `run_end` block, or both.
- **Keep LTI honest.** Continue surfacing LTI/external-tool links as typed
  structure facts with provenance. Rich vendor/tool semantics belong only after
  upstream evidence exists; downstream catalog views can label the stubs.

Rubric promotion handling note, 2026-07-15: the contract release is implemented
in the bundle, staged in the local TUI runner as progress/output display, and
aligned with the catalog's existing `coursecraft.rubrics/1` ingest path. The
DOCX review layer is now present in both the main blueprint and a standalone
rubric document. Remaining release hygiene: run the workbench drift check after
staged-snapshot sync, then cut or bump the bundle release tag and update any
hosted/web pins.

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
