# Next useful steps

The initial foundation is an offline CLI/Python library: isolated FTS5 snapshots,
explicit import adapters, bounded cited evidence, literal references and optional
cached TF-IDF comparison.

The immutable triage-o-mator adapter, deterministic component projections,
atomic FTS5 views, verified progressive readers and optional `bin/cache` bridge
are now implemented. Synthetic fixtures and the upstream's actual publisher
exercise their contracts; they do not qualify production-scale performance.

1. Trial the prepared integration against representative real caches and the
   existing triage TUI. Measure disk overhead, peak memory, selective/dense query
   latency, omissions and independent review effort before widening the defaults.
2. Obtain independently reviewed relevance and hard-negative cases. Measure
   review decisions and effort as well as retrieval quality.
3. Qualify optional lexical/semantic fusion and anchored evidence packing against
   the default with identical source pools and final consumer budgets.
4. Add source acquisition or incremental refresh only when the calling workflows
   need them, preserving coverage and explicit snapshot boundaries.

Optional retrieval backends and review-workflow integrations should be evaluated
against the default on these measured workflows. A broader repository pipeline
product should grow from demonstrated needs.
