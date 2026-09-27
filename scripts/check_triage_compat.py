"""Exercise Reposition through a supplied triage-o-mator checkout, offline and synthetic."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(checkout):
    with tempfile.TemporaryDirectory(prefix="reposition-triage-") as directory:
        temp = Path(directory)
        install = temp / "install"
        (install / "config").mkdir(parents=True)
        (install / "config/repo").write_text("example/triage\n")
        (install / ".triage-install.json").write_text("{}\n")
        spec = importlib.util.spec_from_file_location(
            "synthetic_cache_fixture", ROOT / "examples/triage_cache.py"
        )
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        raw = temp / "synthetic-input"
        manifest, _ = fixture.make_cache(raw)
        environment = {
            **os.environ,
            "TRIAGE_ROOT": str(install),
            "PYTHONPATH": str(checkout / "bin"),
        }
        fake_bin = temp / "fake-bin"
        fake_bin.mkdir()
        calls = temp / "unexpected-github-calls"
        (fake_bin / "gh").write_text(
            "#!/bin/sh\nprintf 'unexpected call\\n' >> \"$REPOSITION_CALLS\"\nexit 99\n"
        )
        (fake_bin / "gh").chmod(0o755)
        environment.update(
            PATH=str(fake_bin) + os.pathsep + environment["PATH"], REPOSITION_CALLS=str(calls)
        )

        def run(arguments, expected=0, no_site=False):
            result = subprocess.run(
                [sys.executable, *(["-S"] if no_site else []), *arguments],
                cwd=install,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != expected:
                raise RuntimeError(
                    f"offline compatibility command failed ({result.returncode}): {result.stderr}"
                )
            return result

        publisher = """
