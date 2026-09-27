"""Measure immutable-cache FTS5 against a verified literal scan over the same projections."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
from pathlib import Path
from time import perf_counter

from reposition import CacheIndex, ProjectionPolicy, TriageCache
from reposition.cache_index import encode_response
from reposition.projections import project


def scan(source, selection, query, components, policy):
    matches = []
    object_bytes = 0
    for member in selection.members:
        row = member["record"]
        if row is None:
            continue
        for component in components:
            descriptor = row["components"].get(component)
            if descriptor is None or descriptor["object"] is None:
                continue
            payload = source.object(component, descriptor, row["identity"]["kind"])
            object_bytes += descriptor["object"]["bytes"]
            if any(query in text for _, text in project(component, payload, policy)):
                matches.append(f"{row['identity']['kind']}:{row['identity']['number']}")
                break
    return matches, object_bytes


def peak_rss():
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if platform.system() == "Darwin" else value * 1024
    except ImportError:
        return None


def run(source, database, scope, cases, repeats=3, max_bytes=12000):
    policy = ProjectionPolicy()
    start = perf_counter()
    build = CacheIndex.build(source, database, **scope, policy=policy, replace=database.exists())
    build_seconds = perf_counter() - start
    selection = source.select(**scope)
    rows = []
    with CacheIndex(database) as index:
        for case in cases:
            options = {
                "components": tuple(
                    case.get("components", ["summary", "comments", "files", "diff"])
                ),
                "max_bytes": max_bytes,
                "max_snippets_per_item": case.get("max_snippets_per_item", 2),
            }
            query_source = TriageCache(
                source.root,
                max_object_bytes=source.max_object_bytes,
                max_metadata_bytes=source.max_metadata_bytes,
            )
            start = perf_counter()
            result = index.query(query_source, case["query"], **scope, **options)
            first_ms = (perf_counter() - start) * 1000
            warm = []
            ids = [(i["identity"]["kind"], i["identity"]["number"]) for i in result["items"]]
            for _ in range(repeats):
                start = perf_counter()
                again = index.query(query_source, case["query"], **scope, **options)
                warm.append((perf_counter() - start) * 1000)
                if ids != [
                    (i["identity"]["kind"], i["identity"]["number"]) for i in again["items"]
                ]:
                    raise RuntimeError("ranking changed without source/index changes")
            start = perf_counter()
            literal, literal_bytes = scan(
                source,
                selection,
                case.get("literal_query", case["query"]),
                options["components"],
                policy,
            )
            literal_ms = (perf_counter() - start) * 1000
            returned = [f"{kind}:{number}" for kind, number in ids]
            expected = set(case.get("expected_items", []))
            rows.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "literal_query": case.get("literal_query", case["query"]),
                    "components": options["components"],
                    "first_query_in_process_ms": first_ms,
                    "warm_ms": warm,
                    "warm_median_ms": statistics.median(warm),
                    "literal_scan_ms": literal_ms,
                    "literal_source_bytes_read": literal_bytes,
                    "literal_matched_items": len(literal),
                    "returned_items": returned,
                    "expected_items": sorted(expected),
                    "known_positive_recall": len(expected.intersection(returned)) / len(expected)
                    if expected
                    else None,
                    "output_bytes": len(encode_response(result).encode()),
                    "diagnostics": result["diagnostics"],
                }
            )
    cache_bytes = sum(
        p.stat().st_size for p in source.root.rglob("*") if p.is_file() and not p.is_symlink()
    )
    return {
        "schema": "reposition.cache-measurement.v1",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "process_id": os.getpid(),
        },
        "scope": scope,
        "source_cache_bytes": cache_bytes,
        "indexed_source_object_bytes": build["source_bytes"],
        "index_bytes": build["index_bytes"],
        "index_to_indexed_source_ratio": build["index_bytes"] / build["source_bytes"]
        if build["source_bytes"]
        else None,
        "units": build["units"],
        "objects": build["objects"],
        "build_seconds": build_seconds,
        "peak_process_rss_bytes": peak_rss(),
        "response_max_bytes": max_bytes,
        "query_reader_policy": "Fresh source reader per case for first query; reused reader for warm queries. Python startup and OS cache clearing excluded.",
        "cases": rows,
        "limits": "Literal scan verifies each examined object and uses the same named-field/chunk projections; this is not the existing CLI pagination benchmark. Timings include verification. OS caches are not cleared. Peak RSS includes selection/build/query/scan. Synthetic labels measure known retrieval targets, not duplicate decisions, reviewer time, or semantic quality.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot")
    group.add_argument("--corpus")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-bytes", type=int, default=12000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    source = TriageCache(args.cache)
    scope = {"snapshot": args.snapshot, "corpus": args.corpus}
    cases = json.loads(args.cases.read_text())
    result = run(
        source,
        args.db or source.index_path(**scope),
        scope,
        cases["cases"],
        args.repeats,
        args.max_bytes,
    )
    result["label_authority"] = cases.get("label_authority", "unspecified")
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
