# Immutable evidence-cache retrieval

Reposition's `cache-*` commands add disposable, offline FTS5 views over
triage-o-mator's version-1 evidence cache. They read its manifests, frozen corpus
plans, progress records and content-addressed objects without importing install
scripts. Acquisition, corpus selection, ledger decisions and approvals stay with
the caller. These commands are separate from the normalized `index/search` API.

## Try the complete workflow

From a Reposition checkout:

```sh
./reposition demo --cache
```

This creates a temporary synthetic cache, builds its index, searches all nine
components and retrieves a larger verified fragment from the first result.
The demo carries IDs and checkpoints between stages automatically and removes
its cache and index when finished. No installation is needed. An installed
package offers the same workflow as `reposition demo --cache`.

## Run the individual stages

Use these commands when you want to keep a fixture and inspect the JSON API.
[Install this checkout](usage.md#install-the-command-or-python-api), then generate
synthetic data:

```sh
python examples/triage_cache.py /tmp/reposition-demo-cache
```

Use the returned snapshot and corpus IDs in these commands:

```sh
reposition cache-index --cache /tmp/reposition-demo-cache --snapshot SNAPSHOT_ID
reposition cache-query --cache /tmp/reposition-demo-cache --snapshot SNAPSHOT_ID \
  --query 'terminal suspend' > discovery.json
reposition cache-index --cache /tmp/reposition-demo-cache --corpus CORPUS_ID
reposition cache-query --cache /tmp/reposition-demo-cache --corpus CORPUS_ID \
  --query 'src/terminal.py' --component files --field path --kind pr
reposition cache-retrieve --cache /tmp/reposition-demo-cache --snapshot SNAPSHOT_ID \
  --unit UNIT_ID --checkpoint INDEX_CHECKPOINT
```

`UNIT_ID` comes from `items[].fragments[].unit_id`; `INDEX_CHECKPOINT` is the
query response's `checkpoint`, not its `query_checkpoint`. Query excerpts are
already source-verified. Retrieve opens a larger source window for the selected
comment, field or diff region. Its default starts at the matched unit; an explicit
`--byte-offset 0` starts at that source boundary. Follow a fragment's returned
`continuation` parameters to read further without changing source selection.

An optional [triage-o-mator bridge](../integrations/triage-o-mator/README.md)
provides `bin/cache search-index`, `query`, `retrieve` and `search-info`, while
preserving existing literal search and readers when Reposition is absent.

## Scope and authority

Every view names exactly one immutable snapshot or frozen corpus. Repository
host, full name and known stable IDs must agree. Item stable-ID contradictions
fail; item kind and number are separate identities. Corpora use their exact
frozen inventory and recorded member snapshots. Pending members remain missing;
a newly recorded partial observation cannot be replaced by an older complete one.
A fixed snapshot bypasses current-item pointers and later acquisition history.

Snapshot IDs and source object checksums are verified before indexing. Query
checkpoints bind actual metadata, snapshot, plan and progress bytes, not supplied
revision strings or file timestamps. Reused manifest validation is keyed by its
actual byte digest. Returned fragments require fresh object checksum, size and
format checks, selected snapshot/component/revision binding, exact structural
locator reproduction, projection and excerpt hashes, and source URI reproduction.
Objects shared by returned fragments are read once per request; each source
binding is still checked. Only returned query objects are audited, not the whole
cache. An unchanged source reference is not a live freshness check.

Corpus continuation uses Reposition's stored-source checkpoint. It is deliberately
separate from triage-o-mator's transient runner/reader tokens; do not interchange
them. Source changes before or during retrieval refuse the response. A changed
corpus requires an explicit index replacement and a fresh query. An offline
reader does not lock acquisition or promise that data stays unchanged afterward.

## Projections and offsets

Projection version 1 retains original string slices:

| Component | Indexed fields and source boundary |
| --- | --- |
| `summary` | Title, body, label names and author login as separate fields |
| `comments` | One comment body |
| `files` | One filename, previous filename or changed-file patch |
| `diff` | One file header or raw diff hunk |
| `reviews` | One review body or state |
| `review_comments` | One inline comment body, path or diff hunk |
| `checks` | Check run/suite/status names, output, status, conclusion and kind |
| `closing_issues` | One explicit relationship URL |
| `timeline` | One event, body or available linked-issue field |

Long fields/regions split at deterministic UTF-8 byte and line ceilings, with
small overlap. Defaults: 4,096 bytes, 200 lines and 128 bytes overlap. Chunk policy
is recorded in the index. A phrase spanning a chunk boundary may be missed when
it exceeds the overlap. Raw diffs keep original bytes; this projector does not
claim to parse every Git diff variant or normalize quoted filenames.

JSON pointers locate original decoded string values. Their offsets count UTF-8
bytes **within that value**, not positions in the serialized JSON object. Raw
diff offsets count UTF-8 bytes in the object itself. All ranges are half-open.
Locators retain source record IDs where available, filename, line range, original
field size and comment/hunk boundary. Hashes cover exact original UTF-8 slices.

## Query and output policy

FTS5 `unicode61` provides case folding and its default diacritic normalization,
without stemming. Title and path columns have weight 3; other text has weight 1.
Balanced quoted phrases retain phrase eligibility; other terms use any-term
matching. Raw SQL and FTS operators are not accepted from query text. Leading
`#NUMBER` anchors prefer the selected summary and respect filters. BM25 scores
are relative ranking signals, not confidence or duplicate probabilities.

Repeated `--component` and `--field` flags select a union. Optional filters include
kind, state, label, author, ISO observation range (`--after`, `--before`) and
`--min-score`. Observation filters apply to the component's recorded fetch time;
state, label and author come from the selected summary. Missing summaries do not
satisfy those summary filters. There is no separate exact-phrase score boost
beyond FTS phrase eligibility and field weights in this version.

Filters apply before the candidate ceiling. Defaults are 1,000 ranked fragments,
ten diverse items and two fragments per item. `--fragments` explicitly switches
to fragment pagination. Diagnostics report candidate caps, per-item omissions,
budget omissions, unread groups, unique verified object bytes and coverage gaps.
Diversity operates inside the bounded candidate pool; dense matches can still
consume that pool. No-hit output cannot establish absence in missing, partial,
unindexed or capped evidence.

`--max-bytes` bounds the **complete compact UTF-8 JSON response plus its newline**,
including citations, metadata, coverage, diagnostics and continuation. Default:
12,000 bytes; maximum: 1 MiB. `budget.used_bytes` is measured from the wire format.
Pretty-printing it changes the budget. Query fragments default to 512 bytes;
retrieval fragments default to 4,096, with a maximum of 65,536. Groups that cannot
fit are omitted whole; a budget that cannot fit metadata or one available group
fails with an actionable error. This byte budget is not a tokenizer guarantee.

Query continuation binds the index publication, selected scope, query, filters,
ranking, candidate/item/fragment limits, byte budget, age policy and verification
ceiling. Keep those parameters identical when supplying `--cursor`. Pagination
advances through ranked groups rather than examined nonmatching cache members.
Retrieve uses the index checkpoint and returns boundary-relative byte offsets.

Source text is untrusted data, never instructions. A derived display title is a
bounded discovery label; verified fragments are the citable source material.
Recorded age and revision problems remain visible, including stale observations.
Relevance, shared paths and closing-issue links do not establish duplicate fixes.
Compare operative changes and discussion on both sides; human approval belongs
to the calling review workflow.

## Storage, limits and recovery

Default index paths are `CACHE/../reposition/snapshot-ID.sqlite` or
`corpus-ID.sqlite`. Keeping them beside the acquisition cache preserves
triage-o-mator's 5 GB cache budget: that budget counts every file inside its cache.
Index building rejects destinations inside the authoritative cache. A custom
`--db` must remain outside it; the caller owns sidecar storage and garbage collection.

The index stores FTS postings, normalized source bindings and compressed bounded
projections, rather than complete serialized source objects. Compressed data is
size-checked before decoding. Returned posting eligibility is checked against the
hashed projection; source payloads remain the authority. Each view has isolated
ranking statistics. Shared immutable-unit storage across views is not implemented.

Build limits default to 512 MiB and one million units. A rebuild may temporarily
need space for both old and new indexes. Object reads default to 64 MiB each;
metadata reads are capped at 16 MiB. Unique source bytes verified per request
are capped at 128 MiB. These are separate from the output budget. Increase the
explicit limits only when needed, or index/query fewer components. Component
objects still load and verify in full; RAM is bounded per object plus selected
metadata and the candidate pool, not a streaming payload reader.

A build lock rejects simultaneous publication. Temporary SQLite builds are
fsynced and atomically replaced only after source checkpoint verification; a
failed build keeps the prior view. Index metadata carries a checksummed publication
manifest. Read transactions bind ranking and resolution; held handles detect
replacement. Use `--replace` for a valid view refresh, `--rebuild` for explicit
corrupt derived-data recovery. Foreign repositories/databases and recognized
future versions require the correct database or an upgrade; recovery cannot
silently downgrade them. Remove an abandoned `.build-lock` directory only after
confirming its builder has stopped. Delete unneeded sidecars locally to reclaim
space without pruning evidence, progress or approval history.

## Measure before extending

```sh
python benchmarks/cache_retrieval.py --cache /tmp/reposition-demo-cache \
  --snapshot SNAPSHOT_ID --cases examples/cache-cases.json --output measurements.json
```

The harness records build time, source/index size, process peak RSS, first and
warm queries, verified source I/O, output bytes and supplied target recall. Its
literal baseline scans the same projected source pool with checksum verification;
it is not the existing CLI's examined-member pagination. OS caches are not cleared.
Synthetic target labels test retrieval, not decision quality or reviewer effort.

The included compatibility script exercises actual upstream publication/corpus
code and the optional bridge without network requests. See its integration guide.
Representative real-cache measurements and independently reviewed duplicate,
competing-fix and different-cause cases remain necessary before claiming decision
quality. Semantic search, reranking and automated review actions are outside this
cache API; the existing normalized API's optional TF-IDF experiment remains separate.
