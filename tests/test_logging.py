import json
import tempfile
import unittest
from pathlib import Path

from macremote import commands as c


class RotatingLogTests(unittest.TestCase):
    def test_log_lines_are_json_and_files_rotate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.log"
            c.open_log(path, max_bytes=2000, backups=2)
            try:
                for n in range(200):
                    c.log("event", n=n, text="x" * 40)
                files = sorted(p.name for p in Path(tmp).iterdir())
                self.assertEqual(files, ["server.log", "server.log.1", "server.log.2"])
                first = json.loads(path.read_text().splitlines()[0])
                self.assertEqual(first["event"], "event")
                self.assertIn("ts", first)
            finally:
                c.open_log(None)
