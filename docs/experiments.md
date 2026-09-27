# Why FTS5 is the default

Reposition grew from local experiments over saved public `omacom/omarchy` and
`omacom/omarchy-pkgs` material. Repositories were kept separate: 297/255 items,
434/592 component records, and 80/85 queries. The 165 queries included 121 earlier
regressions, 37 indirect paraphrases and seven supplied-anchor relationship cases.
Labels were analyst-authored, provisional and nonexhaustive. Reviewer time and
duplicate-decision accuracy were not measured.

Twenty earlier configurations explored FTS5, real CPU semantic models, lexical
fusion, literal reference graphs, rerankers, candidate representation and evidence
packing. FTS5 offered a strong inexpensive default. Lexical-heavy semantic fusion
helped indirect queries but lost some package regression recall. Broad graph
expansion and expensive reranking had mixed outcomes. Anchored navigation is
useful separately from general search. This is not a qualification of Microsoft
GraphRAG or a claim that semantic retrieval cannot improve the workflow.

A later five-arm comparison used matched title/text fields, SQLite `unicode61`,
candidate caps, filters and renderers. Cached 3:1 TF-IDF raised main-repository
indirect recall from 92.6% to 100%, but lowered its MRR from .706 to .653 and
weakened citation retention. Package indirect recall stayed 94.7%, while MRR fell
from .759 to .728. Regression recall fell in both repositories. These findings
support retaining FTS5 and offering TF-IDF as a controlled comparator.

Reposition's extraction check replays the saved FTS5 and TF-IDF rankings against
the same components, then independently checks newly rendered excerpt spans,
hashes and complete-output budgets. Its renderer has a general repository header
and richer diagnostics, so extraction does not claim byte-identical old output.
See [the extraction receipt](../validation/extraction.json) for measured parity.

The large source caches, models and full old receipts remain external artifacts;
they are not bundled into the tool. `scripts/verify_extraction.py --help` describes
the external-input replay. `benchmarks/evaluate.py` runs the portable comparison
on a caller's native records or GitHub cache and supplied relevance labels.
The shipped example cases are synthetic demonstrations, not benchmark evidence.
The portable benchmark now also renders each result at a fixed character or
token budget and reports known-positive citation recall, retention of retrieved
positives, and hard-negative citations. Its retained excerpts can be reviewed for
evidence sufficiency; those citation metrics do not measure sufficiency themselves.

The most valuable next evaluation is independently reviewed examples from real
maintainers, including true duplicates, competing fixes, similar symptoms with
different causes, and absent answers. Freeze labels and settings before comparing
retrieval misses, incorrect duplicate calls, evidence sufficiency, time and tokens.
