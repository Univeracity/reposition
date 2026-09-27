from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from reposition import Record, Snapshot
from reposition.models import sha256

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "reposition_evaluation", ROOT / "benchmarks/evaluate.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class BenchmarkTests(unittest.TestCase):
    def test_output_budget_separates_retrieval_from_citation_retention(self):
        records = tuple(
            Record(id, item, "summary", "Archive", "needle", f"https://example.com/{item}", "rev")
            for id, item in (("a", "1"), ("b", "2"))
        )
        labels = {
            "label_authority": "synthetic test",
            "cases": [
                {"id": "two-matches", "query": "needle", "relevance": {"1": 3, "2": 3}},
                {"id": "absent", "query": "ultramissingzzzz", "relevance": {}},
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            source, cases = Path(folder) / "source.json", Path(folder) / "cases.json"
            source.write_text(json.dumps(Snapshot("test/repo", records).to_dict()))
            cases.write_text(json.dumps(labels))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                evaluation.main([str(source), str(cases), "--chars", "700", "--repeats", "1"])
        result = json.loads(output.getvalue())
        method = result["methods"]["fts5"]
        case, absent = method["cases"]
        evidence = case["evidence"]
        self.assertEqual(case["metrics"]["known_positive_recall"], 1)
        self.assertEqual(evidence["metrics"]["known_positive_citation_recall"], 0.5)
        self.assertEqual(evidence["metrics"]["retrieved_positive_citation_retention"], 0.5)
        self.assertEqual(method["mean_known_positive_citation_recall"], 0.5)
        self.assertEqual(evidence["omitted_hits"], 1)
        self.assertEqual(evidence["count"], len(evidence["text"]))
        self.assertLessEqual(evidence["count"], 700)
        self.assertEqual(evidence["sha256"], sha256(evidence["text"]))
        self.assertIsNone(absent["evidence"]["metrics"]["known_positive_citation_recall"])
        self.assertEqual(result["settings"]["evidence_unit"], "characters")

    def test_hard_negative_citations_and_unretrieved_positives(self):
        evidence = SimpleNamespace(excerpts=({"item": "1"},))
        metrics = evaluation.evidence_metrics(evidence, ["1", "2"], {"2": 3, "3": 1}, ["1"])
        self.assertEqual(metrics["known_positive_citation_recall"], 0)
        self.assertEqual(metrics["retrieved_positive_citation_retention"], 0)
        self.assertEqual(metrics["hard_negative_citations"], ["1"])
        metrics = evaluation.evidence_metrics(evidence, ["1"], {"2": 3}, [])
        self.assertIsNone(metrics["retrieved_positive_citation_retention"])


if __name__ == "__main__":
    unittest.main()
