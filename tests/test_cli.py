import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from macmote import cli, config


class ParserTests(unittest.TestCase):
    def test_every_subcommand_parses(self):
        parser = cli.build_parser()
        for argv in (["status"], ["status", "--json"], ["state"], ["run", "volume", "30"], ["actions"], ["config"],
                     ["config", "origin"], ["config", "set", "port", "1"], ["agent", "status"], ["pair", "--name", "X"],
                     ["phones"], ["unpair", "abc"], ["sudo", "install"], ["approvals"], ["approvals", "deny", "id"],
                     ["push", "test"], ["logs", "-n", "5", "--pam"], ["serve", "--port", "1"]):
            args = parser.parse_args(argv)
            self.assertTrue(callable(args.func), argv)

    def test_unknown_action_is_rejected_by_the_parser(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["run", "rm"])

    def test_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["--version"])
        self.assertIn("macmote", out.getvalue())


class ConfigCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = config.CONFIG_FILE
        config.CONFIG_FILE = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        config.CONFIG_FILE = self.saved
        self.tmp.cleanup()

    def test_set_and_get_round_trip(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["config", "set", "origin", "https://mac.example.com:18791/"]), 0)
            self.assertEqual(cli.main(["config", "origin"]), 0)
        self.assertIn("https://mac.example.com:18791\n", out.getvalue())
        self.assertEqual(json.loads(config.CONFIG_FILE.read_text())["origin"], "https://mac.example.com:18791")

    def test_bad_origin_and_unknown_key_are_errors(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["config", "set", "origin", "http://plain"]), 1)
            self.assertEqual(cli.main(["config", "nope"]), 1)
        self.assertIn("https://", err.getvalue())
        self.assertIn("unknown key", err.getvalue())


class UiTests(unittest.TestCase):
    def test_table_pads_all_but_the_last_column(self):
        from macmote import ui
        text = ui.table([["a", "bb", "long text"], ["ccc", "d", "x"]], header=["id", "n", "what"])
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith(" a    bb  long text"))
        self.assertTrue(lines[2].startswith(" ccc  d   x"))

    def test_no_colour_off_a_terminal(self):
        from macmote import ui
        self.assertEqual(ui.paint("x", "1"), "x")  # tests never run on a tty
