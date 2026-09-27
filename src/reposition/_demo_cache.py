"""Create synthetic triage-o-mator v1 evidence for demos and size measurements."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reposition import _triage_contract as contract
from reposition.models import canonical, sha256

START = "2026-09-27T12:00:00Z"
FETCHED = "2026-09-27T12:01:00Z"
END = "2026-09-27T12:02:00Z"
REPOSITORY = contract.repository("example/triage", database_id=42, node_id="R_synthetic")


def sealed(value):
    return {**value, "checksum": sha256(canonical(value))}


def write_object(root, value, format="json"):
    text = canonical(value) + "\n" if format == "json" else value
    ref = contract.artifact_ref(text, format)
    (root / "objects" / contract.object_name(ref)).write_text(text, encoding="utf-8")
    return ref


def publish(root, records, requested=None):
    manifest = contract.seal_snapshot(
        {
            "schema_version": 1,
            "artifact": "evidence-snapshot",
            "repository": REPOSITORY,
            "started_at": START,
            "completed_at": END,
            "requested_components": sorted(requested or records[0]["components"]),
            "items": records,
        }
    )
    (root / "snapshots" / (manifest["snapshot_id"] + ".json")).write_text(
        canonical(manifest) + "\n", encoding="utf-8"
    )
    return manifest


def make_cache(root: Path, count=4, repeat=1):
    root.mkdir(parents=True, exist_ok=True)
    (root / "objects").mkdir(exist_ok=True)
    (root / "snapshots").mkdir(exist_ok=True)
    (root / "corpora").mkdir(exist_ok=True)
    (root / "cache.json").write_text(
        canonical({"schema_version": 1, "artifact": "evidence-cache", "repository": REPOSITORY})
        + "\n",
        encoding="utf-8",
    )
    records = []
    for n in range(1, count + 1):
        kind = "pr" if n % 2 == 0 else "issue"
        identity = {
            "kind": kind,
            "number": n,
            "database_id": 1000 + n,
            "node_id": f"{kind}_synthetic_{n}",
        }
        revision = {
            "updated_at": START,
            "base_sha": "a" * 40 if kind == "pr" else None,
            "head_sha": "b" * 40 if kind == "pr" else None,
        }
        uri = f"https://github.com/example/triage/{'pull' if kind == 'pr' else 'issues'}/{n}"
        summary = {
            "number": n,
            "kind": kind,
            "state": "open",
            "source_state": "open",
            "title": f"Terminal suspend example {n}",
            "body": "A terminal fails after suspend. Different causes need review.",
            "html_url": uri,
            "user": {"login": "demo"},
            "labels": [{"name": "terminal"}, {"name": "synthetic"}],
            "inventory": {
                "artifact": "inventory-observation",
                "schema_version": 1,
                "repository": REPOSITORY,
                "started_at": START,
                "completed_at": END,
                "endpoint": "repos/example/triage/issues?state=open&per_page=100",
                "pages": 1,
                "pagination_complete": True,
                "mode": "full",
                "since": None,
                "raw_sha256": "0" * 64,
            },
        }
        patch = "@@ -1 +1 @@\n-old\n+resume terminal café 🙂\n"
        diff = (
            "diff --git a/src/terminal.py b/src/terminal.py\n--- a/src/terminal.py\n+++ b/src/terminal.py\n"
            + patch
        )
        payloads = {
            "summary": summary,
            "comments": [
                {
                    "id": n * 100 + i,
                    "body": ("Resume terminal after suspend café 🙂. " * repeat) + f"comment {i}",
                    "html_url": uri + f"#issuecomment-{n * 100 + i}",
                }
                for i in range(2)
            ],
            "files": [{"filename": "src/terminal.py", "status": "modified", "patch": patch}],
            "diff": diff,
            "reviews": [
                {
                    "id": n * 1000,
                    "body": "Alternative terminal fix needs review.",
                    "state": "COMMENTED",
                }
            ],
            "review_comments": [
                {
                    "id": n * 1000 + 1,
                    "body": "This resume path may affect another cause.",
                    "path": "src/terminal.py",
                    "diff_hunk": patch,
                }
            ],
            "checks": [
                {
                    "kind": "check_run",
                    "repository": REPOSITORY,
                    "head_sha": "b" * 40,
                    "fetched_at": FETCHED,
                    "resource": "/synthetic/check-runs",
                    "data": {
                        "id": n,
                        "name": "terminal-tests",
                        "status": "completed",
                        "conclusion": "success",
                        "output": {
                            "title": "Resume checks",
                            "summary": "Synthetic tests pass",
                            "text": "No live validation implied",
                        },
                    },
                }
            ],
            "closing_issues": [
                {
                    "repository": REPOSITORY,
                    "identity": {
                        "kind": "issue",
                        "number": 1,
                        "database_id": None,
                        "node_id": "issue_synthetic_1",
                    },
                    "url": "https://github.com/example/triage/issues/1",
                    "state": "open",
                    "updated_at": START,
                }
            ],
            "timeline": [
                {"id": n, "event": "commented", "body": "A suspend observation, not approval."}
            ],
        }
        components = {}
        for name, value in payloads.items():
            if kind == "issue" and name in contract.PR_ONLY:
                components[name] = {
                    "status": "not_applicable",
                    "fetched_at": None,
                    "source": None,
                    "revision": revision,
                    "expected_count": None,
                    "received_count": None,
                    "pagination_complete": None,
                    "truncated": False,
                    "error": None,
                    "object": None,
                }
                continue
            ref = write_object(root, value, "diff" if name == "diff" else "json")
            collection = name in contract.COLLECTIONS
            components[name] = {
                "status": "complete",
                "fetched_at": FETCHED,
                "source": {
                    "transport": "rest",
                    "resource": f"/repos/example/triage/issues/{n}/{name}",
                },
                "revision": revision,
                "expected_count": len(value) if collection else None,
                "received_count": len(value) if collection else None,
                "pagination_complete": True if collection else None,
                "truncated": False,
                "error": None,
                "object": ref,
            }
        records.append({"identity": identity, "revision": revision, "components": components})
    manifest = publish(root, records)
    plan = sealed(
        {
            "artifact": "evidence-corpus-plan",
            "schema_version": 1,
            "repository": REPOSITORY,
            "inventory_snapshot": manifest["snapshot_id"],
            "scope": "open-items",
            "profile": "backlog",
            "max_age": 86400,
            "members": sorted(
                (r["identity"] for r in records), key=lambda r: (r["kind"], r["number"])
            ),
        }
    )
    corpus = plan["checksum"]
    (root / "corpora" / (corpus + ".plan.json")).write_text(
        canonical(plan) + "\n", encoding="utf-8"
    )
    state = sealed(
        {
            "artifact": "evidence-corpus-state",
            "schema_version": 1,
            "plan_id": corpus,
            "updated_at": END,
            "status": "finished",
            "items": {
                f"{r['identity']['kind']}:{r['identity']['number']}": {
                    "attempts": 1,
                    "snapshot_id": manifest["snapshot_id"],
                    "outcome": "complete",
                    "error": None,
                }
                for r in records
            },
            "last_run": None,
        }
    )
    (root / "corpora" / (corpus + ".state.json")).write_text(
        canonical(state) + "\n", encoding="utf-8"
    )
    return manifest, corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--members", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.output.exists() or args.members < 1 or args.repeat < 1:
        parser.error("use a new output directory and positive members/repeat counts")
    manifest, corpus = make_cache(args.output, args.members, args.repeat)
    print(
        json.dumps(
            {"cache": str(args.output), "snapshot": manifest["snapshot_id"], "corpus": corpus}
        )
    )


if __name__ == "__main__":
    main()