import json,sys
from pathlib import Path
from _cache import EvidenceCache
from _corpus import create,path
from _evidence import object_name,repository,seal_snapshot
from _jobs import write_record
raw=Path(sys.argv[1]); identifier=sys.argv[2]
manifest=json.loads((raw/'snapshots'/(identifier+'.json')).read_text())
cache=EvidenceCache(repository('example/triage'))
cache.initialize(); cache.bind_repository(manifest['repository'])
manifest=seal_snapshot({k:v for k,v in manifest.items() if k!='snapshot_id'})
payloads={object_name(d['object']):(raw/'objects'/object_name(d['object'])).read_text() for r in manifest['items'] for d in r['components'].values() if d['object']}
cache.publish(manifest,payloads)
corpus=create(cache,manifest['snapshot_id'],scope='open-items',profile='backlog')['corpus_id']
state=json.loads((raw/'corpora'/(corpus+'.state.json')).read_text());state.pop('checksum')
write_record(cache,path(cache,corpus,'.state.json'),state)
print(json.dumps({'snapshot':manifest['snapshot_id'],'corpus':corpus,'cache':str(cache.root)}))
"""
        published = json.loads(run(["-c", publisher, str(raw), manifest["snapshot_id"]]).stdout)
        cache = Path(published["cache"])
        before = {
            str(p.relative_to(cache)): p.read_bytes() for p in cache.rglob("*") if p.is_file()
        }
        command = [str(checkout / "bin/cache")]
        help_result = run([*command, "--help"], no_site=True)
        if "search-index" not in help_result.stdout or "query" not in help_result.stdout:
            raise RuntimeError("bridge commands missing from root help")
        legacy = json.loads(
            run(
                [
                    *command,
                    "search",
                    "--corpus",
                    published["corpus"],
                    "--query",
                    "Resume",
                    "--component",
                    "comments",
                    "--limit",
                    "4",
                ],
                no_site=True,
            ).stdout
        )
        if legacy["matched_items"] != 4:
            raise RuntimeError("legacy literal search changed")
        missing = run(
            [*command, "query", "--corpus", published["corpus"], "--query", "terminal"],
            expected=2,
            no_site=True,
        )
        if "Reposition 0.2" not in missing.stderr:
            raise RuntimeError("optional dependency error is not actionable")
        build = json.loads(run([*command, "search-index", "--corpus", published["corpus"]]).stdout)
        from reposition import TriageCache
        from reposition.models import canonical, sha256

        database = TriageCache(cache).index_path(corpus=published["corpus"])
        info_command = [*command, "search-info", "--db", str(database)]
        info = json.loads(run([*info_command, "--corpus", published["corpus"]]).stdout)
        if info["source_checkpoint_verified"] or info["source_payloads_verified"]:
            raise RuntimeError("metadata inspection falsely claims source verification")
        run([*info_command, "--snapshot", published["snapshot"]], expected=2)
        foreign = temp / "foreign.sqlite"
        for changes in (
            {"host": "other.example"},
            {"full_name": "other/repository"},
            {"database_id": 999},
            {"node_id": "R_other"},
            {"database_id": None},
        ):
            shutil.copyfile(database, foreign)
            with sqlite3.connect(foreign) as connection:
                metadata = json.loads(connection.execute("SELECT data FROM manifest").fetchone()[0])
                metadata.pop("checksum")
                metadata["repository"].update(changes)
                metadata["checksum"] = sha256(canonical(metadata))
                connection.execute("UPDATE manifest SET data=?", (canonical(metadata),))
            run([*command, "search-info", "--db", str(foreign)], expected=2)
        response = run(
            [
                *command,
                "query",
                "--corpus",
                published["corpus"],
                "--component",
                "comments",
                "--component",
                "summary",
                "--query",
                "terminal suspend",
                "--max-bytes",
                "6000",
                "--max-snippets-per-item",
                "1",
            ]
        )
        query = json.loads(response.stdout)
        if (
            not query["items"]
            or len(response.stdout.encode()) > 6000
            or query["budget"]["used_bytes"] != len(response.stdout.encode())
        ):
            raise RuntimeError("bridge query did not honor complete response budget")
        fragment = query["items"][0]["fragments"][0]
        retrieved = json.loads(
            run(
                [
                    *command,
                    "retrieve",
                    "--corpus",
                    published["corpus"],
                    "--unit",
                    fragment["unit_id"],
                    "--checkpoint",
                    query["checkpoint"],
                    "--max-bytes",
                    "6000",
                    "--byte-offset",
                    "0",
                ]
            ).stdout
        )
        if not retrieved["items"][0]["fragments"][0]["verified"]:
            raise RuntimeError("bridge source retrieval is unverified")
        run(
            [
                *command,
                "--expected-repo",
                "different/repository",
                "query",
                "--corpus",
                published["corpus"],
                "--query",
                "terminal",
            ],
            expected=2,
        )
        legacy_again = json.loads(
            run(
                [
                    *command,
                    "search",
                    "--corpus",
                    published["corpus"],
                    "--query",
                    "Resume",
                    "--component",
                    "comments",
                    "--limit",
                    "4",
                ],
                no_site=True,
            ).stdout
        )
        if legacy_again != legacy:
            raise RuntimeError("legacy literal search changed after using the bridge")
        run(
            [
                *command,
                "query",
                "--cache",
                str(raw),
                "--corpus",
                published["corpus"],
                "--query",
                "terminal",
            ],
            expected=2,
        )
        after = {str(p.relative_to(cache)): p.read_bytes() for p in cache.rglob("*") if p.is_file()}
        if before != after:
            raise RuntimeError("retrieval changed authoritative cache artifacts")
        if calls.exists():
            raise RuntimeError("offline retrieval attempted a GitHub call")
        return {
            "schema": "reposition.triage-compatibility.v1",
            "passed": True,
            "upstream_published_objects_and_corpus": True,
            "legacy_search_without_reposition": True,
            "missing_optional_package_actionable": True,
            "install_identity_and_cache_override_guards": True,
            "search_info_identity_and_scope_guards": True,
            "search_info_is_metadata_only": True,
            "legacy_search_before_and_after_bridge": True,
            "query_and_retrieve_verified": True,
            "complete_response_max_bytes": 6000,
            "actual_query_bytes": len(response.stdout.encode()),
            "source_cache_bytes_unchanged": True,
            "index_units": build["units"],
            "index_bytes": build["index_bytes"],
            "requests": 0,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout",
        type=Path,
        required=True,
        help="trusted checkout with the proposed cache bridge applied",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = check(args.checkout.resolve())
    serialized = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
