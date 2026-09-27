from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from reposition import Index, Record, Snapshot, load_snapshot, render
from reposition.cli import main
from reposition.importers import components, github, normalized
from reposition.models import sha256
from reposition.query import expression

ROOT = Path(__file__).resolve().parents[1]


def record(
    id="a",
    item="1",
    component="summary",
    title="Archive integrity",
    text="old bytes checksum",
    **kwargs,
):
    return Record(
        id,
        item,
        component,
        title,
        text,
        f"https://github.com/test/repo/issues/{item}",
        "retained-source-revision",
        **kwargs,
    )


class ContractTests(unittest.TestCase):
    def test_snapshot_binds_actual_text_with_unchanged_revision(self):
        old = Snapshot("test/repo", (record(),))
        new = replace(old, records=(replace(old.records[0], text="different bytes"),))
        self.assertNotEqual(old.digest, new.digest)
        self.assertEqual(old.records[0].source_revision, new.records[0].source_revision)

    def test_digest_is_order_independent_and_coverage_sensitive(self):
        a, b = record(), record("b", "2")
        old = Snapshot("test/repo", (a, b))
        self.assertEqual(old.digest, Snapshot("test/repo", (b, a)).digest)
        self.assertNotEqual(old.digest, replace(old, coverage={"comments": "unknown"}).digest)

    def test_duplicate_ids_hash_mismatch_and_bad_offsets_fail(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            Snapshot("test/repo", (record(), record()))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            Record.from_dict({**record().to_dict(), "text": "changed"})
        with self.assertRaises(ValueError):
            record(source_start=True)
        with self.assertRaises(ValueError):
            Record.from_dict({**record().to_dict(), "surprise": True})

    def test_native_round_trip(self):
        snapshot = Snapshot("test/repo", (record(),), {"items": "sample"})
        self.assertEqual(normalized(snapshot.to_dict()).digest, snapshot.digest)

    def test_github_null_body_and_comment_count(self):
        snapshot = github(
            [{"number": 1, "title": "Empty body", "body": None, "comments": 4}], "test/repo"
        )
        self.assertEqual(len(snapshot.records), 1)
        self.assertEqual(snapshot.records[0].text, "")
        self.assertEqual(snapshot.coverage["items"], "unknown")
        self.assertEqual(snapshot.records[0].coverage, {})

    def test_github_invalid_arrays_and_duplicate_numbers_fail(self):
        with self.assertRaises(ValueError):
            github([{"number": 1, "title": "x", "comments": {"nodes": []}}], "test/repo")
        with self.assertRaises(ValueError):
            github([{"number": 1, "title": "x"}] * 2, "test/repo")

    def test_github_source_identity_rejects_a_mixed_repository(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            github(
                [{"number": 1, "title": "x", "url": "https://github.com/other/repo/issues/1"}],
                "test/repo",
            )
        snapshot = github(
            [{"number": 1, "title": "x", "url": "https://api.github.com/repos/test/repo/issues/1"}],
            "test/repo",
        )
        self.assertEqual(snapshot.records[0].uri, "https://github.com/test/repo/issues/1")

    def test_stale_component_coverage_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, manifest = Path(tmp) / "documents.json", Path(tmp) / "manifest.json"
            source.write_text("[]")
            manifest.write_text(json.dumps({"documentsSha256": "wrong"}))
            with self.assertRaisesRegex(ValueError, "does not match input bytes"):
                load_snapshot(
                    source, format="components", repository="test/repo", manifest_path=manifest
                )

    def test_component_cache_retains_absolute_offsets(self):
        value = [
            {
                "id": "a",
                "item": 1,
                "repo": "repo",
                "component": "comments",
                "title": "x",
                "text": "🙂 café",
                "uri": "https://example.com",
                "sourceRevision": "rev",
                "span": {"charStart": 32, "charEnd": 38},
                "excerptSha256": sha256("🙂 café"),
                "componentPossiblyPartial": True,
            }
        ]
        snapshot = components(
            value, "test/repo", {"componentCoverage": {"1": {"comments": "partial"}}}
        )
        self.assertEqual(snapshot.records[0].source_start, 32)
        self.assertEqual(snapshot.records[0].coverage, {"comments": "partial"})
        value[0]["excerptSha256"] = "wrong"
        with self.assertRaises(ValueError):
            components(value, "test/repo")

    def test_literal_query_policy(self):
        self.assertEqual(
            expression('the "remote object" --checksum'), '"remote object" OR "checksum"'
        )
        self.assertEqual(expression('"unterminated checksum'), '"unterminated" OR "checksum"')
        self.assertEqual(expression("---"), "")
        self.assertEqual(expression("a"), '"a"')
        self.assertEqual(expression("x OR y; DROP TABLE records"), '"drop" OR "table" OR "records"')


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "index.sqlite"
        self.snapshot = Snapshot(
            "test/repo",
            (
                record("a", "1", title="checksum checksum", text="alpha"),
                record("b", "1", component="comments", title="Comment", text="checksum"),
                record("c", "2", title="Other", text="checksum beta"),
                record("d", "3", title="Distinct", text="gamma"),
            ),
            {"items": "sample"},
        )
        with Index(self.db) as index:
            index.import_snapshot(self.snapshot)

    def tearDown(self):
        self.tmp.cleanup()

    def test_persistence_and_item_deduplication(self):
        with Index(self.db, readonly=True) as index:
            result = index.search("checksum")
            self.assertEqual([h.record.item for h in result.hits], ["1", "2"])
            self.assertEqual(result.hits[0].record.id, "a")
            self.assertEqual(index.export().digest, self.snapshot.digest)

    def test_filters_apply_before_cap_and_anchor_respects_filter(self):
        with Index(self.db, readonly=True) as index:
            result = index.search("#1 checksum", component="comments", limit=1, candidate_limit=1)
            self.assertEqual([h.record.id for h in result.hits], ["b"])
            self.assertFalse(result.diagnostics["candidates_truncated"])

    def test_leading_anchor_only(self):
        with Index(self.db, readonly=True) as index:
            self.assertEqual(index.search("#3 checksum").hits[0].reason, "explicit-anchor")
            self.assertNotEqual(index.search("checksum #3").hits[0].record.item, "3")

    def test_cap_is_detected_and_unknown_is_not_a_confidence_score(self):
        with Index(self.db, readonly=True) as index:
            result = index.search("checksum", limit=1, candidate_limit=1)
            self.assertTrue(result.diagnostics["candidates_truncated"])
            self.assertFalse(result.diagnostics["score_is_probability"])
            self.assertFalse(index.search("ultramissingzzzz").hits)
            self.assertFalse(index.search("").hits)

    def test_invalid_limits(self):
        with Index(self.db) as index:
            for values in [
                {"limit": 0},
                {"limit": 3, "candidate_limit": 2},
                {"candidate_limit": True},
            ]:
                with self.assertRaises(ValueError):
                    index.search("checksum", **values)

    def test_refresh_removes_deleted_and_changed_evidence(self):
        new = Snapshot("test/repo", (record(text="fresh marker"),))
        with Index(self.db) as index:
            with self.assertRaises(ValueError):
                index.import_snapshot(new)
            index.import_snapshot(new, replace=True)
            self.assertFalse(index.search("checksum").hits)
            self.assertEqual(index.search("fresh marker").hits[0].record.text, "fresh marker")
            self.assertEqual(index.info()["snapshot_digest"], new.digest)

    def test_failed_transaction_keeps_previous_snapshot(self):
        with Index(self.db) as index:
            index.connection.execute(
                "CREATE TRIGGER reject_insert BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'injected failure'); END"
            )
            index.connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                index.import_snapshot(
                    Snapshot("test/repo", (record(text="replacement"),)), replace=True
                )
            self.assertEqual(index.export().digest, self.snapshot.digest)
            self.assertTrue(index.search("checksum").hits)

    def test_repository_and_empty_replacement_guards(self):
        with Index(self.db) as index:
            with self.assertRaises(ValueError):
                index.import_snapshot(Snapshot("other/repo", (record(),)), replace=True)
            with self.assertRaises(ValueError):
                index.import_snapshot(Snapshot("test/repo", ()), replace=True)
            self.assertEqual(index.export().digest, self.snapshot.digest)
            index.import_snapshot(Snapshot("test/repo", ()), replace=True, allow_empty=True)
            self.assertEqual(index.info()["record_count"], 0)
            self.assertFalse(index.search("checksum").hits)

    def test_foreign_database_is_not_modified(self):
        foreign = Path(self.tmp.name) / "foreign.sqlite"
        with sqlite3.connect(foreign) as connection:
            connection.execute("CREATE TABLE important(value TEXT)")
            connection.execute("INSERT INTO important VALUES('keep')")
        with self.assertRaises(ValueError):
            Index(foreign)
        with sqlite3.connect(foreign) as connection:
            self.assertEqual(connection.execute("SELECT * FROM important").fetchone()[0], "keep")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.index = Index()
        self.snapshot = Snapshot(
            "test/repo",
            (
                record(text="🙂 café checksum " * 100, source_start=42),
                record("b", "2", text="checksum " * 100),
            ),
            {"items": "partial"},
        )
        self.index.import_snapshot(self.snapshot)
        self.result = self.index.search("checksum")

    def tearDown(self):
        self.index.close()

    def assert_evidence(self, evidence):
        self.assertEqual(evidence.sha256, sha256(evidence.text))
        self.assertLessEqual(evidence.count, evidence.budget)
        docs = {r.id: r for r in self.snapshot.records}
        for excerpt in evidence.excerpts:
            record = docs[excerpt["record_id"]]
            self.assertEqual(
                excerpt["text"], record.text[excerpt["record_start"] : excerpt["record_end"]]
            )
            self.assertEqual(excerpt["sha256"], sha256(excerpt["text"]))
            self.assertEqual(excerpt["source_start"], record.source_start + excerpt["record_start"])

    def test_character_budget_and_offsets(self):
        for budget in (600, 900, 1800, 8000):
            evidence = render(self.result, budget=budget)
            self.assert_evidence(evidence)
            self.assertEqual(evidence.count, len(evidence.text))
            self.assertEqual(len(evidence.excerpts) + evidence.omitted_hits, len(self.result.hits))

    def test_too_small_budget_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "too small"):
            render(self.result, budget=10)

    def test_empty_body_is_citable(self):
        with Index() as index:
            index.import_snapshot(Snapshot("test/repo", (record(text=""),)))
            evidence = render(index.search("integrity"))
            self.assertEqual(evidence.excerpts[0]["text"], "")


class RelationTests(unittest.TestCase):
    def test_incoming_external_missing_and_code_references(self):
        snapshot = Snapshot(
            "test/repo",
            (
                record(
                    "a",
                    "1",
                    text="Fixes #2; also other/repo#2 and https://github.com/other/repo/issues/2. See #99.",
                ),
                record("b", "2", text="No link here."),
                record("c", "3", component="files", text="code has #2"),
            ),
        )
        with Index() as index:
            index.import_snapshot(snapshot)
            incoming = index.related("2")
            self.assertEqual(len(incoming["edges"]), 1)
            self.assertEqual(incoming["edges"][0]["predicate"], "claims_fixes")
            self.assertFalse(incoming["edges"][0]["equivalence_established"])
            outgoing = index.related("1")
            self.assertEqual(len(outgoing["edges"]), 4)
            self.assertEqual(sum(e["target_present"] for e in outgoing["edges"]), 1)
            self.assertEqual(index.related("1", limit=1)["omitted_edges"], 3)
            for edge in outgoing["edges"]:
                e = edge["evidence"]
                self.assertEqual(
                    snapshot.records[0].text[e["source_start"] : e["source_end"]], e["text"]
                )
                self.assertEqual(sha256(e["text"]), e["sha256"])


class CliTests(unittest.TestCase):
    def test_index_search_export_and_read_only_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "repo.sqlite")
            source = str(ROOT / "examples/github-cache.json")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(
                    main(
                        [
                            "index",
                            source,
                            "--format",
                            "github",
                            "--repo",
                            "example/packages",
                            "--db",
                            db,
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(out.getvalue())["item_count"], 5)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["search", "archive bytes", "--json", "--db", db]), 0)
            response = json.loads(out.getvalue())
            self.assertIn("search", response)
            self.assertIn("evidence", response)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["export", "--db", db]), 0)
            self.assertEqual(normalized(json.loads(out.getvalue())).repository, "example/packages")
            missing = Path(tmp) / "missing.sqlite"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["search", "x", "--db", str(missing)]), 2)
            self.assertFalse(missing.exists())

    def test_malformed_input_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "invalid.json"
            source.write_text('{"items": "wrong"}')
            db = Path(tmp) / "new.sqlite"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "index",
                            str(source),
                            "--format",
                            "github",
                            "--repo",
                            "test/repo",
                            "--db",
                            str(db),
                        ]
                    ),
                    2,
                )
            self.assertFalse(db.exists())

    def test_example_import_needs_no_experiment_sources(self):
        snapshot = load_snapshot(
            ROOT / "examples/github-cache.json", format="github", repository="example/packages"
        )
        self.assertEqual(len(snapshot.records), 7)


if __name__ == "__main__":
    unittest.main()
