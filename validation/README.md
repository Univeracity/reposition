# Local qualification

Locally checked on Python 3.12.3:

- 40 unit/integration tests passed with optional dependencies installed, including
  matching-span retention, conservative relationship wording, and budgeted
  benchmark citation metrics.
- Installed-wheel core tests passed in a fresh environment without third-party
  runtime dependencies; five optional tests were skipped.
- Ruff lint and formatting checks passed.
- Source and wheel builds passed. Distribution checks exclude caches and local
  agent coordination files, and confirm license attribution is included.
- FTS5 and TF-IDF each reproduced all 165 frozen query rankings, over two isolated
  repository corpora. In total: 330 ranking checks, 660 complete token-budgeted
  outputs and 2,734 independently checked source excerpts.

[Extraction receipt](extraction.json) binds source implementation and input
digests. [Wheel smoke receipt](wheel-smoke.json) confirms installation, CLI
behavior and actionable optional-dependency errors outside the checkout.

These are local checks. The GitHub Actions workflow targets Python 3.10, 3.12
and 3.14; see [CI runs](https://github.com/Univeracity/reposition/actions/workflows/ci.yml)
for remote results. Historical labels remain provisional; extraction parity is
not a new human evaluation of review quality.
