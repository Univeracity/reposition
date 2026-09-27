"""Portable retrieval evaluation using caller-supplied, frozen relevance labels."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from time import perf_counter

from reposition import Index, load_snapshot, render


def metrics(items, relevance, negatives, k):
    positive = {str(item): grade for item, grade in relevance.items() if grade > 0}
    returned = items[:k]
    ranks = [rank for rank, item in enumerate(returned, 1) if item in positive]
    dcg = sum(
        (2 ** positive.get(item, 0) - 1) / math.log2(rank + 1)
        for rank, item in enumerate(returned, 1)
    )
    ideal = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(sorted(positive.values(), reverse=True)[:k], 1)
    )
    return {
        "known_positive_recall": len(set(returned) & positive.keys()) / len(positive)
        if positive
        else None,
        "mrr": 1 / min(ranks) if ranks else 0.0,
        "ndcg": dcg / ideal if ideal else None,
        "hard_negative_exposures": sorted(set(returned) & {str(n) for n in negatives}),
        "no_match": not returned if not positive else None,
    }


def evidence_metrics(evidence, retrieved, relevance, negatives):
    positive = {str(item) for item, grade in relevance.items() if grade > 0}
    cited = {e["item"] for e in evidence.excerpts}
    retrieved_positive = set(retrieved) & positive
    return {
        "known_positive_citation_recall": len(cited & positive) / len(positive)
        if positive
        else None,
        "retrieved_positive_citation_retention": len(cited & retrieved_positive)
        / len(retrieved_positive)
        if retrieved_positive
        else None,
        "hard_negative_citations": sorted(cited & {str(item) for item in negatives}),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument(
        "cases",
        help="JSON {label_authority, cases:[{id,query,relevance,hard_negatives,component}]}",
    )
    parser.add_argument("--format", choices=("records", "github", "components"), default="records")
    parser.add_argument("--repo")
    parser.add_argument("--manifest")
    parser.add_argument("--methods", nargs="+", choices=("fts5", "tfidf"), default=["fts5"])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--candidates", type=int, default=100)
    budgets = parser.add_mutually_exclusive_group()
    budgets.add_argument(
        "--chars", type=int, help="complete evidence character budget (default 8000)"
    )
    budgets.add_argument(
        "--tokens", type=int, help="complete evidence token budget; needs [tokens]"
    )
    parser.add_argument("--encoding", default="o200k_base")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("repeats must be positive")
    budget = (
        args.tokens if args.tokens is not None else (args.chars if args.chars is not None else 8000)
    )
    if budget <= 0:
        parser.error("evidence budget must be positive")
    unit = "tokens" if args.tokens is not None else "characters"
    with open(args.cases, encoding="utf-8") as stream:
        labels = json.load(stream)
    snapshot = load_snapshot(
        args.snapshot, format=args.format, repository=args.repo, manifest_path=args.manifest
    )
    outputs = {}
    with Index() as index:
        started = perf_counter()
        index.import_snapshot(snapshot)
        import_ms = (perf_counter() - started) * 1000
        for method in dict.fromkeys(args.methods):
            rows = []
            warm = []
            first_query_ms = None
            for case in labels["cases"]:
                options = {
                    "method": method,
                    "component": case.get("component"),
                    "limit": args.limit,
                    "candidate_limit": args.candidates,
                }
                started = perf_counter()
                result = index.search(case["query"], **options)
                if first_query_ms is None:
                    first_query_ms = (perf_counter() - started) * 1000
                ids = [h.record.id for h in result.hits]
                samples = []
                for _ in range(args.repeats):
                    started = perf_counter()
                    again = index.search(case["query"], **options)
                    samples.append((perf_counter() - started) * 1000)
                    if ids != [h.record.id for h in again.hits]:
                        raise RuntimeError("warm ranking changed")
                warm.extend(samples)
                items = [h.record.item for h in result.hits]
                started = perf_counter()
                evidence = render(result, budget=budget, unit=unit, encoding=args.encoding)
                render_ms = (perf_counter() - started) * 1000
                rows.append(
                    {
                        "id": case["id"],
                        "record_ids": ids,
                        "items": items,
                        "metrics": metrics(
                            items, case["relevance"], case.get("hard_negatives", []), args.limit
                        ),
                        "warm_ms": samples,
                        "diagnostics": result.diagnostics,
                        "evidence": {
                            **evidence.to_dict(),
                            "metrics": evidence_metrics(
                                evidence, items, case["relevance"], case.get("hard_negatives", [])
                            ),
                            "render_ms": render_ms,
                        },
                    }
                )
            positives = [
                r["metrics"] for r in rows if r["metrics"]["known_positive_recall"] is not None
            ]
            citations = [
                r["evidence"]["metrics"]["known_positive_citation_recall"]
                for r in rows
                if r["evidence"]["metrics"]["known_positive_citation_recall"] is not None
            ]
            outputs[method] = {
                "first_query_including_preparation_ms": first_query_ms,
                "warm_median_ms": statistics.median(warm) if warm else None,
                "mean_known_positive_recall": statistics.fmean(
                    m["known_positive_recall"] for m in positives
                )
                if positives
                else None,
                "mean_mrr": statistics.fmean(m["mrr"] for m in positives) if positives else None,
                "mean_known_positive_citation_recall": statistics.fmean(citations)
                if citations
                else None,
                "cases": rows,
            }
    print(
        json.dumps(
            {
                "schema": "reposition.evaluation.v1",
                "repository": snapshot.repository,
                "snapshot_digest": snapshot.digest,
                "label_authority": labels.get("label_authority", "unspecified"),
                "settings": {
                    "candidate_limit": args.candidates,
                    "item_limit": args.limit,
                    "warm_repeats": args.repeats,
                    "tokenizer": "unicode61",
                    "title_weight": 3,
                    "evidence_budget": budget,
                    "evidence_unit": unit,
                    "evidence_encoding": args.encoding if unit == "tokens" else None,
                },
                "limits": "known labels may be incomplete; unjudged hits are not false positives; citation retention does not establish excerpt sufficiency; evidence budgets exclude this JSON wrapper; no reviewer time or decision-quality measurement",
                "import_ms": import_ms,
                "methods": outputs,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
