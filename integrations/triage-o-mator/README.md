# triage-o-mator integration

triage-o-mator maintains its optional Reposition bridge in
[`bin/_reposition.py`](https://github.com/EFrMG/triage-o-mator/blob/master/bin/_reposition.py).
Its [installation and supported-build guide](https://github.com/EFrMG/triage-o-mator/blob/master/docs/reposition.md)
owns the pinned engine version, bundle installation and bridge configuration.
Use that guide for the build supported by your triage-o-mator checkout.
Reposition does not carry a second bridge implementation or installation patch.

From a selected triage install, the bridge provides `bin/cache search-index`,
`query`, `retrieve` and `search-info`. Acquisition and GitHub actions remain with
triage-o-mator. See Reposition's [retrieval contract](../../docs/cache-integration.md)
for source verification, selection, checkpoints and response budgets.

Validate a trusted triage-o-mator checkout with its pinned engine installed:

```sh
PYTHONPATH=src python3 scripts/check_triage_compat.py \
  --checkout /path/to/triage-o-mator --pre-index-state missing-index \
  --output /tmp/reposition-compatibility.json
```

Run this from the matching Reposition checkout. The checker creates a disposable
synthetic install, publishes evidence and corpora through triage-o-mator's owning
code, and checks verified retrieval, identity guards, metadata inspection,
`pr-checks` compatibility and unchanged authoritative cache bytes without GitHub
requests. Its default `--pre-index-state missing-package` supports the earlier
standalone bridge trial; the maintained pinned integration expects `missing-index`.

## Historical trials

The [original integration receipt](../../validation/triage-master.json) records a
proposal tested against triage-o-mator commit
[`80cdcdd77d875c4fdcd96f3367f2f8a7d253e63a`](https://github.com/EFrMG/triage-o-mator/tree/80cdcdd77d875c4fdcd96f3367f2f8a7d253e63a).
The [review and cost trial](../../docs/triage-review-trial.md) records that earlier
engine's synthetic retrieval measurements and limitations. These are historical
records and do not validate the current build; rerun compatibility checks when
the engine or owning cache contracts change.
