# Optional triage-o-mator integration

This proposed patch adds four offline commands to triage-o-mator's `bin/cache`.
For a trial on triage-o-mator's `write-operations` branch, use validated commit
[`f956c1299ab7f28107aa4db7943d97ce0baa4f2a`](https://github.com/EFrMG/triage-o-mator/tree/f956c1299ab7f28107aa4db7943d97ce0baa4f2a).
The patch also supports its original base,
[`dddc487660ffda17ba7e3626c9d9be72e29a6dce`](https://github.com/EFrMG/triage-o-mator/tree/dddc487660ffda17ba7e3626c9d9be72e29a6dce).
The evidence reference and seven core cache/install modules are unchanged
between those commits. The same patch applies to both without modification.
Existing commands work without Reposition. The patch does not alter acquisition,
TUI notifications, ledger decisions or GitHub actions.

To try the complete synthetic retrieval workflow before modifying triage-o-mator,
run `./reposition demo --cache` from a Reposition checkout. It needs no installation
and handles the fixture, IDs and source-verification steps automatically.

Install the supplied `reposition-0.2.0.dev0-*.whl` in the Python environment used
by `bin/cache`, or install the corresponding Reposition checkout with
`python -m pip install -e /path/to/reposition`. Use the supplied build: the
unrelated package with the same PyPI name is not this project.

From a clean upstream checkout:

```sh
git apply --check /path/to/reposition/integrations/triage-o-mator/cache-retrieval.patch
git apply /path/to/reposition/integrations/triage-o-mator/cache-retrieval.patch
```

From the selected triage install:

```sh
bin/cache --expected-repo OWNER/REPO search-index --snapshot INVENTORY_SNAPSHOT --component summary
bin/cache --expected-repo OWNER/REPO search-index --corpus CORPUS_ID
bin/cache --expected-repo OWNER/REPO query --corpus CORPUS_ID \
  --query 'terminal fails after suspend'
bin/cache --expected-repo OWNER/REPO retrieve --corpus CORPUS_ID \
  --unit UNIT_ID --checkpoint INDEX_CHECKPOINT
bin/cache search-info --corpus CORPUS_ID
```

The bridge honors install repository/host identity, requires a bound cache and
refuses `--cache` namespace overrides. Global `--host` and `--expected-repo` go
before the command. Other flags match Reposition's `cache-*` commands, including
`--replace`, `--rebuild`, filters and `--cursor`. New views are stored beside the
cache rather than consuming its acquisition budget. Installation remains optional.

The patch updates `docs/evidence.md`, `docs/evidence-reference.md` and
`prompts/prepare-analysis.md` with bounded query-then-retrieve guidance. For a TUI,
consume the structured JSON, retain scope and checkpoint, show gaps and omissions,
and open larger verified fragments only for selected candidates. Retrieval itself
does not create approval or closure suggestions.

Validate a trusted checkout with the patch applied:

```sh
python /path/to/reposition/scripts/check_triage_compat.py \
  --checkout /path/to/triage-o-mator --output compatibility.json
```

This uses a temporary synthetic install and upstream EvidenceCache/corpus
publication. It checks legacy search without Reposition, missing-package behavior,
identity guards, verified query/retrieve and unchanged authoritative cache bytes.
It makes no acquisition requests. Do not run it against untrusted executable
checkout code. Real-cache size/latency and review decisions require separate trials.

The [validation receipt](../../validation/triage-write-operations.json) records
the checked upstream commit, patch SHA-256 and successful compatibility results.
It covers the pinned commit; later branch changes require another check.

See the [complete retrieval contract](../../docs/cache-integration.md).
