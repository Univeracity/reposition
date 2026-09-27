"""Install a built wheel without dependencies and exercise it outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def smoke(wheel: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="reposition-wheel-") as folder:
        temporary = Path(folder)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        subprocess.run([sys.executable, "-m", "venv", str(temporary / "env")], check=True)
        python = temporary / "env/bin/python"
        if os.name == "nt":
            python = temporary / "env/Scripts/python.exe"

        def run(*args, expected=0):
            process = subprocess.run(
                [str(python), *args], cwd=temporary, env=environment, capture_output=True, text=True
            )
            if process.returncode != expected:
                raise RuntimeError(f"wheel smoke command failed: {args}\n{process.stderr}")
            return process.stdout, process.stderr

        run("-m", "pip", "install", "--no-index", "--no-deps", str(wheel.resolve()))
        module_info, _ = run(
            "-c",
            "import importlib.util,json,reposition; print(json.dumps({'file':reposition.__file__,'optional_present':any(importlib.util.find_spec(n) is not None for n in ['tiktoken','numpy','scipy','sklearn','vyral_runtime'])}))",
        )
        module_info = json.loads(module_info)
        if module_info["optional_present"] or not Path(module_info["file"]).is_relative_to(
            temporary
        ):
            raise RuntimeError(
                "smoke environment is not isolated from source or optional dependencies"
            )
        shutil.copyfile(root / "examples/github-cache.json", temporary / "cache.json")
        run("-m", "reposition", "--version")
        demo, _ = run("-m", "reposition", "demo")
        if "https://github.com/example/packages/issues/40" not in demo:
            raise RuntimeError("wheel demo has no bundled sample results")
        cache_demo, _ = run("-m", "reposition", "demo", "--cache")
        if "Verified source" not in cache_demo or "larger source window" not in cache_demo:
            raise RuntimeError("wheel immutable-cache demo failed")
        indexed, _ = run(
            "-m",
            "reposition",
            "index",
            "cache.json",
            "--format",
            "github",
            "--repo",
            "example/packages",
        )
        if json.loads(indexed)["record_count"] != 7:
            raise RuntimeError("unexpected wheel import result")
        searched, _ = run("-m", "reposition", "search", "archive bytes digest", "--json")
        searched = json.loads(searched)
        if (
            not searched["search"]["hits"]
            or searched["evidence"]["count"] > searched["evidence"]["budget"]
        ):
            raise RuntimeError("wheel search/evidence failed")
        related, _ = run("-m", "reposition", "related", "40")
        if json.loads(related)["edges"][0]["source_item"] != "41":
            raise RuntimeError("wheel reference navigation failed")
        run("-m", "reposition", "export")
        generated, _ = run(str(root / "examples/triage_cache.py"), "immutable-cache")
        scope = json.loads(generated)
        common = ("--cache", "immutable-cache", "--corpus", scope["corpus"])
        run("-m", "reposition", "cache-index", *common)
        discovery, _ = run(
            "-m",
            "reposition",
            "cache-query",
            *common,
            "--query",
            "café suspend",
            "--max-bytes",
            "6000",
        )
        result = json.loads(discovery)
        if len(discovery.encode("utf-8")) > 6000 or not result["items"]:
            raise RuntimeError("wheel immutable-cache query/budget failed")
        fragment = result["items"][0]["fragments"][0]
        resolved, _ = run(
            "-m",
            "reposition",
            "cache-retrieve",
            *common,
            "--unit",
            fragment["unit_id"],
            "--checkpoint",
            result["checkpoint"],
            "--max-bytes",
            "6000",
        )
        if not json.loads(resolved)["items"][0]["fragments"][0]["verified"]:
            raise RuntimeError("wheel immutable-cache source resolution failed")
        _, error = run("-m", "reposition", "search", "archive", "--tokens", "1024", expected=2)
        if "[tokens]" not in error:
            raise RuntimeError("missing tokens extra has no actionable message")
        _, error = run("-m", "reposition", "search", "archive", "--method", "tfidf", expected=2)
        if "[tfidf]" not in error:
            raise RuntimeError("missing tfidf extra has no actionable message")
        tests, errors = run("-m", "unittest", "discover", "-s", str(root / "tests"), "-v")
        skipped = re.search(r"OK \(skipped=(\d+)\)", tests + errors)
        if skipped is None:
            raise RuntimeError(
                "dependency-free installed-wheel tests did not pass with optional checks skipped"
            )
    return {
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "dependency_free": True,
        "installed_module_outside_checkout": True,
        "cli_self_contained_demos": True,
        "cli_import_search_relations_export": True,
        "cli_immutable_cache_index_query_retrieve": True,
        "missing_extras_actionable": True,
        "core_tests_passed": True,
        "optional_tests_skipped": int(skipped[1]),
        "passed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = smoke(args.wheel)
    serialized = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
