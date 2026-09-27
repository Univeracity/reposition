![Reposition — Put repository work in context.](docs/brand/reposition-readme-header.png)

Reposition searches saved issues, pull requests, comments, and changed files,
then brings cited evidence into your next review. Find related work, inspect
references to prior fixes, and see what the cache does and does not cover.

This is an early local tool with a Python API and CLI. The default uses SQLite
FTS5 and needs no model, server, credentials, or third-party Python package.
Nothing is posted to GitHub, and nothing from a cache is executed.

## Try it

Python 3.10+ with SQLite FTS5 support is required. From a checkout, one command
runs a complete synthetic example:

```sh
./reposition demo
```

It indexes the included sample in memory, shows cited search results and follows
a reference to a proposed fix. No installation, credentials or downloads are
needed, and no database is saved. To try another query:

```sh
./reposition demo 'advisory metadata'
```

For the immutable triage-cache workflow, `./reposition demo --cache` creates a
temporary fixture, indexes it, searches it and retrieves a larger verified source
fragment. It handles the snapshot, corpus and fragment IDs and removes its files
when finished.

Starting without a checkout? Clone it, enter it and run the demo:

```sh
git clone https://github.com/Univeracity/reposition.git
cd reposition
./reposition demo
```

On Windows, use `py reposition demo`; elsewhere, `python3 reposition demo` also
works. Installed packages provide the same demo as `reposition demo`.

Ready for your own data? See [using Reposition](docs/usage.md) for indexing,
installation and filters, or the [immutable-cache workflow](docs/cache-integration.md)
for triage-o-mator snapshots and corpora.

## Evidence you can inspect

- Title-weighted FTS5 search over source text, with component filters and one
  highest-ranked component per distinct item.
- Stable snapshot digests derived from actual records and coverage metadata.
- Source URI, revision, exact excerpt offsets, and SHA-256 hashes for each citation.
- Visible candidate caps, omitted hits, excerpt truncation, and unknown coverage.
- Incoming and outgoing literal references, including missing or external targets.
  A phrase such as “fixes #40” is retained as a source claim with its surrounding
  wording; negated or uncertain wording remains a generic reference.

Leading `#40` anchors select that item's summary before lexical results. Quoted
phrases are preserved; ordinary terms use any-term matching. Results are ranked
evidence, not automated duplicate decisions or calibrated confidence scores.

## Immutable triage caches

The `cache-index`, `cache-query` and `cache-retrieve` commands work directly with
triage-o-mator's immutable evidence cache. They provide component-aware ranked
fragments, exact snapshot/corpus selection, source verification and progressive
reads across all nine components. The complete JSON response has an enforceable
UTF-8 byte budget. Derived indexes live beside the acquisition cache and have
their own storage ceiling.

See the [workflow and contracts](docs/cache-integration.md), including resource
limits and an optional `bin/cache` integration patch. This API
keeps acquisition and approvals with the caller; real-cache and independent
review evaluation remain the next qualification steps.

## Optional comparisons and token budgets

```sh
python -m pip install -e '.[tokens,tfidf]'
reposition search 'archive integrity' --tokens 1024 --encoding o200k_base
reposition search 'archive integrity' --method tfidf --json
```

The budget measures the **complete formatted evidence text**, including citations,
headers, coverage, and footer. With `--json`, the structured JSON wrapper and full
ranked records are additional output; pass `evidence.text` to a bounded consumer.
Choose the actual consuming tokenizer encoding. Character budgets never claim
to be token budgets.

TF-IDF is an experimental comparison arm, fitted once per loaded snapshot using
the same SQLite tokenizer and title/text fields. It does not replace FTS5.
The initial experiments found mixed tradeoffs; [experiment notes](docs/experiments.md)
explain the evidence and its limits. Semantic search and Vyral integration are
possible extensions, not current runtime requirements.

## Python integration

```python
from reposition import Index, load_snapshot, render

snapshot = load_snapshot(
    "examples/github-cache.json", format="github", repository="example/packages"
)
with Index("repo.sqlite") as index:
    index.import_snapshot(snapshot, replace=True)
    result = index.search("same filename older bytes", component="summary")
    evidence = render(result, budget=8000)
    print(evidence.text)
```

A TUI or agent can use `SearchResult` and `Evidence` without parsing CLI prose.
Reposition is currently offline: acquisition, incremental updates, and review
actions belong to the calling workflow.

## Develop

```sh
python -m pip install -e '.[dev,tokens,tfidf]'
python -m unittest discover -s tests -v
ruff check .
python -m build
python scripts/check_dist.py
python scripts/smoke_wheel.py dist/*.whl
python benchmarks/evaluate.py examples/github-cache.json examples/cases.json \
  --format github --repo example/packages --methods fts5 tfidf --tokens 1024
```

The benchmark reports retrieval recall and citation retention separately at the
chosen complete evidence budget (`--chars` or `--tokens`). Each case includes the
rendered text, source-bound excerpts, omitted hits, and rendering time. Citation
retention does not establish that an excerpt supports a correct review decision;
independent review is still needed. The report's JSON wrapper is outside that
evidence budget.

See [design](docs/design.md) and [next steps](docs/roadmap.md). Source code is MIT
licensed, with the adapted Vyral query policy under Apache-2.0 and the adapted
triage-o-mator evidence validator under MIT as described in
[NOTICE](NOTICE). This project is separate from the GitHub-to-Notion CLI also
called Reposition.
