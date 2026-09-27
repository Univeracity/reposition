# Initial contracts

The useful default is local repository evidence search. Keep acquisition,
retrieval, evidence rendering, and review decisions separate so a cache/TUI/agent
workflow can use only the parts it needs.

Each database belongs to one `owner/name`. Repository statistics and overlapping
item numbers never mix. Snapshot replacement is transactional, removes deleted
records, and requires an explicit replacement flag. Empty snapshots require a
second explicit flag. Malformed input is validated before changing evidence.

The snapshot identity is SHA-256 of canonical normalized records and coverage,
sorted by record ID. It includes actual text, titles, source identity, offsets,
and coverage. A changed record produces a different snapshot even if its supplied
revision is unchanged. The digest describes this cache; it is not an upstream
Git revision, remote freshness guarantee, or complete-repository claim.

FTS5 uses `unicode61`, title weight 3 and text weight 1. Component filtering is
applied before the record candidate cap; stable record-ID ties precede distinct
item selection. Leading numeric anchors select summaries. Plain queries are
literal any-term expressions with balanced quoted phrases. No raw FTS operators
or SQL from user text are executed. BM25 is corpus-relative, not a probability.

Candidate caps and item limits are independent. More matching components can
consume the cap without filling the requested distinct-item count; diagnostics
make this visible. The cap detects an additional matching candidate rather than
guessing truncation merely because the pool is exactly full.

The renderer uses unmodified source slices. Offsets are Unicode code points,
half-open `[start,end)` ranges, with both record-relative and source-relative
positions. Excerpt hashes cover original UTF-8 text. Budget accounting includes
the whole formatted text. JSON containers and diagnostic record payloads are
outside that text budget. Too-small budgets fail explicitly.

Literal relationship navigation is one hop, restricted to discussion components.
Qualified external references retain their repository identity. File patches are
excluded to avoid interpreting code hashtags as discussion links. “Claims fixes”
does not establish a fix, equivalence, or duplicate. Missing targets remain visible.

Optional TF-IDF uses SQLite's token stream, smooth corpus-wide IDF, raw term
frequency, 3:1 field weighting and L2 normalization. Queries never participate in
fitting. The cache lives on an `Index` instance and is rebuilt on snapshot change;
it is not a persisted vector cache or incremental updater. Phrases are flattened
for cosine scoring, so this method does not enforce FTS phrase eligibility.

Read operations bind metadata, ranking and hydrated records in one SQLite read
transaction. Exports verify their normalized digest against the stored snapshot.
Databases are application-owned caches, not signed or tamper-proof evidence stores.
