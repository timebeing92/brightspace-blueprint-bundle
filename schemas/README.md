# Portable Contract Family

The bundle owns the portable producer copies of the CourseCraft contracts it
emits:

- `coursecraft.activities/1` — additive activity, grade, condition, join, and
  diagnostic evidence;
- `coursecraft.structure/1` — additive manifest-tree, HTML-topic, unknown-node,
  and diagnostic evidence. An HTML topic's `body_segments` are open
  `{heading, level, blocks, text}` chunks; since 2026-10-13 a segment whose
  heading sits inside its first block (a panel that opens with a heading it
  owns) carries the additive marker `heading_owner: "panel"`, and a consumer
  that prints the segment heading itself must not print it again in front of
  that panel;
- `coursecraft.run/1` — source, producer, contract, step, artifact, and checksum
  historical receipt, also retained for unreadable-input failure evidence;
- `coursecraft.run/2` — numeric-org-unit/manifest lineage, classified observed
  aliases, receipted linked-syllabus fetch policy and source-backed title evidence
  for readable packages. Existing run/1 schema bytes are preserved;
- `coursecraft.blueprint/5` — the blueprint model (`blueprint_schema.json`).
  Revision 5 (Workbench, 2026-10-07) adds `bold`, `italic`, and `underline`
  flags on runs (present only when true); a `table` block kind with `rows` of
  cells, each cell a list of runs, and `header_rows`; a `module` field on
  before-week sections, which the renderers group as one `Module:` row per
  module; and, on a run, `link_kind` and `link_title` for a resolved internal
  link written in the packager's bracket form `[link to kind: Title | words]`
  with an empty `href`. The `level` on a `label` block is the source heading
  level (1-6), written one to one as Word Heading 2 to Heading 6; Heading 1 is
  the blueprint frame. The previous revision is kept as
  `blueprint_schema_4.json` so `/4` models still validate;
- `coursecraft.rubrics/1` and `coursecraft.progress/1` — established rubric
  and progress contracts.

Activity and structure contracts intentionally permit additive fields and
unknown vendor kinds. Consumers should warn on unfamiliar fields or versions
before hard failure unless the meaning or joins are unsafe. A version bump is
required for removal, renaming, changed meaning, changed join semantics, or new
required structure that breaks an existing consumer.

The sanitized files under `examples/` demonstrate unknown shapes that remain
valid without being coerced into a known semantic or advertised as buildable.
