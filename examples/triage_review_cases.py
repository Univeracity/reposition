"""Generate synthetic similar-symptom cases with explicitly different causes."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from reposition._demo_cache import make_cache, publish, write_object

SCENARIOS = (
    (
        "Terminal blank after suspend",
        "GPU reset",
        "Recreating the renderer restores output; the session socket remains connected.",
        "src/resume.py",
        "recreate_renderer()",
    ),
    (
        "Terminal blank after suspend",
        "stale session socket",
        "The renderer remains healthy; reopening the disconnected session socket restores output.",
        "src/resume.py",
        "reopen_session_socket()",
    ),
    (
        "Package checksum mismatch",
        "stale local cache",
        "The remote archive matches the pinned digest; removing the stale local cached archive resolves this case.",
        "src/packages.py",
        "invalidate_local_archive()",
    ),
    (
        "Package checksum mismatch",
        "upstream archive replaced",
        "A fresh download still differs from the pinned digest; the upstream archive was replaced. Cache eviction cannot fix this case.",
        "src/packages.py",
        "reject_changed_upstream_archive()",
    ),
)


def make_review_cache(root: Path, *, groups=1, repeat=1):
    if root.exists() or groups < 1 or repeat < 1:
        raise ValueError("use a new fixture path and positive groups/repeat")
    root.mkdir(parents=True)
    for name in ("objects", "snapshots", "corpora"):
        (root / name).mkdir()
    with tempfile.TemporaryDirectory(prefix="reposition-review-source-") as directory:
        original = Path(directory) / "cache"
        manifest, _ = make_cache(original, count=groups * 8)
        (root / "cache.json").write_bytes((original / "cache.json").read_bytes())
        records = manifest["items"]
        for row in records:
            number = row["identity"]["number"]
            symptom, cause, evidence, filename, fix = SCENARIOS[((number - 1) % 8) // 2]
            group = (number - 1) // 8 + 1
            context = f"Synthetic case group {group}. {symptom}. Cause: {cause}. {evidence}"
            for component, descriptor in row["components"].items():
                if descriptor["object"] is None:
                    continue
                ref = descriptor["object"]
                text = (
                    original
                    / "objects"
                    / (ref["sha256"] + (".diff" if component == "diff" else ".json"))
                ).read_text()
                value = text if component == "diff" else json.loads(text)
                patch = f"@@ -1 +1 @@\n-old_resume_or_download()\n+{fix}\n"
                if component == "summary":
                    value.update(title=f"{symptom}: {cause} (case {group})", body=context)
                    if row["identity"]["kind"] == "pr":
                        value["body"] += (
                            f" Proposed for #{number - 1}; this is a source claim, not approval."
                        )
                elif component == "comments":
                    for comment in value:
                        comment["body"] = (context + " ") * repeat
                elif component == "files":
                    value = [{"filename": filename, "status": "modified", "patch": patch}]
                elif component == "diff":
                    value = (
                        f"diff --git a/{filename} b/{filename}\n--- a/{filename}\n+++ b/{filename}\n"
                        + patch
                    )
                elif component == "reviews":
                    value[0]["body"] = context + " Proposed change still requires review."
                elif component == "review_comments":
                    value[0].update(body=context, path=filename, diff_hunk=patch)
                elif component == "closing_issues":
                    value[0]["identity"].update(
                        number=number - 1, node_id=f"issue_synthetic_{number - 1}"
                    )
                    value[0]["url"] = f"https://github.com/example/triage/issues/{number - 1}"
                descriptor["object"] = write_object(root, value, ref["format"])
        return publish(root, records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--groups", type=int, default=1, help="eight items per group")
    parser.add_argument("--repeat", type=int, default=1, help="comment length multiplier")
    args = parser.parse_args()
    manifest = make_review_cache(args.output, groups=args.groups, repeat=args.repeat)
    print(
        json.dumps(
            {
                "snapshot": manifest["snapshot_id"],
                "members": len(manifest["items"]),
                "synthetic": True,
            }
        )
    )


if __name__ == "__main__":
    main()
