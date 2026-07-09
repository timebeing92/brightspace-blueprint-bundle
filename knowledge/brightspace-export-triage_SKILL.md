---
name: brightspace-export-triage
description: Use when inspecting a Brightspace/D2L export ZIP or unpacked folder, creating inventories, tracing manifest relationships, or orienting before any structural edit.
---

Treat the export as evidence first.

## What to do
1. Find `imsmanifest.xml` and major D2L payload XML files.
2. Inventory the package before making assumptions.
3. Distinguish extracted facts from inference.
4. Keep wording intact when surfacing instructional content.
5. Write review-friendly outputs into `workspace/review/`.
6. Keep hidden/faculty-facing manifest items visible with a clear note rather
   than dropping them silently.
7. Do not edit the only raw copy of the export.

## Priorities
- preserve raw evidence
- map major joins and resources
- surface ambiguity explicitly
- create a calm reviewer surface

## Typical outputs
- package inventory markdown/json
- manifest probe markdown/json
- course structure markdown/json
- course activities markdown/json/workbook
- course QA markdown/json
- blueprint markdown/json/DOCX when running the full bundle pipeline
- concise unresolved-questions note

## Repo note
- In this bundle, consult `knowledge/BRIGHTSPACE_PACKAGE_STRUCTURE_AND_IMPORT_NOTES.md`
  for package patterns and `knowledge/SCRIPT_PIPELINE_AND_PACKAGE_CONTEXT.md`
  for the full `run_blueprint.sh` pipeline.
