# Reposition

**Put repository work in context.**

Reposition searches saved issues, pull requests, comments, and changed files,
then brings cited evidence into your next review. Find related work, inspect
references to prior fixes, and see what the cache does and does not cover.

This is an early local tool with a Python API and CLI. The default uses SQLite
FTS5 and needs no model, server, credentials, or third-party Python package.
Nothing is posted to GitHub, and nothing from a cache is executed.

## Try it

Python 3.10+ with SQLite FTS5 support is required. From a checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

reposition index examples/github-cache.json --format github --repo example/packages
reposition search 'same filename older bytes index digest'
reposition search 'archive digest' --component comments --json
reposition related 40
reposition info
```

The included data is synthetic. Use one database per repository:

```sh
reposition index my-cache.json --format github --repo owner/repository --db repo.sqlite
reposition search 'migration runs an older helper' --db repo.sqlite --chars 8000
reposition index refreshed-cache.json --format github --repo owner/repository --db repo.sqlite --replace
reposition export --db repo.sqlite > normalized-cache.json
```

`github` input accepts a JSON array from `gh issue list --json
number,title,body,url,updatedAt,comments` or `gh pr list` with corresponding
fields. REST lists can also be used. PR changed-file patches must be included
in the cache to search them. Lists are bounded snapshots, not proof of complete
repository coverage. See [input formats](docs/input-formats.md) for the native
record format and the earlier experiment's component-cache adapter.

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
licensed, with the adapted Vyral query policy under Apache-2.0 as described in
[NOTICE](NOTICE). This project is separate from the GitHub-to-Notion CLI also
called Reposition.
