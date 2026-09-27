"""Offline command-line interface; stdout is text or structured JSON."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .importers import load_snapshot
from .index import Index
from .render import render


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="reposition", description="Put repository work in context.")
    root.add_argument("--version", action="version", version=f"Reposition {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
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
    for command in commands.choices.values():
        command.add_argument(
            "--db",
            type=Path,
            default=Path(".reposition/index.sqlite"),
            help="database path (one repository per database)",
        )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
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
