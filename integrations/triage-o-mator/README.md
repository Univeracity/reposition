# Optional triage-o-mator integration

This proposed patch adds four offline commands to triage-o-mator's `bin/cache`.
The first draft PR targets current master at
[`80cdcdd77d875c4fdcd96f3367f2f8a7d253e63a`](https://github.com/EFrMG/triage-o-mator/tree/80cdcdd77d875c4fdcd96f3367f2f8a7d253e63a).
Earlier compatibility checks cover the `write-operations` commit
[`f956c1299ab7f28107aa4db7943d97ce0baa4f2a`](https://github.com/EFrMG/triage-o-mator/tree/f956c1299ab7f28107aa4db7943d97ce0baa4f2a).
The patch also supports its original base,
[`dddc487660ffda17ba7e3626c9d9be72e29a6dce`](https://github.com/EFrMG/triage-o-mator/tree/dddc487660ffda17ba7e3626c9d9be72e29a6dce).
The historical receipts cover the earlier patch; the refreshed patch and
[current-master receipt](../../validation/triage-master.json) name the current
integration base. Recheck later upstream changes before adoption.
Existing commands work without Reposition. The patch does not alter acquisition,
TUI notifications, ledger decisions or GitHub actions.

To try the complete synthetic retrieval workflow before modifying triage-o-mator,
run `./reposition demo --cache` from a Reposition checkout. It needs no installation
and handles the fixture, IDs and source-verification steps automatically.

This bridge supports Reposition `0.2.0.dev1` and cache format version 1. Install
the tested engine into the Python environment used by `bin/cache`:

```sh
python -m pip install --no-deps \
  'git+https://github.com/Univeracity/reposition.git@10bd0641b50d514984dad6dee480f140ab86ee44'
```

The bridge rejects other engine versions until compatibility is checked. Use
this source or the matching supplied wheel: the unrelated package with the same
PyPI name is not this project. Installation may need network access; subsequent
indexing, queries, retrieval and metadata inspection remain offline. Reposition
has no third-party runtime dependencies.

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
It also checks `search-info` against foreign host/name/stable IDs, explicit scope,
and literal search before/after bridge use. Metadata inspection does not audit
source checkpoints or payloads; its JSON reports both verification flags false.
It makes no acquisition requests. Do not run it against untrusted executable
checkout code. Real-cache size/latency and review decisions require separate trials.

The [validation receipt](../../validation/triage-master.json) records
the checked upstream commit, patch SHA-256 and successful compatibility results.
It covers the pinned commit; later branch changes require another check.

See the [complete retrieval contract](../../docs/cache-integration.md).

The patch includes two workflow tests and a separate CI job with the pinned
optional engine; the ordinary CI job retains the no-package path. Run checks on
both sides whenever cache/adapter behavior changes. The
[review and cost trial](../../docs/triage-review-trial.md) contains reproducible
same-symptom/different-cause examples, query/retrieval costs, memory and storage
measurements, and the boundaries of synthetic validation. Real-backlog trials
remain the next qualification step before TUI or suggestion workflows.
