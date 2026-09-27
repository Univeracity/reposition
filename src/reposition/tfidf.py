"""Opt-in cached TF-IDF comparator using exactly the SQLite FTS5 tokenizer."""

from __future__ import annotations

import re
import sqlite3

from .query import expression


class TfidfIndex:
    def __init__(self, connection: sqlite3.Connection, snapshot_digest: str):
        try:
            import numpy as np
            from scipy import sparse
            from sklearn.feature_extraction.text import TfidfTransformer
            from sklearn.preprocessing import normalize
        except ImportError as exc:
            raise ValueError("TF-IDF requires: pip install 'reposition[tfidf]'") from exc
        self.np, self.sparse = np, sparse
        self.normalize = normalize
        self.snapshot_digest = snapshot_digest
        self.records = [
            (row[0], row[1])
            for row in connection.execute("SELECT id,component FROM evidence ORDER BY rowid")
        ]
        # TEMP vocabulary reads the durable index even on a read-only connection.
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.reposition_vocab USING fts5vocab(main,evidence,'instance')"
        )
        rows = connection.execute(
            "SELECT term,doc,col,count(*) FROM reposition_vocab GROUP BY term,doc,col"
        ).fetchall()
        vocabulary = sorted({row[0] for row in rows})
        self.features = {term: i for i, term in enumerate(vocabulary)}
        self.transformer = None
        self.matrix = None
        if vocabulary:
            shape = (len(self.records), len(vocabulary))
            weighted = sparse.csr_matrix(
                (
                    [float(n) * (3 if col == "title" else 1) for _, _, col, n in rows],
                    (
                        [doc - 1 for _, doc, _, _ in rows],
                        [self.features[term] for term, _, _, _ in rows],
                    ),
                ),
                shape=shape,
            )
            self.transformer = TfidfTransformer(norm=None, smooth_idf=True, sublinear_tf=False)
            self.matrix = normalize(self.transformer.fit_transform(weighted), norm="l2")
        self.fit_count = 1 if vocabulary else 0
        self.tokenizer = sqlite3.connect(":memory:")
        self.tokenizer.execute("CREATE VIRTUAL TABLE tokens USING fts5(text,tokenize='unicode61')")
        self.tokenizer.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(tokens,'instance')")

    def search(self, query: str, component: str | None, limit: int) -> list[tuple[str, float]]:
        if self.matrix is None:
            return []
        literal_text = " ".join(re.findall(r'"([^\"]+)"', expression(query)))
        self.tokenizer.execute("DELETE FROM tokens")
        self.tokenizer.execute("INSERT INTO tokens VALUES(?)", (literal_text,))
        counts = [
            (self.features[term], n)
            for term, n in self.tokenizer.execute("SELECT term,count(*) FROM vocab GROUP BY term")
            if term in self.features
        ]
        if not counts:
            return []
        vector = self.sparse.csr_matrix(
            ([n for _, n in counts], ([0] * len(counts), [i for i, _ in counts])),
            shape=(1, len(self.features)),
            dtype=self.np.float64,
        )
        vector = self.normalize(self.transformer.transform(vector), norm="l2")
        scores = (self.matrix @ vector.T).toarray().reshape(-1)
        ranked = [
            (id, float(scores[i]))
            for i, (id, c) in enumerate(self.records)
            if (component is None or c == component) and scores[i] > 0
        ]
        return sorted(ranked, key=lambda row: (-row[1], row[0]))[:limit]

    def close(self) -> None:
        self.tokenizer.close()
