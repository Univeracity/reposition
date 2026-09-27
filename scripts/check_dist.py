"""Check built archives for licenses and accidental local/cache inclusion."""

import argparse
import tarfile
import zipfile
from pathlib import Path


def check(path):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
    banned = ("dev_coordination", ".agent-state", ".agent-artifacts", ".venv", "__pycache__")
    for name in names:
        if any(part in name.split("/") for part in banned) or name.endswith(
            (".sqlite", ".db", ".pyc")
        ):
            raise ValueError(f"local artifact in distribution: {name}")
        if "validation/local/" in name:
            raise ValueError(f"local validation artifact in distribution: {name}")
    for required in ("LICENSE", "NOTICE", "Vyral-Apache-2.0.txt", "triage-o-mator-MIT.txt"):
        if not any(name.endswith("/" + required) for name in names):
            raise ValueError(f"missing license attribution: {required}")
    print(f"{path.name}: passed ({len(names)} archive entries)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()
    artifacts = sorted([*args.directory.glob("*.whl"), *args.directory.glob("*.tar.gz")])
    if not artifacts:
        raise ValueError("no built distributions found")
    for path in artifacts:
        check(path)


if __name__ == "__main__":
    main()
