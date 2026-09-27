"""Run a disposable synthetic review/cost trial; no cache selection or IDs to copy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=int, default=1, help="eight synthetic items per group")
    parser.add_argument("--repeat", type=int, default=1, help="comment length multiplier")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--checkout",
        type=Path,
        help="trusted triage-o-mator checkout with the bridge applied; also measure CLI startup and retrieval",
    )
    args = parser.parse_args()
    if args.groups < 1 or args.repeat < 1:
        parser.error("groups/repeat must be positive")
    spec = importlib.util.spec_from_file_location(
        "review_fixture", ROOT / "examples/triage_review_cases.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with tempfile.TemporaryDirectory(prefix="reposition-review-costs-") as directory:
        temp = Path(directory)
        install = temp / "install"
        cache = install / "data/example/triage/cache" if args.checkout else temp / "cache"
        manifest = fixture.make_review_cache(cache, groups=args.groups, repeat=args.repeat)
        cases = json.loads((ROOT / "examples/triage-review-cases.json").read_text())
        if args.groups > 1:
            cases["label_authority"] = (
                "Repeated synthetic causes for resource costs only; no ranking quality labels"
            )
            for case in cases["cases"]:
                case.pop("expected_items", None)
        case_file = temp / "cases.json"
        case_file.write_text(json.dumps(cases))
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "benchmarks/cache_retrieval.py"),
                "--cache",
                str(cache),
                "--snapshot",
                manifest["snapshot_id"],
                "--cases",
                str(case_file),
                "--max-bytes",
                "12000",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        result = json.loads(completed.stdout)
        if args.checkout:
            from reposition import TriageCache

            (install / "config").mkdir()
            (install / "config/repo").write_text("example/triage\n")
            (install / ".triage-install.json").write_text("{}\n")
            fake_bin = temp / "fake-bin"
            fake_bin.mkdir()
            (fake_bin / "gh").write_text(
                "#!/bin/sh\nprintf 'unexpected call\\n' >> \"$REPOSITION_CALLS\"\nexit 99\n"
            )
            (fake_bin / "gh").chmod(0o755)
            environment = {
                **os.environ,
                "TRIAGE_ROOT": str(install),
                "PYTHONPATH": str(args.checkout.resolve() / "bin"),
                "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                "REPOSITION_CALLS": str(temp / "unexpected-github-calls"),
            }
            database = TriageCache(cache).index_path(snapshot=manifest["snapshot_id"])

            def bridge(arguments):
                start = perf_counter()
                response = subprocess.run(
                    [sys.executable, str(args.checkout.resolve() / "bin/cache"), *arguments],
                    cwd=install,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60,
                )
                return json.loads(response.stdout), (perf_counter() - start) * 1000

            scope = ["--snapshot", manifest["snapshot_id"], "--db", str(database)]
            for case, measured in zip(cases["cases"], result["cases"], strict=True):
                options = [
                    "query",
                    *scope,
                    "--query",
                    case["query"],
                    "--max-bytes",
                    "12000",
                    "--max-snippets-per-item",
                    str(case.get("max_snippets_per_item", 2)),
                ]
                for component in case.get("components", ["summary", "comments", "files", "diff"]):
                    options += ["--component", component]
                durations = []
                for _ in range(3):
                    response, elapsed = bridge(options)
                    ids = [
                        f"{item['identity']['kind']}:{item['identity']['number']}"
                        for item in response["items"]
                    ]
                    if ids != measured["returned_items"]:
                        raise RuntimeError("bridge ranking differs from the measured engine view")
                    durations.append(elapsed)
                measured["bridge_query_process_ms"] = durations
                measured["bridge_query_process_median_ms"] = statistics.median(durations)
                if response["items"]:
                    fragment = response["items"][0]["fragments"][0]
                    retrieved, elapsed = bridge(
                        [
                            "retrieve",
                            *scope,
                            "--unit",
                            fragment["unit_id"],
                            "--checkpoint",
                            response["checkpoint"],
                            "--max-bytes",
                            "12000",
                        ]
                    )
                    if not retrieved["items"][0]["fragments"][0]["verified"]:
                        raise RuntimeError("bridge retrieval returned unverified evidence")
                    measured["bridge_retrieve_process_ms"] = elapsed
            result["bridge_cost_policy"] = (
                "Separate Python process for each call, including startup and source verification; OS caches not cleared. Retrieval costs read one returned fragment. Peak RSS measures the separate engine benchmark process, not CLI subprocesses."
            )
            if (temp / "unexpected-github-calls").exists():
                raise RuntimeError("offline bridge attempted a GitHub call")
    result["fixture"] = {
        "synthetic": True,
        "members": args.groups * 8,
        "groups": args.groups,
        "repeat": args.repeat,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
