# Reposition development

Reposition puts repository work in context through local search and traceable
evidence. Keep the default usable without models, services, or credentials.

- Read README.md and docs/design.md before changing retrieval contracts.
- Keep repositories isolated; never mix their ranking statistics implicitly.
- Bind snapshots to actual input content. A supplied revision is evidence
  metadata, not proof that the current bytes match a prior snapshot.
- Preserve source URI, revision, coverage, offsets, and excerpt hashes.
- Retrieval relevance and literal references do not establish duplicate fixes.
- Keep experimental methods opt-in and compare them against the FTS5 default.
- Use synthetic fixtures in source control. Do not commit user caches, private
  conversations, credentials, or large experimental artifacts.
- Run `python -m unittest discover -s tests -v` and `ruff check .` for changes.
- For sustained work use Develish if available; coordination files stay local.
- Public publishing and external messages require the owner's instruction.
