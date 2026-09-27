"""Replay extracted search against external frozen component/TF-IDF receipts.

Inputs: corpus-root/{omarchy,omarchy-pkgs}/{documents.json,manifest.json} and
receipts-root/{omarchy,omarchy-pkgs}/results.json from the five-arm comparison.
cases-root/{omarchy,omarchy-pkgs}/cases.json supplies the original query filters.
No runtime, model, private project path, or network is needed.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from reposition import Index, load_snapshot, render
from reposition.models import sha256


def file_digest(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--receipts-root", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    receipt = {
        "schema": "reposition.extraction.v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            **{p: importlib.metadata.version(p) for p in ("tiktoken", "scikit-learn", "scipy")},
        },
        "implementation_digests": {
            str(p.relative_to(root)): file_digest(p)
            for p in sorted((root / "src/reposition").glob("*.py"))
        },
        "repositories": {},
        "limits": "saved provisional labels; search parity only, not new human qualification; new general renderer audited independently",
    }
    for repository in ("omarchy", "omarchy-pkgs"):
        source = args.corpus_root / repository
        old_path = args.receipts_root / repository / "results.json"
        old = json.loads(old_path.read_text())
        cases_path = args.cases_root / repository / "cases.json"
        case_options = {c["id"]: c for c in json.loads(cases_path.read_text())}
        snapshot = load_snapshot(
            source / "documents.json",
            format="components",
            repository="omacom/" + repository,
            manifest_path=source / "manifest.json",
        )
        records = {r.id: r for r in snapshot.records}
        repo_receipt = {
            "records": len(records),
            "snapshot_digest": snapshot.digest,
            "input_digests": {
                "documents.json": file_digest(source / "documents.json"),
                "manifest.json": file_digest(source / "manifest.json"),
                "cases.json": file_digest(cases_path),
                "results.json": file_digest(old_path),
            },
            "methods": {},
        }
        with Index() as index:
            index.import_snapshot(snapshot)
            for method in ("fts5", "tfidf"):
                checked = excerpts = outputs = 0
                for case in old["variants"][method]["cases"]:
                    options = case_options[case["caseId"]]
                    if options["query"] != case["query"]:
                        raise AssertionError("query differs between case definitions and receipts")
                    component = options.get("component")
                    result = index.search(case["query"], method=method, component=component)
                    ids = [h.record.id for h in result.hits]
                    if ids != case["ids"]:
                        raise AssertionError(
                            f"{repository}/{method}/{case['caseId']}: extracted ranking differs"
                        )
                    checked += 1
                    for budget in (1024, 2048):
                        evidence = render(result, budget=budget, unit="tokens")
                        import tiktoken

                        assert evidence.count == len(
                            tiktoken.get_encoding("o200k_base").encode(
                                evidence.text, disallowed_special=()
                            )
                        )
                        assert evidence.count <= budget
                        assert evidence.sha256 == sha256(evidence.text)
                        for excerpt in evidence.excerpts:
                            record = records[excerpt["record_id"]]
                            assert (
                                excerpt["text"]
                                == record.text[excerpt["record_start"] : excerpt["record_end"]]
                            )
                            assert (
                                excerpt["source_start"]
                                == record.source_start + excerpt["record_start"]
                            )
                            assert (
                                excerpt["source_end"] == record.source_start + excerpt["record_end"]
                            )
                            assert excerpt["source_revision"] == record.source_revision
                            assert excerpt["uri"] == record.uri
                            assert excerpt["sha256"] == sha256(excerpt["text"])
                            excerpts += 1
                        outputs += 1
                repo_receipt["methods"][method] = {
                    "ranking_parity_queries": checked,
                    "complete_outputs": outputs,
                    "excerpt_checks": excerpts,
                    "passed": True,
                }
        receipt["repositories"][repository] = repo_receipt
    receipt["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": True, "repositories": receipt["repositories"]}, indent=2))


if __name__ == "__main__":
    main()
