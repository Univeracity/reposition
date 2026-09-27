# Synthetic review and cost trial

The optional triage-o-mator bridge was checked against master commit
`80cdcdd77d875c4fdcd96f3367f2f8a7d253e63a` using Reposition `0.2.0.dev1`, engine
commit `10bd0641b50d514984dad6dee480f140ab86ee44`. The
[current-master receipt](../validation/triage-master.json) records the exact
patch and compatibility checks. These are synthetic offline trials, not a
qualification of a real Omarchy backlog or a 5 GB cache.

## Similar symptoms, different causes

The [eight-item fixture](../examples/triage_review_cases.py) deliberately includes
four source diagnoses and their proposed changes:

| Source items | Shared symptom | Distinguishing source evidence | Proposed change |
| --- | --- | --- | --- |
| issue 1 / PR 2 | Terminal blank after suspend | GPU reset; session socket still connected | Recreate the renderer |
| issue 3 / PR 4 | Terminal blank after suspend | Renderer healthy; session socket disconnected | Reopen the socket |
| issue 5 / PR 6 | Package checksum mismatch | Remote archive matches pinned digest; local archive stale | Invalidate local cache |
| issue 7 / PR 8 | Package checksum mismatch | A fresh download still differs; upstream archive replaced | Reject changed upstream payload |

PRs 2 and 4 both change `src/resume.py`; PRs 6 and 8 both change
`src/packages.py`. Shared paths and symptoms are useful discovery signals, but
these authored source cases call for different investigations and fixes. A PR's
claim to address an issue remains a source claim, not proof, approval or a
semantic duplicate verdict.

Quoted symptom queries retained all four relevant items in each symptom family
within the 12,000-byte response budget. A quoted path query retained both resume
fixes; a focused comment phrase retained both upstream-replacement sources. Every
returned excerpt was source-verified and selected retrieval was verified again.
These labels were authored with the fixture; they are not independent judgments
or measured duplicate-decision accuracy. Plain path terms use any-term matching;
quote the path when phrase eligibility is desired.

## Storage and complete CLI costs

| Items | Source MiB | Index MiB | Units | Build seconds | Peak engine RSS MiB | CLI query median range, ms | One-fragment retrieval range, ms |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 0.049 | 0.234 | 140 | 0.72 | 22.6 | 95–101 | 95–98 |
| 128 | 1.427 | 3.152 | 2,240 | 6.60 | 29.8 | 94–143 | 116–123 |
| 512 | 47.990 | 30.645 | 20,224 | 24.35 | 52.9 | 98–358 | 193–200 |

Raw bounded reports: [8 items](../validation/triage-costs-small.json),
[128 items](../validation/triage-costs-medium.json),
[512 items](../validation/triage-costs-large.json).

The larger fixtures repeat the same synthetic cause families with different
item identities and longer comments. They are resource probes and have no
ranking-quality labels. The 512-item source is about 48 MiB, far short of 5 GB;
repetition, unusually compressible comments and limited cause diversity prevent
extrapolating these numbers to the real backlog.

The reports include first-reader and warm in-process query timings, unique
verified object bytes, candidate/response omissions, a verified literal scan,
and complete `bin/cache` query/retrieval subprocess timings. Three CLI calls per
query include Python startup and source verification. Peak RSS is from the
separate engine benchmark process, including build/query/scan; it does not
measure the parent generator, CLI subprocess memory or TUI memory. The final
sizes were measured sequentially without concurrent agent test/build jobs;
the machine was shared and OS caches were not cleared. Build cost is one sample,
and retrieval timing is one selected fragment per case.

Tradeoffs remain mixed. On the eight-item fixture, literal scanning is cheaper
than ranked query and the index is larger than the source. At 512 items, the
warm comment-phrase query took about 190 ms versus 518 ms for the projection
scan; an absent marker took about 7 ms versus 679 ms. Broad summary searches were
roughly tied, and the quoted file search was slower with FTS5 (63 versus 46 ms).
The scan is an unpaginated verified projection baseline, not the existing
literal CLI's pagination contract; these are not equivalent latency promises.
The startup-inclusive bridge costs in the table are the better guide for an
external CLI caller.

Indexes live beside the authoritative cache and do not consume its 5 GB
acquisition budget. Default ceilings are 512 MiB and one million units per view.
Multiple views accumulate separately and replacement may need old plus new
space. Full object verification still requires a 64 MiB per-object and 128 MiB
per-response default allowance. A bounded build failure preserves the old view.
The source cache, progress and approvals are not rewritten by retrieval.

## Reproduce and qualify the next trial

Install the supported Reposition checkout, then use a trusted triage-o-mator
checkout with the supplied patch applied:

```sh
python scripts/check_triage_compat.py --checkout /path/to/triage-o-mator
python scripts/measure_triage_review.py --checkout /path/to/triage-o-mator
python scripts/measure_triage_review.py --checkout /path/to/triage-o-mator \
  --groups 16 --repeat 16 --output medium.json
python scripts/measure_triage_review.py --checkout /path/to/triage-o-mator \
  --groups 64 --repeat 256 --output large.json
```

Each cost command creates and removes its synthetic cache/index automatically;
no snapshot IDs need to be copied. It makes no acquisition requests and refuses
unexpected GitHub calls. The compatibility command uses actual upstream
EvidenceCache/corpus publication and checks the before/after literal path without
Reposition, missing-package behavior, identity/scope guards, bounded verified
query/retrieval and byte-identical authoritative artifacts.

For real-backlog review, freeze an explicit snapshot or corpus checkpoint and
record coverage first. Measure build/query/retrieval latency, peak memory, index
size, disk headroom and output/verification omissions under declared limits.
Have reviewers label true duplicates, competing fixes and similar symptoms with
different causes independently of the retrieval ranking. Compare reviewer
decisions and time, citation support, and actual consuming-model token use
against the existing literal workflow. Keep unknown coverage and disagreements
visible. Those observations should guide TUI integration and later suggestions;
search itself grants no approval or permission to act.
