# Portable Contract Family

The bundle owns the portable producer copies of the CourseCraft contracts it
emits:

- `coursecraft.activities/1` — additive activity, grade, condition, join, and
  diagnostic evidence;
- `coursecraft.structure/1` — additive manifest-tree, HTML-topic, unknown-node,
  and diagnostic evidence;
- `coursecraft.run/1` — source, producer, contract, step, artifact, and checksum
  receipt;
- `coursecraft.blueprint/4`, `coursecraft.rubrics/1`, and
  `coursecraft.progress/1` — established blueprint, rubric, and progress
  contracts.

Activity and structure contracts intentionally permit additive fields and
unknown vendor kinds. Consumers should warn on unfamiliar fields or versions
before hard failure unless the meaning or joins are unsafe. A version bump is
required for removal, renaming, changed meaning, changed join semantics, or new
required structure that breaks an existing consumer.

The sanitized files under `examples/` demonstrate unknown shapes that remain
valid without being coerced into a known semantic or advertised as buildable.
