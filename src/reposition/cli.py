"""Offline command-line interface; stdout is text or structured JSON."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .cache_index import CacheIndex, encode_response
from .cache_source import TriageCache
from .importers import load_snapshot
from .index import Index
from .projections import ProjectionPolicy
from .render import render


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="reposition", description="Put repository work in context.")
    root.add_argument("--version", action="version", version=f"Reposition {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="try synthetic search without setup or saved files")
    demo.add_argument("query", nargs="?", help="optional search text for the demo")
    demo.add_argument(
        "--cache", action="store_true", help="demo immutable-cache indexing, search and retrieval"
    )
    index = commands.add_parser("index", help="index an offline JSON snapshot")
    index.add_argument("input", type=Path)
    index.add_argument("--format", choices=("records", "github", "components"), default="records")
    index.add_argument("--repo", help="owner/name; required for github or components input")
    index.add_argument("--manifest", type=Path, help="coverage manifest for components input")
    index.add_argument(
        "--replace", action="store_true", help="atomically replace this repository's snapshot"
    )
    index.add_argument(
        "--allow-empty", action="store_true", help="permit an explicitly empty snapshot"
    )
    search = commands.add_parser("search", help="find distinct items and render cited evidence")
    search.add_argument("query")
    search.add_argument("--component", help="restrict candidate components, e.g. comments or files")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--candidates", type=int, default=100)
    search.add_argument("--method", choices=("fts5", "tfidf"), default="fts5")
    budgets = search.add_mutually_exclusive_group()
    budgets.add_argument(
        "--chars", type=int, help="complete formatted text character budget (default 8000)"
    )
    budgets.add_argument(
        "--tokens", type=int, help="complete formatted text token budget; needs [tokens] extra"
    )
    search.add_argument(
        "--encoding", default="o200k_base", help="consuming tokenizer encoding for --tokens"
    )
    search.add_argument(
        "--json",
        action="store_true",
        help="structured rankings and evidence; JSON wrapper is outside text budget",
    )
    relations = commands.add_parser(
        "related", help="inspect literal incoming/outgoing item references"
    )
    relations.add_argument("item", help="item number with or without #")
    relations.add_argument("--limit", type=int, default=20)
    commands.add_parser("info", help="show repository, snapshot and coverage metadata")
    commands.add_parser("export", help="write the normalized snapshot to stdout")
    cache_index = commands.add_parser(
        "cache-index", help="build a verified immutable-cache FTS5 view"
    )
    cache_query = commands.add_parser(
        "cache-query", help="query ranked, source-verified cache fragments"
    )
    cache_retrieve = commands.add_parser(
        "cache-retrieve", help="resolve selected cache units with source verification"
    )
    cache_info = commands.add_parser("cache-info", help="inspect a derived cache index manifest")
    for command in (cache_index, cache_query, cache_retrieve, cache_info):
        command.add_argument(
            "--cache", type=Path, required=True, help="triage-o-mator evidence cache directory"
        )
        command.add_argument("--max-object-bytes", type=int, default=64 * 1024 * 1024)
        scope = command.add_mutually_exclusive_group(required=command != cache_info)
        scope.add_argument("--snapshot", help="exact immutable snapshot ID")
        scope.add_argument("--corpus", help="frozen corpus ID; progress is checkpoint-bound")
    cache_index.add_argument("--component", action="append")
    cache_index.add_argument("--replace", action="store_true")
    cache_index.add_argument(
        "--rebuild",
        action="store_true",
        help="explicit recovery of corrupt derived data; never downgrade future versions",
    )
    cache_index.add_argument("--chunk-bytes", type=int, default=4096)
    cache_index.add_argument("--overlap-bytes", type=int, default=128)
    cache_index.add_argument("--chunk-lines", type=int, default=200)
    cache_index.add_argument("--max-index-bytes", type=int, default=512 * 1024 * 1024)
    cache_index.add_argument("--max-units", type=int, default=1_000_000)
    cache_query.add_argument("--query", required=True)
    cache_query.add_argument("--component", action="append")
    cache_query.add_argument("--field", action="append")
    cache_query.add_argument("--kind", choices=("issue", "pr"))
    cache_query.add_argument(
        "--state",
        choices=("open", "closed", "merged", "unknown", "unavailable", "deleted", "transferred"),
    )
    cache_query.add_argument("--label")
    cache_query.add_argument("--author")
    cache_query.add_argument("--after")
    cache_query.add_argument("--before")
    cache_query.add_argument("--min-score", type=float)
    cache_query.add_argument("--limit", type=int, default=10)
    cache_query.add_argument("--candidates", type=int, default=1000)
    cache_query.add_argument("--max-snippets-per-item", type=int, default=2)
    cache_query.add_argument(
        "--fragments", action="store_true", help="page fragments instead of diverse items"
    )
    cache_query.add_argument("--cursor")
    cache_retrieve.add_argument(
        "--unit", action="append", required=True, help="unit_id from a query result"
    )
    cache_retrieve.add_argument(
        "--checkpoint", required=True, help="index checkpoint from discovery"
    )
    cache_retrieve.add_argument(
        "--byte-offset",
        type=int,
        default=None,
        help="UTF-8 byte offset within the original comment/hunk boundary; default starts at the selected unit",
    )
    for command in (cache_query, cache_retrieve):
        command.add_argument(
            "--max-bytes",
            type=int,
            default=12000,
            help="complete UTF-8 JSON response ceiling, including newline",
        )
        command.add_argument(
            "--fragment-bytes", type=int, default=512 if command == cache_query else 4096
        )
        command.add_argument(
            "--max-age",
            type=int,
            default=86400,
            help="age diagnostic policy in seconds; no live freshness claim",
        )
        command.add_argument(
            "--max-verify-bytes",
            type=int,
            default=128 * 1024 * 1024,
            help="ceiling on unique source-object bytes verified per response",
        )
    for name, command in commands.choices.items():
        if name == "demo":
            continue
        command.add_argument(
            "--db",
            type=Path,
            default=None if name.startswith("cache-") else Path(".reposition/index.sqlite"),
            help="database path; cache commands default to a scope-specific sidecar beside CACHE",
        )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import run_demo

            sys.stdout.write(run_demo(args.query, immutable_cache=args.cache))
            return 0
        if args.command.startswith("cache-"):
            source = TriageCache(args.cache, max_object_bytes=args.max_object_bytes)
            path = args.db or source.index_path(snapshot=args.snapshot, corpus=args.corpus)
            if args.command == "cache-index":
                output = CacheIndex.build(
                    source,
                    path,
                    snapshot=args.snapshot,
                    corpus=args.corpus,
                    components=args.component,
                    policy=ProjectionPolicy(args.chunk_bytes, args.overlap_bytes, args.chunk_lines),
                    max_index_bytes=args.max_index_bytes,
                    max_units=args.max_units,
                    replace=args.replace,
                    recover=args.rebuild,
                )
            else:
                with CacheIndex(path) as index:
                    if args.command == "cache-query":
                        output = index.query(
                            source,
                            args.query,
                            snapshot=args.snapshot,
                            corpus=args.corpus,
                            components=args.component,
                            fields=args.field,
                            kind=args.kind,
                            state=args.state,
                            label=args.label,
                            author=args.author,
                            after=args.after,
                            before=args.before,
                            min_score=args.min_score,
                            limit=args.limit,
                            candidates=args.candidates,
                            max_snippets_per_item=args.max_snippets_per_item,
                            fragment_mode=args.fragments,
                            cursor=args.cursor,
                            max_bytes=args.max_bytes,
                            fragment_bytes=args.fragment_bytes,
                            max_age=args.max_age,
                            max_verify_bytes=args.max_verify_bytes,
                        )
                    elif args.command == "cache-retrieve":
                        output = index.retrieve(
                            source,
                            tuple(args.unit),
                            checkpoint=args.checkpoint,
                            snapshot=args.snapshot,
                            corpus=args.corpus,
                            max_bytes=args.max_bytes,
                            fragment_bytes=args.fragment_bytes,
                            max_age=args.max_age,
                            max_verify_bytes=args.max_verify_bytes,
                            byte_offset=args.byte_offset,
                        )
                    else:
                        output = index.info()
            sys.stdout.write(encode_response(output))
            return 0
        if args.command == "index":
            snapshot = load_snapshot(
                args.input, format=args.format, repository=args.repo, manifest_path=args.manifest
            )
            if not snapshot.records and not args.allow_empty:
                raise ValueError(
                    "empty snapshot rejected; use --allow-empty to clear intentionally"
                )
            args.db.parent.mkdir(parents=True, exist_ok=True)
            with Index(args.db) as index:
                output = index.import_snapshot(
                    snapshot, replace=args.replace, allow_empty=args.allow_empty
                )
        else:
            with Index(args.db, readonly=True) as index:
                if args.command == "search":
                    result = index.search(
                        args.query,
                        component=args.component,
                        limit=args.limit,
                        candidate_limit=args.candidates,
                        method=args.method,
                    )
                    evidence = render(
                        result,
                        budget=args.tokens
                        if args.tokens is not None
                        else (args.chars if args.chars is not None else 8000),
                        unit="tokens" if args.tokens is not None else "characters",
                        encoding=args.encoding,
                    )
                    if not args.json:
                        sys.stdout.write(evidence.text)
                        return 0
                    output = {"search": result.to_dict(), "evidence": evidence.to_dict()}
                elif args.command == "related":
                    output = index.related(args.item.lstrip("#"), limit=args.limit)
                elif args.command == "export":
                    output = index.export().to_dict()
                else:
                    output = index.info()
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        print(f"reposition: {exc}", file=sys.stderr)
        return 2
