import json
import tempfile
import unittest
from pathlib import Path

from macmote import actions as a
from macmote import commands as c
from macmote.state import read_state
from tests.fakes import AWAKE_HELPER_JSON, FakeRunner

STATE = read_state(FakeRunner())


class PlanTests(unittest.TestCase):
    def test_volume_maps_to_fixed_osascript_argv(self):
        self.assertEqual(a.plan("volume", 30, STATE), [[c.OSASCRIPT, "-e", "set volume output volume 30"]])

    def test_display_actions_use_pmset_and_caffeinate(self):
        self.assertEqual(a.plan("display_off", None, STATE), [[c.PMSET, "displaysleepnow"]])
        self.assertEqual(a.plan("display_on", None, STATE), [[c.CAFFEINATE, "-u", "-t", "1"]])

    def test_away_turns_keyboard_off_then_locks_then_sleeps_display(self):
        self.assertEqual(
            a.plan("away", None, STATE),
            [[c.HELPER, "keyboard", "0"], [c.HELPER, "lock"], [c.PMSET, "displaysleepnow"]],
        )

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(c.InvalidAction):
            a.plan("rm -rf", None, STATE)

    def test_percent_actions_accept_only_integers_in_range(self):
        for bad in (None, -1, 101, True, "30", 30.5, float("nan")):
            with self.subTest(value=bad), self.assertRaises(c.InvalidAction):
                a.plan("keyboard", bad, STATE)
        self.assertEqual(a.plan("keyboard", 0, STATE), [[c.HELPER, "keyboard", "0"]])
        self.assertEqual(a.plan("brightness", 100, STATE), [[c.HELPER, "brightness", "100"]])

    def test_actions_without_value_reject_a_value(self):
        with self.assertRaises(c.InvalidAction):
            a.plan("lock", 1, STATE)

    def test_mute_accepts_only_booleans(self):
        with self.assertRaises(c.InvalidAction):
            a.plan("mute", 1, STATE)

    def test_mute_uses_the_device_control_when_it_has_one(self):
        state = read_state(FakeRunner(hardware_mute=False))
        self.assertEqual(a.plan("mute", True, state), [[c.OSASCRIPT, "-e", "set volume output muted true"]])


class ApplyTests(unittest.TestCase):
    def test_apply_runs_steps_in_order_and_returns_fresh_state(self):
        runner = FakeRunner()
        state = a.apply("away", None, runner, wait=0)
        reads = ([c.HELPER, "state"], [c.PMSET, "-g", "batt"], [c.OSASCRIPT, "-l", "JavaScript", c.NOWPLAYING])
        actions = [call for call in runner.calls if call not in reads and call[:3] != [c.OSASCRIPT, "-e", "get volume settings"]]
        self.assertEqual(actions, a.plan("away", None, STATE))
        # The fake helper reports an unlocked session, so the scene must not claim success.
        self.assertEqual(state["action"], {"name": "away", "reached": False})


class KeyboardNeedsDisplayTests(unittest.TestCase):
    """macOS keeps the keyboard backlight off while displays sleep and ignores brightness changes."""

    def test_keyboard_is_refused_while_displays_sleep(self):
        runner = FakeRunner()
        with self.assertRaises(c.Unavailable):
            a.apply("keyboard", 30, runner, wait=0)
        self.assertNotIn([c.HELPER, "keyboard", "30"], runner.calls)

    def test_keyboard_runs_while_display_is_awake(self):
        runner = FakeRunner(helper=AWAKE_HELPER_JSON)
        a.apply("keyboard", 30, runner, wait=0)
        self.assertIn([c.HELPER, "keyboard", "30"], runner.calls)

    def test_away_still_runs_every_step_while_displays_sleep(self):
        runner = FakeRunner()
        a.apply("away", None, runner, wait=0)
        for step in a.plan("away", None, STATE):
            self.assertIn(step, runner.calls)


class SoftwareMuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = a.MUTE_MEMORY
        a.MUTE_MEMORY = Path(self.tmp.name) / "mute.json"

    def tearDown(self):
        a.MUTE_MEMORY = self.saved
        self.tmp.cleanup()

    def test_mute_drives_volume_to_zero_and_remembers_the_old_volume(self):
        runner = FakeRunner(volume=56)
        state = a.apply("mute", True, runner, wait=0)
        self.assertIn([c.OSASCRIPT, "-e", "set volume output volume 0"], runner.calls)
        self.assertEqual(state["volume"], 0)
        self.assertEqual(state["action"], {"name": "mute", "reached": True})
        self.assertEqual(json.loads(a.MUTE_MEMORY.read_text())["volume"], 56)

    def test_unmute_restores_the_remembered_volume(self):
        runner = FakeRunner(volume=56)
        a.apply("mute", True, runner, wait=0)
        state = a.apply("mute", False, runner, wait=0)
        self.assertIn([c.OSASCRIPT, "-e", "set volume output volume 56"], runner.calls)
        self.assertEqual(state["volume"], 56)
        self.assertIs(state["muted"], False)
        self.assertEqual(state["action"], {"name": "mute", "reached": True})

    def test_muting_while_already_silent_keeps_the_remembered_volume(self):
        runner = FakeRunner(volume=56)
        a.apply("mute", True, runner, wait=0)
        a.apply("mute", True, runner, wait=0)
        self.assertEqual(json.loads(a.MUTE_MEMORY.read_text())["volume"], 56)

    def test_unmute_without_memory_uses_the_default_volume(self):
        runner = FakeRunner(volume=0)
        state = a.apply("mute", False, runner, wait=0)
        self.assertEqual(state["volume"], a.DEFAULT_UNMUTE_VOLUME)

    def test_hardware_mute_leaves_the_volume_alone(self):
        runner = FakeRunner(volume=56, hardware_mute=False)
        state = a.apply("mute", True, runner, wait=0)
        self.assertIn([c.OSASCRIPT, "-e", "set volume output muted true"], runner.calls)
        self.assertEqual(state["volume"], 56)
        self.assertIs(state["muted"], True)
        self.assertFalse(a.MUTE_MEMORY.exists())

    def test_mute_is_unavailable_when_volume_cannot_be_read(self):
        runner = FakeRunner(fail={"osascript"})
        with self.assertRaises(c.Unavailable):
            a.apply("mute", True, runner, wait=0)


class OutputDeviceTests(unittest.TestCase):
    def test_switching_to_a_listed_device_is_observed(self):
        runner = FakeRunner()
        state = a.apply("output", "DAC-1", runner, wait=0)
        self.assertIn([c.HELPER, "output", "DAC-1"], runner.calls)
        self.assertEqual(state["audio"]["default"], "DAC-1")
        self.assertEqual(state["action"], {"name": "output", "reached": True})

    def test_unknown_device_is_refused_before_running_anything(self):
        runner = FakeRunner()
        with self.assertRaises(c.Unavailable):
            a.apply("output", "nope", runner, wait=0)
        self.assertNotIn([c.HELPER, "output", "nope"], runner.calls)

    def test_value_must_be_a_string(self):
        with self.assertRaises(c.InvalidAction):
            a.plan("output", 7, STATE)


class MediaTests(unittest.TestCase):
    def test_toggle_is_judged_by_the_playing_flag_changing(self):
        runner = FakeRunner()
        state = a.apply("media_toggle", None, runner, wait=0)
        self.assertIs(state["media"]["playing"], False)
        self.assertEqual(state["action"], {"name": "media_toggle", "reached": True})

    def test_next_is_judged_by_the_track_changing(self):
        runner = FakeRunner()
        state = a.apply("media_next", None, runner, wait=0)
        self.assertEqual(state["media"]["title"], "Song B")
        self.assertTrue(state["action"]["reached"])
        state = a.apply("media_previous", None, runner, wait=0)
        self.assertEqual(state["media"]["title"], "Song A")
        self.assertTrue(state["action"]["reached"])

    def test_a_command_that_changed_nothing_is_reported(self):
        runner = FakeRunner()
        original = runner.__call__
        def ignore_media(argv, timeout=5.0):
            if argv[:5] == [c.OSASCRIPT, "-l", "JavaScript", c.NOWPLAYING, "send"]:
                runner.calls.append(argv)
                return '{"sent": 2}'
            return original(argv, timeout)
        state = a.apply("media_toggle", None, ignore_media, wait=0)
        self.assertFalse(state["action"]["reached"])

    def test_nothing_playing_is_unavailable(self):
        runner = FakeRunner(media={})  # the helper reports no now-playing item
        with self.assertRaises(c.Unavailable):
            a.apply("media_toggle", None, runner, wait=0)
