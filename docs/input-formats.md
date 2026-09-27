# Offline inputs

`reposition index` supports three explicit formats. Import does not crawl GitHub
or silently guess arbitrary cache layouts. Coverage is a source declaration;
Reposition preserves it and does not infer completeness from a nonempty array.

## Native records (default)

```json
{
  "schema": "reposition.snapshot.v1",
  "repository": "example/packages",
  "coverage": {"items": "bounded sample", "reviews": "not acquired"},
  "records": [{
    "id": "40-summary",
    "item": "40",
    "component": "summary",
    "title": "Unexpected archive bytes",
    "text": "The index and remote object have different digests.",
    "uri": "https://github.com/example/packages/issues/40",
    "source_revision": "opaque source revision or sha256 of retained source",
    "source_start": 0,
    "partial": false,
    "coverage": {"comments": "not acquired"}
  }]
}
```

Record IDs are unique inside the repository. Item identifiers are strings; records
with the same item are grouped for search. Components are strings; `summary`,
`comments`, and `files` are the initial conventions. URI and source revision are
required. `partial: null` means unknown. `source_start` is a nonnegative code-point
offset into the original component source. Optional `text_sha256` is checked on
import; export includes it. Unknown record fields fail rather than disappearing.

## GitHub cache (`--format github --repo owner/name`)

Input is an array of items or `{ "items": [...], "coverage": {...} }`. Each item
has a distinct positive `number`, nonempty `title`, and optional `body`, `url` or
`html_url`. Inline `comments` are an array with `body`, optional `id` and URL.
A REST integer `comments` count contains no comment evidence and is not indexed.
Inline `files` are an array with `filename` or `path`, optional `patch` and
`blob_url`; API patches can be partial even when present. Per-item `coverage`
is preserved on every derived record.

Revisions are hashes of the retained source objects, not claims about an upstream
commit. File URLs use a retained head SHA when available; otherwise they point
back to the item. Bodies are not replaced with the string `None`. Absent comments,
files, reviews, and checks remain unknown or explicitly not acquired.

## Experiment component cache (`--format components --repo owner/name`)

This adapter accepts the original experiment's `documents.json` arrays: `id`,
`item`, `repo`, `component`, `title`, `text`, `uri`, `sourceRevision`,
`span.charStart`, `span.charEnd`, `excerptSha256`, and `componentPossiblyPartial`.
Text hashes and bounds are checked. `--manifest manifest.json` preserves per-item
`componentCoverage`; a retained `documentsSha256` must match the input bytes.
The adapter needs no original runtime or harness code.

Unsupported cache formats should be translated into native records by a small
caller-owned adapter. Keep item, component, revision and coverage explicit.

## Immutable triage-o-mator evidence cache

Use the separate `cache-index/query/retrieve/info` commands for the version-1
content-addressed cache containing `cache.json`, `snapshots/`, `objects/` and
`corpora/`. No flattening or install-script execution is required. The reader
validates source contracts and checksums, exact repository identity, selected
snapshot/corpus membership, progress and returned source fragments. Its UTF-8
byte offsets and complete JSON budget differ from normalized code-point/text
budgets. See [immutable-cache retrieval](cache-integration.md).
