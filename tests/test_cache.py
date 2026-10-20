from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reposition import CacheIndex, ProjectionPolicy, TriageCache
from reposition.cache_index import encode_response
from reposition.cli import main
from reposition.models import canonical, sha256
from reposition.projections import pointer_get, project, reproduce

fixture_spec = importlib.util.spec_from_file_location(
    "cache_fixture", Path(__file__).resolve().parents[1] / "examples/triage_cache.py"
)
fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture)
END, make_cache, publish, sealed = fixture.END, fixture.make_cache, fixture.publish, fixture.sealed


def evidence_bytes(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for directory in ("objects", "snapshots", "corpora")
        for p in (root / directory).glob("*")
    } | {"cache.json": (root / "cache.json").read_bytes()}


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "cache"
        self.manifest, self.corpus = make_cache(self.root)
        self.snapshot = self.manifest["snapshot_id"]
        self.source = TriageCache(self.root)
        self.database = self.root.parent / "reposition/search.sqlite"
        self.build()

    def build(self, **kwargs):
        return CacheIndex.build(self.source, self.database, snapshot=self.snapshot, **kwargs)

    def query(self, text="terminal", **kwargs):
        with CacheIndex(self.database) as index:
            return index.query(self.source, text, snapshot=self.snapshot, **kwargs)

    def state(self):
        return json.loads((self.root / "corpora" / (self.corpus + ".state.json")).read_text())

    def change_state(self, state):
        state.pop("checksum", None)
        (self.root / "corpora" / (self.corpus + ".state.json")).write_text(
            canonical(sealed(state)) + "\n"
        )

    def test_queries_verify_returned_sources_and_complete_wire_budget(self):
        result = self.query("SUSPEND café 🙂", max_bytes=7000, fragment_bytes=80)
        encoded = encode_response(result).encode("utf-8")
        self.assertEqual(result["budget"]["used_bytes"], len(encoded))
        self.assertLessEqual(len(encoded), 7000)
        self.assertEqual(result["requests"], 0)
        self.assertTrue(result["items"])
        self.assertIsNotNone(result["continuation"])
        for item in result["items"]:
            for fragment in item["fragments"]:
                self.assertTrue(fragment["verified"])
                excerpt = fragment["excerpt"]
                self.assertLessEqual(len(excerpt["text"].encode("utf-8")), 80)
                self.assertEqual(excerpt["sha256"], sha256(excerpt["text"]))
                record = next(
                    r for r in self.manifest["items"] if r["identity"] == item["identity"]
                )
                payload = self.source.object(
                    fragment["component"],
                    record["components"][fragment["component"]],
                    item["identity"]["kind"],
                )
                original = (
                    payload
                    if fragment["locator"]["pointer"] is None
                    else pointer_get(json.loads(payload), fragment["locator"]["pointer"])
                )
                self.assertEqual(
                    original.encode("utf-8")[excerpt["start"] : excerpt["end"]].decode("utf-8"),
                    excerpt["text"],
                )

    def test_components_fields_and_item_filters_apply_before_candidate_cap(self):
        result = self.query(
            "terminal",
            components=("files", "review_comments"),
            fields=("path",),
            kind="pr",
            state="open",
            label="terminal",
            author="demo",
            after="2026-09-27T12:00:00Z",
            before="2026-09-27T12:02:00Z",
            max_snippets_per_item=1,
        )
        self.assertEqual({i["identity"]["number"] for i in result["items"]}, {2, 4})
        self.assertFalse(self.query(label="absent")["items"])
        self.assertFalse(self.query(after="2027-01-01T00:00:00Z")["items"])
        self.assertFalse(self.query(author="someone-else")["items"])
        self.assertFalse(result["diagnostics"]["score_is_probability"])
        self.assertTrue(
            all(f["locator"]["field"] == "path" for i in result["items"] for f in i["fragments"])
        )

    def test_all_nine_components_are_searchable(self):
        for component, query in {
            "summary": "suspend",
            "comments": "café",
            "files": "terminal",
            "diff": "café",
            "reviews": "Alternative",
            "review_comments": "another cause",
            "checks": "Synthetic",
            "closing_issues": "issues",
            "timeline": "observation",
        }.items():
            with self.subTest(component=component):
                result = self.query(query, components=(component,), max_snippets_per_item=1)
                self.assertTrue(result["items"])
                self.assertTrue(
                    all(
                        f["component"] == component for i in result["items"] for f in i["fragments"]
                    )
                )

    def test_exact_phrases_and_title_path_weights(self):
        result = self.query('"fails after suspend"', components=("summary",), fields=("body",))
        self.assertEqual(result["diagnostics"]["ranked_groups"], 4)
        self.assertIn("fails after suspend", result["items"][0]["fragments"][0]["excerpt"]["text"])
        self.assertFalse(self.query('"after fails suspend"')["items"])
        self.assertEqual(
            self.query("terminal", components=("summary",))["items"][0]["fragments"][0]["locator"][
                "field"
            ],
            "title",
        )

    def test_item_diversity_and_fragment_mode(self):
        result = self.query(max_snippets_per_item=1)
        self.assertTrue(all(len(i["fragments"]) == 1 for i in result["items"]))
        self.assertGreater(result["diagnostics"]["fragments_omitted_per_item"], 0)
        fragments = self.query(fragment_mode=True, max_bytes=60000)
        self.assertGreater(len(fragments["items"]), len(result["items"]))
        self.assertTrue(all(len(i["fragments"]) == 1 for i in fragments["items"]))

    def test_pagination_pages_matches_and_binds_all_query_limits(self):
        first = self.query(limit=1, max_snippets_per_item=1)
        seen = {first["items"][0]["identity"]["number"]}
        cursor = first["continuation"]
        while cursor:
            page = self.query(limit=1, max_snippets_per_item=1, cursor=cursor)
            self.assertEqual(len(page["items"]), 1)
            n = page["items"][0]["identity"]["number"]
            self.assertNotIn(n, seen)
            seen.add(n)
            cursor = page["continuation"]
        self.assertEqual(seen, {1, 2, 3, 4})
        for change in (
            {"limit": 2},
            {"max_bytes": 13000},
            {"fragment_bytes": 80},
            {"components": ("comments",)},
            {"fields": ("body",)},
            {"label": "terminal"},
            {"fragment_mode": True},
            {"max_age": 0},
            {"candidates": 100},
            {"max_verify_bytes": 4096},
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "continuation"):
                options = {
                    "limit": 1,
                    "max_snippets_per_item": 1,
                    "cursor": first["continuation"],
                    **change,
                }
                self.query(**options)
        with self.assertRaisesRegex(ValueError, "continuation"):
            self.query(
                "different query", limit=1, max_snippets_per_item=1, cursor=first["continuation"]
            )

    def test_candidate_and_budget_omissions_are_explicit(self):
        result = self.query(candidates=1, max_snippets_per_item=1)
        self.assertTrue(result["diagnostics"]["candidates_truncated"])
        self.assertEqual(result["diagnostics"]["candidates_considered"], 1)
        with self.assertRaisesRegex(ValueError, "byte budget"):
            self.query(max_bytes=512)
        with self.assertRaisesRegex(ValueError, "verification byte ceiling"):
            self.query(max_verify_bytes=1)

    def test_only_returned_source_objects_are_read_and_shared_objects_read_once(self):
        original = self.source.object
        with patch.object(self.source, "object", wraps=original) as reader:
            result = self.query("terminal", components=("files",), fields=("path",))
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(reader.call_count, 1)

    def test_corrupt_object_fails_closed_without_echoing_source_text(self):
        reference = self.query("café", components=("comments",))["items"][0]["fragments"][0][
            "object"
        ]
        path = self.source.path("objects", reference["sha256"] + ".json")
        path.write_bytes(b"PRIVATE SOURCE DO NOT ECHO")
        with self.assertRaisesRegex(ValueError, "failed verification") as error:
            self.query("café", components=("comments",))
        self.assertNotIn("PRIVATE SOURCE", str(error.exception))

    def test_bad_manifest_and_future_source_version_fail(self):
        path = self.root / "snapshots" / (self.snapshot + ".json")
        value = json.loads(path.read_text())
        value["items"][0]["revision"]["updated_at"] = "2026-09-28T00:00:00Z"
        path.write_text(canonical(value))
        with self.assertRaisesRegex(ValueError, "source selection changed"):
            self.query()
        metadata = json.loads((self.root / "cache.json").read_text())
        metadata["schema_version"] = 2
        (self.root / "cache.json").write_text(canonical(metadata))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            TriageCache(self.root)

    def test_index_corruption_and_explicit_recovery(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE manifest SET data='broken'")
        with self.assertRaises(ValueError):
            self.query()
        with self.assertRaises(ValueError):
            self.build(replace=True)
        self.build(recover=True)
        self.assertTrue(self.query()["items"])

    def test_corrupt_projection_never_searches_nearby_source(self):
        identifier = self.query()["items"][0]["fragments"][0]["unit_id"]
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE units SET projection=X'00' WHERE id=?", (identifier,))
        with self.assertRaisesRegex(ValueError, "corrupt; rebuild"):
            self.query()

    def test_dropped_fts_table_has_explicit_rebuild_path(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE evidence")
        with self.assertRaisesRegex(ValueError, "rebuild"):
            self.query()

    def test_false_fts_postings_cannot_quote_an_unrelated_source(self):
        with sqlite3.connect(self.database) as connection:
            rowid = connection.execute("SELECT min(rowid) FROM units").fetchone()[0]
            connection.execute(
                "INSERT INTO evidence(rowid,text) VALUES(?,?)", (rowid, "inventedmarker")
            )
        with self.assertRaisesRegex(ValueError, "postings.*rebuild"):
            self.query("inventedmarker")

    def test_future_index_version_is_not_downgraded_even_by_recovery(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA user_version=2")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, "upgrade"):
            self.build(recover=True)
        self.assertEqual(before, self.database.read_bytes())

    def test_foreign_repository_and_stable_id_conflicts_fail_closed(self):
        metadata_path = self.root / "cache.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["repository"]["database_id"] = 999
        metadata_path.write_text(canonical(metadata))
        foreign = TriageCache(self.root)
        with CacheIndex(self.database) as index, self.assertRaises(ValueError):
            index.query(foreign, "terminal", snapshot=self.snapshot)
        with CacheIndex(self.database) as index, self.assertRaises(ValueError):
            index.info(foreign)
        with self.assertRaises(ValueError):
            CacheIndex.build(foreign, self.database, snapshot=self.snapshot, replace=True)

    def test_metadata_inspection_checks_identity_and_scope_without_a_source_audit(self):
        arguments = ["cache-info", "--cache", str(self.root), "--db", str(self.database)]
        before = evidence_bytes(self.root)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(arguments), 0)
        info = json.loads(output.getvalue())
        self.assertEqual(info["schema"], "reposition.cache-info.v2")
        self.assertEqual(info["repository"], self.manifest["repository"])
        self.assertEqual(info["indexed_snapshot_count"], 1)
        self.assertNotIn("indexed_snapshots", info)
        self.assertNotIn("indexed_snapshots_next_offset", info)
        self.assertFalse(info["source_checkpoint_verified"])
        self.assertFalse(info["source_payloads_verified"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([*arguments, "--corpus", self.corpus]), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([*arguments, "--snapshot", self.snapshot]), 0)
        self.assertEqual(before, evidence_bytes(self.root))
        (self.root / "snapshots" / (self.snapshot + ".json")).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([*arguments, "--snapshot", self.snapshot]), 0)
        metadata = json.loads((self.root / "cache.json").read_text())
        for changes in (
            {"full_name": "other/repository"},
            {"host": "other.example"},
            {"database_id": 999},
            {"node_id": "R_other"},
            {"database_id": None},
        ):
            with self.subTest(changes=changes), contextlib.redirect_stderr(io.StringIO()):
                foreign = copy.deepcopy(metadata)
                foreign["repository"].update(changes)
                (self.root / "cache.json").write_text(canonical(foreign))
                self.assertEqual(main(arguments), 2)

    def test_metadata_snapshot_ids_are_bounded_and_paginated(self):
        snapshots = [f"{number:064x}" for number in range(4932)]
        with sqlite3.connect(self.database) as connection:
            manifest = json.loads(connection.execute("SELECT data FROM manifest").fetchone()[0])
            manifest.pop("checksum")
            manifest["indexed_snapshots"] = snapshots
            manifest["checksum"] = sha256(canonical(manifest))
            connection.execute("UPDATE manifest SET data=?", (canonical(manifest),))

        arguments = ["cache-info", "--cache", str(self.root), "--db", str(self.database)]

        def inspect(*flags):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main([*arguments, *flags]), 0)
            return json.loads(output.getvalue()), len(output.getvalue().encode("utf-8"))

        default, size = inspect()
        self.assertEqual(default["indexed_snapshot_count"], len(snapshots))
        self.assertNotIn("indexed_snapshots", default)
        self.assertNotIn("indexed_snapshots_next_offset", default)
        self.assertLess(size, 10000)

        first, size = inspect("--snapshot-ids-limit", "100")
        self.assertEqual(first["indexed_snapshots"], snapshots[:100])
        self.assertEqual(first["indexed_snapshots_next_offset"], 100)
        self.assertLess(size, 20000)

        second, _ = inspect(
            "--snapshot-ids-limit",
            "100",
            "--snapshot-ids-offset",
            str(first["indexed_snapshots_next_offset"]),
            "--checkpoint",
            first["checksum"],
        )
        self.assertEqual(second["indexed_snapshots"], snapshots[100:200])
        self.assertEqual(second["indexed_snapshots_next_offset"], 200)

        last, _ = inspect(
            "--snapshot-ids-limit",
            "100",
            "--snapshot-ids-offset",
            "4900",
            "--checkpoint",
            first["checksum"],
        )
        self.assertEqual(last["indexed_snapshots"], snapshots[4900:])
        self.assertIsNone(last["indexed_snapshots_next_offset"])

        for flags in (
            ("--snapshot-ids-limit", "10", "--snapshot-ids-offset", "1"),
            ("--checkpoint", "0" * 64),
            ("--snapshot-ids-limit", "101"),
            ("--snapshot-ids-limit", "-1"),
            ("--snapshot-ids-offset", "1"),
            ("--snapshot-ids-limit", "10", "--snapshot-ids-offset", "-1"),
        ):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*arguments, *flags]), 2)

        with sqlite3.connect(self.database) as connection:
            manifest.pop("checksum")
            manifest["indexed_snapshots"] = snapshots[:100]
            manifest["checksum"] = sha256(canonical(manifest))
            connection.execute("UPDATE manifest SET data=?", (canonical(manifest),))

        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(
                main(
                    [
                        *arguments,
                        "--snapshot-ids-limit",
                        "100",
                        "--snapshot-ids-offset",
                        "100",
                        "--checkpoint",
                        first["checksum"],
                    ]
                ),
                2,
            )
        self.assertIn("checkpoint changed", error.getvalue())

    def test_failed_build_keeps_previous_index_and_cleans_temporaries(self):
        before = self.database.read_bytes()
        for limits in ({"max_units": 1}, {"max_index_bytes": 65536}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.build(replace=True, **limits)
            self.assertEqual(before, self.database.read_bytes())
            self.assertEqual(list(self.database.parent.glob("*.building")), [])
            self.assertFalse(self.database.with_name("search.sqlite.build-lock").exists())
        self.assertTrue(self.query()["items"])

    def test_source_changes_during_build_never_publish(self):
        before = self.database.read_bytes()
        with (
            patch.object(
                self.source,
                "checkpoint",
                side_effect=[self.source.checkpoint(snapshot=self.snapshot), "changed"],
            ),
            self.assertRaisesRegex(ValueError, "changed during build"),
        ):
            self.build(replace=True)
        self.assertEqual(before, self.database.read_bytes())

    def test_index_build_and_reads_do_not_mutate_evidence_or_corpus_progress(self):
        before = evidence_bytes(self.root)
        self.build(replace=True)
        result = self.query()
        unit = result["items"][0]["fragments"][0]["unit_id"]
        with CacheIndex(self.database) as index:
            index.retrieve(
                self.source, (unit,), checkpoint=result["checkpoint"], snapshot=self.snapshot
            )
        self.assertEqual(before, evidence_bytes(self.root))
        with self.assertRaisesRegex(ValueError, "read-only"):
            CacheIndex.build(
                self.source, self.root / "objects/forbidden.sqlite", snapshot=self.snapshot
            )

    def test_corpus_membership_and_progress_are_exact(self):
        plan = json.loads((self.root / "corpora" / (self.corpus + ".plan.json")).read_text())
        plan.pop("checksum")
        plan["profile"] = "pr-checks"
        plan = sealed(plan)
        checks_corpus = plan["checksum"]
        (self.root / "corpora" / (checks_corpus + ".plan.json")).write_text(canonical(plan))
        state = self.state()
        state.pop("checksum")
        state["plan_id"] = checks_corpus
        (self.root / "corpora" / (checks_corpus + ".state.json")).write_text(
            canonical(sealed(state))
        )
        CacheIndex.build(self.source, self.database, corpus=checks_corpus, replace=True)
        with CacheIndex(self.database) as index:
            checks = index.query(
                self.source, "terminal", corpus=checks_corpus, components=("checks",)
            )
            self.assertTrue(checks["items"])
            self.assertEqual(checks["coverage"]["selected_members"], 4)

        CacheIndex.build(self.source, self.database, corpus=self.corpus, replace=True)
        with CacheIndex(self.database) as index:
            first = index.query(self.source, "terminal", corpus=self.corpus, limit=1)
            self.assertEqual(first["coverage"]["selected_members"], 4)
            state = self.state()
            state["updated_at"] = "2026-09-27T12:03:00Z"
            self.change_state(state)
            with self.assertRaisesRegex(ValueError, "source selection changed"):
                index.query(
                    self.source,
                    "terminal",
                    corpus=self.corpus,
                    limit=1,
                    cursor=first["continuation"],
                )
            with self.assertRaisesRegex(ValueError, "scope differs"):
                index.query(self.source, "terminal", snapshot=self.snapshot)

    def test_unstarted_corpus_keeps_missing_members_visible(self):
        (self.root / "corpora" / (self.corpus + ".state.json")).unlink()
        CacheIndex.build(self.source, self.database, corpus=self.corpus, replace=True)
        with CacheIndex(self.database) as index:
            result = index.query(self.source, "terminal", corpus=self.corpus)
        self.assertFalse(result["items"])
        self.assertEqual(result["coverage"]["missing_members"], 4)
        self.assertEqual(result["coverage"]["outcomes"]["pending"], 4)

    def test_new_partial_corpus_observation_is_not_hidden_by_old_complete_one(self):
        CacheIndex.build(self.source, self.database, corpus=self.corpus, replace=True)
        records = copy.deepcopy(self.manifest["items"])
        comment = records[1]["components"]["comments"]
        comment.update(status="partial", error="Synthetic unfinished page", truncated=True)
        newer = publish(self.root, records)
        state = self.state()
        state["items"]["pr:2"].update(snapshot_id=newer["snapshot_id"], outcome="gaps")
        self.change_state(state)
        with (
            CacheIndex(self.database) as index,
            self.assertRaisesRegex(ValueError, "source selection changed"),
        ):
            index.query(self.source, "terminal", corpus=self.corpus)
        CacheIndex.build(self.source, self.database, corpus=self.corpus, replace=True)
        with CacheIndex(self.database) as index:
            result = index.query(
                self.source,
                "café",
                corpus=self.corpus,
                components=("comments",),
                kind="pr",
                max_snippets_per_item=1,
            )
        item = next(i for i in result["items"] if i["identity"]["number"] == 2)
        self.assertEqual(item["fragments"][0]["snapshot_id"], newer["snapshot_id"])
        self.assertIn("partial", item["fragments"][0]["problems"])

    def test_rebuilt_index_invalidates_old_query_and_retrieval_checkpoints(self):
        first = self.query(limit=1)
        self.build(replace=True)
        with self.assertRaisesRegex(ValueError, "continuation"):
            self.query(limit=1, cursor=first["continuation"])
        unit = first["items"][0]["fragments"][0]["unit_id"]
        with (
            CacheIndex(self.database) as index,
            self.assertRaisesRegex(ValueError, "checkpoint changed"),
        ):
            index.retrieve(
                self.source, (unit,), checkpoint=first["checkpoint"], snapshot=self.snapshot
            )

    def test_object_limits_and_symlinks_fail_before_reading_unbounded_content(self):
        tiny = TriageCache(self.root, max_object_bytes=16)
        with self.assertRaisesRegex(ValueError, "failed verification"):
            CacheIndex.build(tiny, self.database, snapshot=self.snapshot, replace=True)
        reference = self.query("café", components=("comments",))["items"][0]["fragments"][0][
            "object"
        ]
        object_path = self.source.path("objects", reference["sha256"] + ".json")
        real = object_path.with_suffix(".saved")
        object_path.rename(real)
        object_path.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "verification"):
            self.query("café", components=("comments",))

    def test_cli_round_trip_uses_the_same_bounded_wire_format(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(
                [
                    "cache-query",
                    "--cache",
                    str(self.root),
                    "--snapshot",
                    self.snapshot,
                    "--db",
                    str(self.database),
                    "--query",
                    "terminal",
                    "--max-bytes",
                    "7000",
                    "--max-snippets-per-item",
                    "1",
                ]
            )
        self.assertEqual(code, 0, err.getvalue())
        result = json.loads(out.getvalue())
        self.assertEqual(result["budget"]["used_bytes"], len(out.getvalue().encode("utf-8")))
        self.assertLessEqual(result["budget"]["used_bytes"], 7000)

    def test_numeric_anchor_selects_exact_summary_without_relaxing_filters(self):
        result = self.query("#3 unrelated missing terms", max_snippets_per_item=1)
        self.assertEqual(result["items"][0]["identity"]["number"], 3)
        self.assertEqual(result["items"][0]["fragments"][0]["reason"], "explicit-anchor")
        self.assertFalse(self.query("#3 nonexistent", kind="pr")["items"])
        self.assertFalse(self.query("#3 nonexistent", components=("comments",))["items"])

    def test_open_index_handle_rejects_atomic_replacement(self):
        with CacheIndex(self.database) as index:
            first = index.query(self.source, "terminal", snapshot=self.snapshot, limit=1)
            self.build(replace=True)
            with self.assertRaisesRegex(ValueError, "was replaced"):
                index.query(
                    self.source,
                    "terminal",
                    snapshot=self.snapshot,
                    limit=1,
                    cursor=first["continuation"],
                )

    def test_read_transaction_binds_index_rows_through_resolution(self):
        with CacheIndex(self.database) as index:
            original = index._verify

            def verify(*args, **kwargs):
                self.assertTrue(index.connection.in_transaction)
                return original(*args, **kwargs)

            with patch.object(index, "_verify", side_effect=verify):
                index.query(self.source, "terminal", snapshot=self.snapshot)
            self.assertFalse(index.connection.in_transaction)

    def test_fixed_snapshot_ignores_unrelated_newer_partial_history(self):
        records = copy.deepcopy(self.manifest["items"])
        records[0]["components"]["comments"].update(
            status="partial", truncated=True, error="New synthetic partial observation"
        )
        newer = publish(self.root, records)
        self.assertNotEqual(newer["snapshot_id"], self.snapshot)
        result = self.query("café", components=("comments",), max_snippets_per_item=1)
        self.assertTrue(
            all(f["snapshot_id"] == self.snapshot for i in result["items"] for f in i["fragments"])
        )

    def test_source_progress_change_during_verification_prevents_response(self):
        CacheIndex.build(self.source, self.database, corpus=self.corpus, replace=True)
        original = self.source.object

        def changed(*args, **kwargs):
            payload = original(*args, **kwargs)
            state = self.state()
            state["updated_at"] = "2026-09-27T12:03:00Z"
            self.change_state(state)
            return payload

        with (
            patch.object(self.source, "object", side_effect=changed),
            CacheIndex(self.database) as index,
            self.assertRaisesRegex(ValueError, "source selection changed"),
        ):
            index.query(self.source, "terminal", corpus=self.corpus)

    def test_corpus_cannot_claim_complete_when_selected_component_is_partial(self):
        records = copy.deepcopy(self.manifest["items"])
        records[1]["components"]["comments"].update(
            status="partial", truncated=True, error="Synthetic missing page"
        )
        partial = publish(self.root, records)
        state = self.state()
        state["items"]["pr:2"]["snapshot_id"] = partial["snapshot_id"]
        self.change_state(state)
        with self.assertRaisesRegex(ValueError, "incomplete evidence"):
            CacheIndex.build(self.source, self.database, corpus=self.corpus, replace=True)

    def test_validation_rejects_bad_limits_filters_and_cursors(self):
        for options in (
            {"limit": 0},
            {"fragment_bytes": True},
            {"max_age": -1},
            {"max_verify_bytes": 0},
            {"components": ("unknown",)},
            {"kind": "other"},
            {"state": "unknown-state"},
            {"label": ""},
            {"after": "invalid"},
            {"after": "2027-01-01T00:00:00Z", "before": END},
            {"min_score": float("nan")},
            {"cursor": "%%%"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.query(**options)


class ProjectionTests(unittest.TestCase):
    def test_deterministic_unicode_chunks_reproduce_original_values(self):
        policy = ProjectionPolicy(max_bytes=256, overlap_bytes=32, max_lines=3)
        payload = canonical([{"id": 1, "body": "🙂 café\n" * 200}])
        first = list(project("comments", payload, policy))
        self.assertEqual(first, list(project("comments", payload, policy)))
        self.assertGreater(len(first), 1)
        for locator, text in first:
            self.assertEqual(text, reproduce(payload, locator))
            self.assertLessEqual(len(text.encode("utf-8")), 256)
            self.assertEqual(locator["pointer"], "/0/body")
            self.assertLessEqual(locator["line_end"] - locator["line_start"], 3)
        self.assertEqual(first[0][0]["start"], 0)
        self.assertEqual(first[-1][0]["end"], len(("🙂 café\n" * 200).encode("utf-8")))

    def test_diff_file_hunk_boundaries_are_not_repositioned(self):
        payload = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+🙂 café\n@@ -3 +3 @@\n-b\n+c\ndiff --git a/b.py b/b.py\n@@ -1 +1 @@\n-d\n+e\n"
        projected = list(project("diff", payload, ProjectionPolicy()))
        self.assertEqual(len(projected), 5)
        self.assertEqual(
            [locator["field"] for locator, _ in projected],
            ["file_header", "hunk", "hunk", "file_header", "hunk"],
        )
        self.assertEqual(
            [locator["filename"] for locator, _ in projected],
            ["a.py", "a.py", "a.py", "b.py", "b.py"],
        )
        self.assertEqual("".join(t for _, t in projected), payload)
        for locator, text in projected:
            self.assertEqual(text, reproduce(payload, locator))
            self.assertEqual(
                payload.encode("utf-8")[locator["start"] : locator["end"]].decode("utf-8"), text
            )

    def test_structural_locator_never_adopts_nearby_text(self):
        payload = canonical([{"body": "café"}])
        locator, _ = next(project("comments", payload, ProjectionPolicy()))
        with self.assertRaises(ValueError):
            reproduce(payload, {**locator, "start": 4})
        with self.assertRaises((KeyError, IndexError)):
            reproduce(payload, {**locator, "pointer": "/1/body"})


class ExpandedRetrievalTests(unittest.TestCase):
    def test_progressive_read_extends_past_index_chunk_without_losing_source_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "cache"
            manifest, _ = make_cache(root, count=1, repeat=400)
            snapshot = manifest["snapshot_id"]
            source = TriageCache(root)
            database = root.parent / "reposition/search.sqlite"
            CacheIndex.build(
                source,
                database,
                snapshot=snapshot,
                policy=ProjectionPolicy(max_bytes=256, overlap_bytes=16),
            )
            with CacheIndex(database) as index:
                result = index.query(
                    source,
                    "café",
                    snapshot=snapshot,
                    components=("comments",),
                    max_snippets_per_item=1,
                )
                unit = result["items"][0]["fragments"][0]["unit_id"]
                page = index.retrieve(
                    source,
                    (unit,),
                    snapshot=snapshot,
                    checkpoint=result["checkpoint"],
                    fragment_bytes=1000,
                    byte_offset=0,
                )
                fragment = page["items"][0]["fragments"][0]
                self.assertGreater(len(fragment["excerpt"]["text"].encode()), 256)
                self.assertLessEqual(len(fragment["excerpt"]["text"].encode()), 1000)
                self.assertTrue(fragment["continuation"])
                next_page = index.retrieve(
                    source,
                    (unit,),
                    snapshot=snapshot,
                    checkpoint=result["checkpoint"],
                    fragment_bytes=1000,
                    byte_offset=fragment["continuation"]["byte_offset"],
                )
                self.assertEqual(
                    next_page["items"][0]["fragments"][0]["excerpt"]["start"],
                    fragment["excerpt"]["end"],
                )
                with self.assertRaisesRegex(ValueError, "divides UTF-8"):
                    index.retrieve(
                        source,
                        (unit,),
                        snapshot=snapshot,
                        checkpoint=result["checkpoint"],
                        byte_offset=fragment["excerpt"]["text"].encode().index("🙂".encode()) + 1,
                    )


if __name__ == "__main__":
    unittest.main()


class NativeCILogsTests(unittest.TestCase):
    def setUp(self):
        import base64

        from reposition import ci_logs
        from reposition._demo_cache import write_object

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "cache"
        old, _ = make_cache(self.root)
        self.record = copy.deepcopy(old["items"][1])
        self.repo = old["repository"]
        self.source = TriageCache(self.root)
        self.database = self.root.parent / "view.sqlite"
        self.write_object = lambda value: write_object(self.root, value)
        self.identity = self.record["identity"]
        revision = self.record["revision"]
        self.head = revision["head_sha"]
        raw_repo = dict(
            full_name=self.repo["full_name"],
            id=self.repo["database_id"],
            node_id=self.repo["node_id"],
        )
        raw = dict(
            number=2,
            id=self.identity["database_id"],
            node_id=self.identity["node_id"],
            state="open",
            merged=False,
            title="Native CI test",
            body="source summary",
            updated_at=revision["updated_at"],
            html_url="https://github.com/example/triage/pull/2",
            base=dict(sha=revision["base_sha"], repo=raw_repo),
            head=dict(sha=self.head, repo=raw_repo),
        )
        self.record["components"]["summary"]["object"] = self.write_object(raw)
        source = dict(repository=self.repo, head_sha=self.head, fetched_at=END)
        app = dict(id=42, slug="github-actions")
        suite = dict(id=31, head_sha=self.head, app=app)
        check = dict(
            id=41,
            head_sha=self.head,
            app=app,
            check_suite=dict(id=31),
            name="Build native",
            status="completed",
            conclusion="failure",
        )
        self.checks = [
            dict(
                **source,
                kind="check_suite",
                resource=f"repos/example/triage/commits/{self.head}/check-suites",
                data=suite,
            ),
            dict(
                **source,
                kind="check_run",
                resource="repos/example/triage/check-suites/31/check-runs?filter=all",
                data=check,
            ),
        ]
        descriptor = self.record["components"]["checks"]
        descriptor.update(
            object=self.write_object(self.checks),
            received_count=2,
            expected_count=2,
            fetched_at=END,
        )
        self.snapshot = publish(self.root, [self.record])["snapshot_id"]
        run = dict(
            id=51,
            run_attempt=2,
            head_sha=self.head,
            check_suite_id=31,
            repository=raw_repo,
            url="https://api.github.com/repos/example/triage/actions/runs/51",
            name="Native workflow",
        )
        job = dict(
            id=61,
            run_id=51,
            head_sha=self.head,
            name="Native job",
            status="completed",
            conclusion="failure",
            url="https://api.github.com/repos/example/triage/actions/jobs/61",
            check_run_url="https://api.github.com/repos/example/triage/check-runs/41",
        )
        self.log_text = "\x1b[31merror café 🙂\x1b[0m\nsecond failure line\n"
        segment_raw = self.log_text.encode()
        self.packet = dict(
            schema_version=1,
            artifact="ci-failure-log",
            repository=self.repo,
            number=2,
            snapshot_id=self.snapshot,
            source_repository=self.repo,
            head_sha=self.head,
            check_id=41,
            check_name="Build native",
            run_id=51,
            attempt=2,
            job_id=61,
            check_source=dict(component="checks", object=descriptor["object"], index=1),
            run=run,
            job=job,
            membership=job,
            membership_resource="repos/example/triage/actions/runs/51/attempts/2/jobs?per_page=100&page=1",
            log_resource="repos/example/triage/actions/jobs/61/logs",
            fetched_at=END,
            download_partial=True,
            binding_revalidated=True,
            observed_bytes=500,
            observed_lines=20,
            observed_sha256="e" * 64,
            selection_basis="error-pattern leads",
            omissions=["intervening lines omitted"],
            segments=[
                dict(
                    start_line=5,
                    end_line=6,
                    start_byte=100,
                    end_byte=100 + len(segment_raw),
                    last_line_complete=True,
                    content_base64=base64.b64encode(segment_raw).decode(),
                )
            ],
            request_budget=20,
            max_log_bytes=1000,
            requests=8,
            reserved_requests=11,
        )
        self.scope = dict(
            repository=self.repo,
            number=2,
            snapshot_id=self.snapshot,
            source_repository=self.repo["full_name"],
            check_id=41,
        )
        (self.root / "ci-logs").mkdir()
        self.pointer = self.root / "ci-logs" / (sha256(canonical(self.scope)) + ".json")
        self.ref = self.save_packet()
        self.ci = ci_logs

    def save_packet(self):
        ref = self.write_object(self.packet)
        self.pointer.write_text(
            canonical(
                dict(
                    schema_version=1,
                    artifact="ci-failure-log-pointer",
                    scope=self.scope,
                    log_object=ref,
                )
            )
            + "\n"
        )
        return ref

    def build(self, **kwargs):
        return CacheIndex.build(self.source, self.database, snapshot=self.snapshot, **kwargs)

    def query(self, query="failure", **kwargs):
        with CacheIndex(self.database) as index:
            return index.query(
                self.source,
                query,
                snapshot=self.snapshot,
                components=("ci_logs",),
                max_bytes=16000,
                **kwargs,
            )

    def test_native_search_retrieve_exact_raw_segments_and_frozen_pointers(self):
        before = evidence_bytes(self.root) | {"pointer": self.pointer.read_bytes()}
        report = self.build(components=("ci_logs",))
        self.assertEqual(report["coverage"]["ci_logs"]["captures"], 1)
        response = self.query()
        fragment = response["items"][0]["fragments"][0]
        self.assertEqual(fragment["component"], "ci_logs")
        self.assertEqual(fragment["excerpt"]["text"], self.log_text)
        self.assertEqual(fragment["excerpt"]["original_start_byte"], 100)
        self.assertEqual(fragment["locator"]["original_start_line"], 5)
        self.assertEqual(fragment["ci_log"]["attempt"], 2)
        self.assertEqual(fragment["ci_log"]["capture_snapshot_id"], self.snapshot)
        self.assertEqual(fragment["ci_log"]["provider"], "github-actions")
        self.assertEqual(fragment["excerpt"]["sha256"], sha256(self.log_text))
        self.assertTrue(self.query("workflow")["items"])
        colored = self.query("error")["items"][0]["fragments"][0]
        self.assertEqual(colored["excerpt"]["text"], self.log_text)
        self.assertEqual(colored["excerpt"]["original_start_byte"], 100)
        self.assertIn("observation outside freshness window", colored["problems"])
        self.assertTrue(self.query("Build native")["items"])
        with CacheIndex(self.database) as index:
            retrieved = index.retrieve(
                self.source,
                (fragment["unit_id"],),
                snapshot=self.snapshot,
                checkpoint=response["checkpoint"],
                fragment_bytes=10,
                max_bytes=16000,
            )
        part = retrieved["items"][0]["fragments"][0]
        self.assertLess(part["excerpt"]["end"], len(self.log_text.encode()))
        self.assertEqual(part["excerpt"]["original_end_byte"], 100 + part["excerpt"]["end"])
        self.assertIsNotNone(part["continuation"])
        self.assertEqual(before, evidence_bytes(self.root) | {"pointer": self.pointer.read_bytes()})
        self.pointer.write_text("later mutable pointer is not consulted")
        self.assertEqual(self.query()["items"][0]["fragments"][0]["object"], self.ref)
        self.pointer.unlink()
        self.assertTrue(self.query()["items"])

        # ANSI matching must also place a short excerpt around a distant colored error.
        import base64

        raw = ("pré🙂\n" * 80 + self.log_text).encode()
        self.packet["segments"][0].update(
            end_byte=100 + len(raw),
            end_line=86,
            content_base64=base64.b64encode(raw).decode(),
        )
        self.packet.update(observed_bytes=100 + len(raw), observed_lines=86)
        self.save_packet()
        self.build(replace=True, components=("ci_logs",))
        excerpt = self.query("error", fragment_bytes=64)["items"][0]["fragments"][0]["excerpt"]
        self.assertIn("error", excerpt["text"])
        self.assertGreater(excerpt["start"], 0)
        self.assertEqual(raw[excerpt["start"]:excerpt["end"]].decode(), excerpt["text"])
        self.assertEqual(excerpt["sha256"], sha256(excerpt["text"]))
        self.assertEqual(excerpt["original_start_byte"], 100 + excerpt["start"])

    def test_partial_non_utf8_unavailable_and_ordinary_component_isolation(self):
        import base64

        self.packet["segments"][0]["content_base64"] = base64.b64encode(
            b"\xff" * (len(self.log_text.encode()) - 1) + b"\n"
        ).decode()
        # Keep valid two-line native bounds while omitting this unsupported projection.
        raw = b"bad \xff\nerror \xfe\n"
        self.packet["segments"][0].update(
            content_base64=base64.b64encode(raw).decode(), end_byte=100 + len(raw)
        )
        self.save_packet()
        coverage = self.build(components=("ci_logs",))["coverage"]["ci_logs"]
        self.assertEqual(coverage["non_utf8_segments"], 1)
        self.assertEqual(coverage["partial"], 1)
        self.assertEqual(coverage["omission_count"], 1)
        self.assertFalse(self.query()["items"])
        self.assertTrue(self.query("workflow")["items"])
        self.packet["segments"] = []
        self.save_packet()
        self.assertEqual(
            self.build(replace=True, components=("ci_logs",))["coverage"]["ci_logs"]["unavailable"],
            1,
        )
        self.pointer.write_text("invalid pointer ignored by ordinary explicit scope")
        with patch.object(
            self.ci, "Selection", side_effect=AssertionError("CI discovery must not run")
        ):
            self.build(replace=True, components=("summary",))

    def test_pointer_drift_refuses_publication_and_source_corruption_fails(self):
        self.build(components=("ci_logs",))
        old = self.database.read_bytes()
        original = self.source.checkpoint

        def drift(**kwargs):
            self.pointer.write_bytes(self.pointer.read_bytes() + b" ")
            return original(**kwargs)

        with (
            patch.object(self.source, "checkpoint", side_effect=drift),
            self.assertRaisesRegex(ValueError, "association changed"),
        ):
            self.build(replace=True, components=("ci_logs",))
        self.assertEqual(self.database.read_bytes(), old)
        object_path = self.root / "objects" / (self.ref["sha256"] + ".json")
        object_path.write_bytes(object_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "checksum|size"):
            self.query()

    def test_packet_binding_and_segment_validation_and_limits(self):
        for field in ("job", "membership"):
            for attempt in (3, True, None, "2"):
                with self.subTest(field=field, attempt=attempt):
                    saved = self.packet[field]
                    self.packet[field] = dict(saved, run_attempt=attempt)
                    self.save_packet()
                    with self.assertRaisesRegex(ValueError, "run attempt"):
                        self.build(components=("ci_logs",))
                    self.packet[field] = saved
        self.packet["job"] = dict(self.packet["job"], run_attempt=2)
        self.packet["membership"] = dict(self.packet["membership"], run_attempt=2)
        for key, changed in (
            ("head_sha", "f" * 40),
            ("check_id", 99),
            ("attempt", 3),
            ("number", 4),
        ):
            with self.subTest(key=key):
                saved = self.packet[key]
                self.packet[key] = changed
                self.save_packet()
                with self.assertRaises(ValueError):
                    self.build(components=("ci_logs",))
                self.packet[key] = saved
        self.packet["check_source"]["index"] = True
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "source index"):
            self.build(components=("ci_logs",))
        self.packet["check_source"]["index"] = 1
        self.packet["segments"][0]["end_byte"] += 1
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "segment source ranges"):
            self.build(components=("ci_logs",))
        self.packet["segments"][0]["end_byte"] -= 1
        self.save_packet()
        self.build(components=("ci_logs",))
        with self.assertRaisesRegex(ValueError, "verification byte ceiling"):
            self.query(max_verify_bytes=self.ref["bytes"])
        response = self.query()
        self.assertGreater(response["diagnostics"]["verified_object_bytes"], self.ref["bytes"])
        self.assertEqual(response["diagnostics"]["verified_object_count"], 3)
        with self.assertRaisesRegex(ValueError, "unit ceiling"):
            self.build(replace=True, components=("ci_logs",), max_units=1)
        self.assertTrue(self.query()["items"])
        import base64

        raw = b"failure line\n" * 4000
        self.packet.update(
            observed_bytes=len(raw),
            observed_lines=4000,
            max_log_bytes=65536,
            segments=[
                dict(
                    start_line=1,
                    end_line=4000,
                    start_byte=0,
                    end_byte=len(raw),
                    last_line_complete=True,
                    content_base64=base64.b64encode(raw).decode(),
                )
            ],
        )
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "storage ceiling"):
            self.build(
                replace=True,
                components=("ci_logs",),
                max_index_bytes=65536,
                policy=ProjectionPolicy(max_bytes=256, overlap_bytes=0),
            )
        self.assertTrue(self.query()["items"])
        with CacheIndex(self.database) as index, self.assertRaisesRegex(ValueError, "byte budget"):
            index.query(
                self.source,
                "failure",
                snapshot=self.snapshot,
                components=("ci_logs",),
                max_bytes=4096,
            )
        checks_path = (
            self.root
            / "objects"
            / (self.record["components"]["checks"]["object"]["sha256"] + ".json")
        )
        checks_path.write_bytes(checks_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "verification"):
            self.query()

    def test_reused_capture_requires_exact_raw_check_and_suite(self):
        old_snapshot = self.snapshot
        updated = copy.deepcopy(self.record)
        # A metadata-only distinct snapshot with the same retained source projections.
        updated["components"]["checks"]["source"]["resource"] += "?observation=2"
        self.snapshot = publish(self.root, [updated])["snapshot_id"]
        self.scope["snapshot_id"] = self.snapshot
        self.pointer = self.root / "ci-logs" / (sha256(canonical(self.scope)) + ".json")
        self.save_packet()
        self.build(components=("ci_logs",))
        context = self.query()["items"][0]["fragments"][0]["ci_log"]
        self.assertEqual(context["capture_snapshot_id"], old_snapshot)
        self.assertEqual(context["selected_snapshot_id"], self.snapshot)
        self.assertEqual(self.query()["diagnostics"]["verified_object_count"], 3)
        different = copy.deepcopy(self.checks)
        different[0]["data"]["extra_raw_suite_field"] = "changed"
        updated["components"]["checks"]["object"] = self.write_object(different)
        self.snapshot = publish(self.root, [updated])["snapshot_id"]
        self.scope["snapshot_id"] = self.snapshot
        self.pointer = self.root / "ci-logs" / (sha256(canonical(self.scope)) + ".json")
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "reuse differs"):
            self.build(replace=True, components=("ci_logs",))

    def test_cli_selects_ci_logs_and_cap(self):
        arguments = [
            "cache-index",
            "--cache",
            str(self.root),
            "--snapshot",
            self.snapshot,
            "--db",
            str(self.database),
            "--component",
            "ci_logs",
            "--max-ci-logs",
            "1",
        ]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(arguments), 0)
        self.assertEqual(
            json.loads(output.getvalue())["coverage"]["ci_logs"]["maximum_captures"], 1
        )
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(
                main(
                    [
                        "cache-query",
                        "--cache",
                        str(self.root),
                        "--snapshot",
                        self.snapshot,
                        "--db",
                        str(self.database),
                        "--component",
                        "ci_logs",
                        "--query",
                        "failure",
                        "--max-bytes",
                        "16000",
                    ]
                ),
                0,
            )
        self.assertTrue(json.loads(output.getvalue())["items"])

    def test_exact_lookup_caps_missing_provider_and_foreign_packet_repository(self):
        additional = copy.deepcopy(self.checks[1])
        additional["data"]["id"] = 42
        all_checks = self.checks + [additional]
        record = copy.deepcopy(self.record)
        record["components"]["checks"].update(
            object=self.write_object(all_checks), received_count=3, expected_count=3
        )
        self.snapshot = publish(self.root, [record])["snapshot_id"]
        self.scope["snapshot_id"] = self.snapshot
        self.pointer = self.root / "ci-logs" / (sha256(canonical(self.scope)) + ".json")
        self.save_packet()
        coverage = self.build(components=("ci_logs",), max_ci_logs=1)["coverage"]["ci_logs"]
        self.assertEqual(coverage["captures"], 1)
        self.assertEqual(coverage["capped_checks"], 1)
        coverage = self.build(replace=True, components=("ci_logs",))["coverage"]["ci_logs"]
        self.assertEqual(coverage["missing"], 1)
        additional["data"]["app"]["slug"] = "another-provider"
        record["components"]["checks"]["object"] = self.write_object(self.checks + [additional])
        self.snapshot = publish(self.root, [record])["snapshot_id"]
        self.scope["snapshot_id"] = self.snapshot
        self.pointer = self.root / "ci-logs" / (sha256(canonical(self.scope)) + ".json")
        self.save_packet()
        coverage = self.build(replace=True, components=("ci_logs",))["coverage"]["ci_logs"]
        self.assertEqual(coverage["unsupported_providers"], 1)
        self.packet["repository"] = {**self.repo, "database_id": 999}
        self.save_packet()
        with self.assertRaisesRegex(ValueError, "repository"):
            self.build(replace=True, components=("ci_logs",))

    def test_corpus_selection_and_omitted_segment_progressive_boundaries(self):
        import base64

        raw = b"later failure window\n"
        self.packet["segments"].append(
            dict(
                start_line=15,
                end_line=15,
                start_byte=300,
                end_byte=300 + len(raw),
                last_line_complete=True,
                content_base64=base64.b64encode(raw).decode(),
            )
        )
        self.save_packet()
        plan = sealed(
            dict(
                schema_version=1,
                artifact="evidence-corpus-plan",
                repository=self.repo,
                inventory_snapshot=self.snapshot,
                scope="open-prs",
                profile="pr-checks",
                max_age=86400,
                members=[self.identity],
            )
        )
        corpus = plan["checksum"]
        (self.root / "corpora" / (corpus + ".plan.json")).write_text(canonical(plan) + "\n")
        state = sealed(
            dict(
                schema_version=1,
                artifact="evidence-corpus-state",
                plan_id=corpus,
                updated_at=END,
                status="finished",
                last_run=None,
                items={
                    "pr:2": dict(
                        snapshot_id=self.snapshot, outcome="complete", attempts=1, error=None
                    )
                },
            )
        )
        (self.root / "corpora" / (corpus + ".state.json")).write_text(canonical(state) + "\n")
        CacheIndex.build(self.source, self.database, corpus=corpus, components=("ci_logs",))
        with CacheIndex(self.database) as index:
            response = index.query(
                self.source, "second", corpus=corpus, components=("ci_logs",), max_bytes=16000
            )
            fragment = response["items"][0]["fragments"][0]
            result = index.retrieve(
                self.source,
                (fragment["unit_id"],),
                checkpoint=response["checkpoint"],
                corpus=corpus,
                fragment_bytes=65536,
                max_bytes=16000,
            )
        retrieved = result["items"][0]["fragments"][0]
        self.assertEqual(retrieved["excerpt"]["text"], self.log_text)
        self.assertIsNone(retrieved["continuation"])
        self.assertNotIn("later failure", retrieved["excerpt"]["text"])
        self.assertEqual(retrieved["locator"]["boundary_end"], len(self.log_text.encode()))
        self.assertLessEqual(len(encode_response(result).encode()), 16000)
        self.assertEqual(result["budget"]["used_bytes"], len(encode_response(result).encode()))

    def test_corrupt_derived_coverage_cannot_upgrade_partial_capture(self):
        self.build(components=("ci_logs",))
        with sqlite3.connect(self.database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT u.id,u.data,u.binding_id,b.data AS binding FROM units u JOIN bindings b ON b.id=u.binding_id WHERE u.field='log'"
            ).fetchone()
            binding = json.loads(row["binding"])
            binding["coverage"]["status"] = "complete"
            binding_text = canonical(binding)
            binding_id = sha256(binding_text)
            connection.execute("INSERT INTO bindings VALUES(?,?)", (binding_id, binding_text))
            identifier = sha256(canonical({**binding, **json.loads(row["data"])}))
            connection.execute(
                "UPDATE units SET id=?,binding_id=? WHERE id=?", (identifier, binding_id, row["id"])
            )
        with self.assertRaisesRegex(ValueError, "CI source binding"):
            self.query()
