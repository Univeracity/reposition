"""First-run commands must work without installation or touching user data."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_checkout_demos_work_without_site_packages_and_preserve_user_files(self):
        with tempfile.TemporaryDirectory() as folder:
            working = Path(folder)
            database = working / ".reposition/index.sqlite"
            database.parent.mkdir()
            database.write_bytes(b"existing user database: do not open or overwrite")
            scratch = working / "temporary"
            scratch.mkdir()
            environment = os.environ.copy()
            environment.update({name: str(scratch) for name in ("TMPDIR", "TEMP", "TMP")})
            for args, expected in (
                (("demo",), "https://github.com/example/packages/issues/40"),
                (("demo", "advisory metadata"), "Version-bound advisory metadata"),
                (("demo", "--cache"), "Read the first result with a larger source window"),
                (("demo", "--cache", "src/terminal.py"), "\nsrc/terminal.py\n"),
                (("demo", "--cache", "unmatchedsyntheticterm"), "No matching fragments"),
            ):
                with self.subTest(args=args):
                    process = subprocess.run(
                        [sys.executable, "-I", "-S", str(ROOT / "reposition"), *args],
                        cwd=working,
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertIn(expected, process.stdout)
                    self.assertEqual(process.stderr, "")
                    self.assertEqual(
                        database.read_bytes(),
                        b"existing user database: do not open or overwrite",
                    )
                    self.assertEqual(list(scratch.iterdir()), [])
                    self.assertEqual(
                        {str(path.relative_to(working)) for path in working.rglob("*")},
                        {".reposition", ".reposition/index.sqlite", "temporary"},
                    )


if __name__ == "__main__":
    unittest.main()
