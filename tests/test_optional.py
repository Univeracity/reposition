import importlib.util
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from reposition import Index, Record, Snapshot, render


def snapshot(text="archive checksum integrity", title="Archive"):
    return Snapshot(
        "test/repo",
        (
            Record("a", "1", "summary", title, text, "https://example.com/1", "same-revision"),
            Record(
                "b",
                "2",
                "comments",
                "Other",
                "checksum independent",
                "https://example.com/2",
                "rev",
            ),
        ),
    )


@unittest.skipUnless(importlib.util.find_spec("tiktoken"), "tokens extra not installed")
class TokenTests(unittest.TestCase):
    def test_shrinking_token_window_preserves_the_matching_passage(self):
        import tiktoken

        text = "🙂 " * 1000 + "needle" + " 🙂" * 1000
        with Index() as index:
            index.import_snapshot(snapshot(text))
            result = index.search("needle")
            minimum = render(result, unit="tokens", excerpt_chars=6)
            evidence = render(result, unit="tokens", budget=minimum.count + 20)
            self.assertEqual(len(evidence.excerpts), 1)
            excerpt = evidence.excerpts[0]
            self.assertIn("needle", excerpt["text"])
            self.assertEqual(text[excerpt["record_start"] : excerpt["record_end"]], excerpt["text"])
            self.assertEqual(
                evidence.count,
                len(
                    tiktoken.get_encoding("o200k_base").encode(evidence.text, disallowed_special=())
                ),
            )
            self.assertLessEqual(evidence.count, evidence.budget)

    def test_complete_output_token_count_and_special_text(self):
        import tiktoken

        with Index() as index:
            index.import_snapshot(snapshot("🙂 checksum <|endoftext|> " * 100))
            result = index.search("checksum")
            for budget in (256, 512, 1024):
                evidence = render(result, budget=budget, unit="tokens", encoding="o200k_base")
                self.assertLessEqual(evidence.count, budget)
                self.assertEqual(
                    evidence.count,
                    len(
                        tiktoken.get_encoding("o200k_base").encode(
                            evidence.text, disallowed_special=()
                        )
                    ),
                )


@unittest.skipUnless(
    importlib.util.find_spec("sklearn") and importlib.util.find_spec("scipy"),
    "tfidf extra not installed",
)
class TfidfTests(unittest.TestCase):
    def test_fitted_once_query_independent_and_filter_before_cap(self):
        with Index() as index:
            index.import_snapshot(snapshot())
            result = index.search(
                "checksum", method="tfidf", component="comments", limit=1, candidate_limit=1
            )
            self.assertEqual([h.record.id for h in result.hits], ["b"])
            model = index._tfidf
            idf = model.transformer.idf_.copy()
            self.assertFalse(index.search("ultramissingzzzz", method="tfidf").hits)
            index.search("integrity", method="tfidf")
            self.assertIs(model, index._tfidf)
            self.assertEqual(model.fit_count, 1)
            self.assertTrue((model.transformer.idf_ == idf).all())

    def test_cache_refreshes_when_same_revision_has_new_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.sqlite"
            with Index(db) as writer:
                writer.import_snapshot(snapshot())
            with Index(db, readonly=True) as reader:
                reader.search("archive", method="tfidf")
                old_model = reader._tfidf
                with Index(db) as writer:
                    writer.import_snapshot(
                        snapshot("new telescope marker", title="Changed"), replace=True
                    )
                self.assertEqual(reader.search("telescope", method="tfidf").hits[0].record.id, "a")
                self.assertIsNot(old_model, reader._tfidf)
                self.assertFalse(reader.search("archive", method="tfidf").hits)

    def test_empty_index_and_empty_vocabulary(self):
        with Index() as index:
            index.import_snapshot(Snapshot("test/repo", ()), allow_empty=True)
            self.assertFalse(index.search("anything", method="tfidf").hits)
            blank = snapshot("", title="")
            blank = replace(blank, records=blank.records[:1])
            index.import_snapshot(blank, replace=True)
            self.assertFalse(index.search("anything", method="tfidf").hits)


if __name__ == "__main__":
    unittest.main()
