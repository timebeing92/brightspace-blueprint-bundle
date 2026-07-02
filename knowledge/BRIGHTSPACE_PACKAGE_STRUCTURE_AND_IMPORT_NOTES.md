# Brightspace Package Structure and Import Notes

This note is a durable repo reference for small Brightspace export / import packages.

It is intentionally split into:

- confirmed structure from inspected local exports
- cautious expectations for package types that still need direct evidence

## Confirmed Small-Package Pattern

For small component exports, Brightspace commonly uses a compact package shape such as:

- `imsmanifest.xml`
- `orgunitconfig/orgunitconfig.xml`
- one or more component payload XML files

Treat that as a working pattern, not a universal guarantee. Always inspect the actual export in front of you.

## What Each File Is Usually Doing

`imsmanifest.xml`

- resource index and routing layer
- declares which package files Brightspace should treat as resources
- is often the first place to inspect before assuming how many payloads exist

`orgunitconfig/orgunitconfig.xml`

- org unit metadata, not the instructional payload itself
- useful as package context, but usually not where the component content lives

component payload XML

- stores the actual checklist, rubric, quiz, discussion, assignment, or other object payloads
- may be a single file or several related files depending on package type

## Checklist Pattern We Can Reuse

Observed small checklist packages commonly use:

- root `<checklists>`
- one `<checklist>` per checklist object
- one or more nested `<category>` nodes
- multiple `<item>` children inside each category

Fields worth checking:

- object `id`
- object `resource_code`
- schedule flags
- item sort order
- due date fields

## Due Date Encoding Reminder

Brightspace commonly stores due dates as UTC timestamps.

Working implication:

- a local deadline may appear as a next-day UTC timestamp in exported XML
- do not assume the exported time string is already in local course time

## Import-Oriented Working Area

Use:

- `workspace/generated/import-ready-components/`

for:

- clean package copies
- minimal import test bundles
- component-specific working packages

Do not treat that lane as the raw evidence layer.

## Working Rules For Package Edits

1. Preserve the raw export.
2. Work on a copy in `workspace/generated/`.
3. Inspect the manifest before editing payload files.
4. Keep joins stable unless you are intentionally rebuilding them.
5. Validate edited XML after each structural change.

## Joins Worth Checking

Depending on package type, inspect these before editing:

- `identifier`
- `identifierref`
- `resource_code`
- `href`
- relative file paths
- Quicklink references where HTML content is involved

## Evidence Posture For Quiz and Question Library Work

Do not treat any single `questiondb.xml` from an arbitrary shell export as canonical by itself.

Safer posture:

1. export one known-good quiz package
2. inventory all payload files
3. trace object joins before editing
4. make the smallest working import bundle possible
5. only then scale to pooled question libraries and linked quizzes

If you rely on external reference exports, document:

- where they came from
- why they are considered trustworthy
- what version or environment they represent

Keep those exports in a reference or example lane rather than mixing them into live starter surfaces.

## Open Questions To Confirm When Mapping a New Package Type

- exact payload filenames used for the object type
- whether payloads are split across one or more XML files
- whether organizations are present in the manifest or resources-only is sufficient
- what IDs and cross-references must remain aligned
- whether related HTML assets, feedback fragments, or attachments live as separate files

## Recommended Posture For Package Work

- start with one exported component as evidence
- keep construction and validation traceable
- preserve IDs and joins rather than regenerating them casually
- favor small importable bundles over early full-course package assembly

## Related Notes

Use these alongside this note when relevant:

- `docs/project/QUIZ_PACKAGE_BUILD_AND_VALIDATION_METHOD.md`
- `docs/QUIZ_POOL_REVIEW_WORKFLOW.md`
- `docs/QUIZ_XML_EXPORT_INTAKE_CHECKLIST.md`
- `docs/project/QUIZ_REVIEW_OUTPUT_SCHEMA_NOTE.md`
- `docs/project/RUBRIC_PACKAGE_BUILD_AND_LINKING_NOTES.md`
