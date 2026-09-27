"""Persistent, repository-isolated SQLite FTS5 index."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import Hit, Record, SearchResult, Snapshot, canonical
from .query import expression

APPLICATION_ID = 0x52504F53  # RPOS
DATABASE_VERSION = 1


class Index:
    def __init__(self, path: str | Path = ":memory:", *, readonly: bool = False):
        self.path = str(path)
        self.readonly = readonly
        if readonly:
            if self.path == ":memory:" or not Path(path).is_file():
                raise ValueError("database does not exist; index a snapshot first")
            self.connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        else:
            self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._tfidf = None
        try:
            application_id = self.connection.execute("PRAGMA application_id").fetchone()[0]
            tables = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not tables and not readonly:
                self._initialize()
            elif application_id != APPLICATION_ID:
                raise ValueError("this is not a Reposition database")
            if self.connection.execute("PRAGMA user_version").fetchone()[0] != DATABASE_VERSION:
                raise ValueError("unsupported Reposition database version")
        except Exception:
            self.connection.close()
            raise

    def _initialize(self) -> None:
        self.connection.executescript(f"""
            PRAGMA application_id = {APPLICATION_ID};
            PRAGMA user_version = {DATABASE_VERSION};
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE records(id TEXT PRIMARY KEY, item TEXT NOT NULL,
                component TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX record_items ON records(item, component);
            CREATE VIRTUAL TABLE evidence USING fts5(
                id UNINDEXED, component UNINDEXED, title, text, tokenize='unicode61');
        """)

    @contextmanager
    def _read(self):
        # Metadata, rankings and hydrated evidence come from the same read snapshot.
        self.connection.execute("BEGIN")
        try:
            yield
        finally:
            self.connection.rollback()

    def _info(self) -> dict[str, Any]:
        return {
            row["key"]: json.loads(row["value"])
            for row in self.connection.execute("SELECT * FROM metadata")
        }

    def info(self) -> dict[str, Any]:
        with self._read():
            return self._info()

    def import_snapshot(
        self, snapshot: Snapshot, *, replace: bool = False, allow_empty: bool = False
    ) -> dict[str, Any]:
        if self.readonly:
            raise ValueError("database is read-only")
        if not snapshot.records and not allow_empty:
            raise ValueError(
                "empty snapshot rejected; use allow_empty to intentionally clear evidence"
            )
        # Validate and materialize everything before starting a replacement transaction.
        snapshot = Snapshot(
            snapshot.repository,
            tuple(Record.from_dict(r.to_dict()) for r in snapshot.records),
            json.loads(canonical(snapshot.coverage)),
        )
        rows = [(r.id, r.item, r.component, canonical(r.to_dict())) for r in snapshot.records]
        fts_rows = [(r.id, r.component, r.title, r.text) for r in snapshot.records]
        info = {
            "repository": snapshot.repository,
            "snapshot_digest": snapshot.digest,
            "record_count": len(rows),
            "item_count": len({r.item for r in snapshot.records}),
            "coverage": snapshot.coverage,
            "tokenizer": "unicode61",
            "title_weight": 3,
            "database_version": DATABASE_VERSION,
        }
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self._info()
            if existing and existing["repository"] != snapshot.repository:
                raise ValueError(
                    "database belongs to a different repository; use a separate database"
                )
            if existing and not replace:
                raise ValueError("database already has a snapshot; pass replace=True / --replace")
            self.connection.execute("DELETE FROM evidence")
            self.connection.execute("DELETE FROM records")
            self.connection.execute("DELETE FROM metadata")
            self.connection.executemany("INSERT INTO records VALUES(?,?,?,?)", rows)
            self.connection.executemany("INSERT INTO evidence VALUES(?,?,?,?)", fts_rows)
            self.connection.executemany(
                "INSERT INTO metadata VALUES(?,?)", [(k, canonical(v)) for k, v in info.items()]
            )
        if self._tfidf is not None:
            self._tfidf.close()
        self._tfidf = None
        return info

    def _record(self, id: str) -> Record:
        row = self.connection.execute("SELECT data FROM records WHERE id=?", (id,)).fetchone()
        if row is None:
            raise ValueError(f"record missing from index: {id}")
        return Record.from_dict(json.loads(row["data"]))

    def _anchor(self, query: str, component: str | None) -> str | None:
        import re

        match = re.match(r"^#(\d+)\b", query)
        if not match or component not in (None, "summary"):
            return None
        row = self.connection.execute(
            "SELECT id FROM records WHERE item=? AND component='summary' ORDER BY id LIMIT 1",
            (match[1],),
        ).fetchone()
        return row["id"] if row else None

    def search(
        self,
        query: str,
        *,
        component: str | None = None,
        limit: int = 10,
        candidate_limit: int = 100,
        method: str = "fts5",
    ) -> SearchResult:
        if (
            type(limit) is not int
            or type(candidate_limit) is not int
            or not 1 <= limit <= candidate_limit <= 10000
        ):
            raise ValueError("limits must satisfy 1 <= limit <= candidate_limit <= 10000")
        if component is not None and (not isinstance(component, str) or not component):
            raise ValueError("component must be a nonempty string")
        match_expression = expression(query)
        with self._read():
            info = self._info()
            if not info:
                raise ValueError("index has no snapshot")
            if method == "fts5":
                params = [match_expression]
                clause = ""
                if component:
                    clause = " AND component=?"
                    params.append(component)
                params.append(candidate_limit + 1)
                rows = (
                    self.connection.execute(
                        "SELECT id,bm25(evidence,0,0,3,1) AS score FROM evidence WHERE evidence MATCH ?"
                        + clause
                        + " ORDER BY score,id LIMIT ?",
                        params,
                    ).fetchall()
                    if match_expression
                    else []
                )
                candidates = [(row["id"], -row["score"]) for row in rows]
            elif method == "tfidf":
                from .tfidf import TfidfIndex

                if self._tfidf is None or self._tfidf.snapshot_digest != info["snapshot_digest"]:
                    if self._tfidf is not None:
                        self._tfidf.close()
                    self._tfidf = TfidfIndex(self.connection, info["snapshot_digest"])
                candidates = self._tfidf.search(query, component, candidate_limit + 1)
            else:
                raise ValueError(f"unsupported method: {method}")
            capped = len(candidates) > candidate_limit
            candidates = candidates[:candidate_limit]
            hits = []
            seen = set()
            anchor = self._anchor(query, component)
            entries = [(anchor, None, "explicit-anchor")] if anchor else []
            entries += [
                (id, score, "lexical" if method == "fts5" else "tfidf") for id, score in candidates
            ]
            for id, score, reason in entries:
                record = self._record(id)
                if record.item not in seen:
                    seen.add(record.item)
                    hits.append(Hit(record, score, reason))
            return SearchResult(
                info["repository"],
                info["snapshot_digest"],
                query,
                method,
                tuple(hits[:limit]),
                {
                    "expression": match_expression,
                    "candidate_limit": candidate_limit,
                    "candidate_count": len(candidates),
                    "candidates_truncated": capped,
                    "item_limit": limit,
                    "items_omitted": max(0, len(hits) - limit),
                    "component": component,
                    "title_weight": 3,
                    "tokenizer": "unicode61",
                    "score_is_probability": False,
                },
                info["coverage"],
            )

    def export(self) -> Snapshot:
        with self._read():
            info = self._info()
            if not info:
                raise ValueError("index has no snapshot")
            records = tuple(
                Record.from_dict(json.loads(r[0]))
                for r in self.connection.execute("SELECT data FROM records ORDER BY id")
            )
            snapshot = Snapshot(info["repository"], records, info["coverage"])
            if snapshot.digest != info["snapshot_digest"]:
                raise ValueError("snapshot digest does not match stored records")
            return snapshot

    def related(self, item: str, *, limit: int = 20) -> dict[str, Any]:
        from .relations import related

        return related(self.export(), str(item), limit=limit)

    def close(self) -> None:
        if self._tfidf is not None:
            self._tfidf.close()
            self._tfidf = None
        self.connection.close()

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
