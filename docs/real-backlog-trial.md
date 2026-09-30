# First real-backlog trial: omacom/omarchy

This is the first run of the immutable-cache workflow against a real backlog
instead of a synthetic fixture. It covers roadmap steps 1 and 2 for one
repository, one machine and one run; it does not qualify the defaults.

## Source and labels

triage-o-mator's bulk `backlog` acquisition froze all 2,849 open PRs of
omacom/omarchy on 2026-09-30: 2,643 complete and 206 with recorded gaps. No
cache is committed here; the [receipt](../validation/omarchy-backlog-trial.json)
records the corpus ID, its progress checkpoint, the inventory snapshot and the
base commit, so a rerun can tell whether it reads the same input.

The labels are independent of Reposition and triage-o-mator. In an
[update to omacom/omarchy#11049](https://github.com/omacom/omarchy/issues/11049#issuecomment-5857142203),
@aholbreich published 28 duplicate groups and 27 pairs of open PRs on
2026-09-27. He found candidates by clustering titles and requiring a shared
primary file, then checked each one by reading the diffs. That candidate step
overlaps with searching summaries and file lists, so the cases may favour
duplicates that lexical retrieval finds easily; the manual review settles
membership, not how the candidates were found. The
[case file](../examples/omarchy-review-cases.json) keeps the 55 groups with two
or more open PRs. Each query is the oldest open member's own title, searched over
`summary`, `files` and `diff`: where a triager asking "is #N a duplicate?"
starts. That query choice is ours, not the labeller's. The two PRs the labeller
named as the same problem solved differently are kept as `hard_negatives` for a
later decision-level evaluation; retrieval returning them is expected.

## Results

| Measure | Value |
| --- | ---: |
| Expected items returned within 12,000 bytes | 130 / 146 |
| Other group members returned (anchor excluded) | 75 / 91 (82.4%) |
| Cases with every member returned | 46 / 55 |
| Median returned items per case | 5 |
| Median warm query | 318 ms |
| Median verified literal scan, same projections | 3,864 ms |
| Indexed source / index size | 103.3 / 192.6 MiB |
| Build time / peak RSS | 26.9 s / 679 MiB |

The anchor's own title always finds the anchor, so the 82.4% figure is the one
that measures finding its duplicates. Every case hit the 1,000-candidate limit:
a title's ordinary terms use any-term matching, so ranking, not filtering, does
the work. The index is 1.86 times the indexed source.

## Limits

Precision is not measured: the labels say which PRs belong together, not how
relevant each other returned item is. A returned item is a lead to read, never
a duplicate decision. Timings are from one run on one machine with OS caches not
cleared and nothing else running. Recreating the cache needs GitHub access and a
triage-o-mator install, and a new acquisition gets a new corpus ID and
checkpoint; compare them with the receipt. Rerun with:

```sh
python benchmarks/cache_retrieval.py --cache /path/to/install/data/omacom/omarchy/cache \
  --corpus CORPUS_ID --cases examples/omarchy-review-cases.json
```
