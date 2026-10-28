import unittest

from macmote import state as s
from tests.fakes import BATTERY_TEXT, FakeRunner

VOLUME_TEXT = "output volume:56, input volume:missing value, alert volume:100, output muted:missing value"


class ParseTests(unittest.TestCase):
    def test_volume_with_unsupported_mute(self):
        self.assertEqual(s.parse_volume(VOLUME_TEXT), (56, None))

    def test_volume_muted(self):
        text = "output volume:12, input volume:50, alert volume:100, output muted:true"
        self.assertEqual(s.parse_volume(text), (12, True))

    def test_volume_unavailable(self):
        text = "output volume:missing value, input volume:missing value, alert volume:100, output muted:missing value"
        self.assertEqual(s.parse_volume(text), (None, None))

    def test_battery_charging(self):
        self.assertEqual(s.parse_battery(BATTERY_TEXT), {"percent": 87, "state": "charging", "source": "AC Power"})

    def test_battery_attached_but_not_charging(self):
        text = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; AC attached; not charging present: true\n"
        self.assertEqual(s.parse_battery(text)["state"], "AC attached")

    def test_battery_absent(self):
        self.assertIsNone(s.parse_battery("Now drawing from 'AC Power'\n"))


class ReadStateTests(unittest.TestCase):
    def test_state_converts_fractions_to_percent(self):
        state = s.read_state(FakeRunner())
        self.assertEqual(state["keyboard"], 0)
        self.assertEqual(state["brightness"], 56)
        self.assertEqual(state["volume"], 56)
        self.assertIs(state["muted"], False)
        self.assertTrue(state["displays_asleep"])
        self.assertFalse(state["locked"])
        self.assertEqual(state["battery"]["percent"], 87)
        self.assertEqual(state["errors"], {})

    def test_failed_reads_are_reported_per_field(self):
        state = s.read_state(FakeRunner(fail={"macctl"}))
        self.assertIn("helper", state["errors"])
        self.assertIsNone(state["keyboard"])
        self.assertEqual(state["volume"], 56)

    def test_helper_field_errors_are_surfaced(self):
        import json
        helper = json.dumps({"keyboard": None, "keyboard_error": "missing class", "brightness": None,
                             "brightness_error": "no built-in display", "displays": [], "locked": None})
        state = s.read_state(FakeRunner(helper=helper))
        self.assertEqual(state["errors"]["keyboard"], "missing class")
        self.assertEqual(state["errors"]["brightness"], "no built-in display")
        self.assertIsNone(state["displays_asleep"])


class MuteModeTests(unittest.TestCase):
    """A device without a mute control is muted in software by driving the volume to 0."""

    def test_device_with_mute_control_reports_hardware_mode(self):
        state = s.read_state(FakeRunner(hardware_mute=True))
        self.assertEqual(state["mute_mode"], "hardware")
        self.assertIs(state["muted"], True)

    def test_device_without_mute_control_is_muted_when_volume_is_zero(self):
        self.assertEqual(s.read_state(FakeRunner())["mute_mode"], "software")
        self.assertIs(s.read_state(FakeRunner(volume=56))["muted"], False)
        self.assertIs(s.read_state(FakeRunner(volume=0))["muted"], True)

    def test_unknown_volume_means_unknown_mute(self):
        state = s.read_state(FakeRunner(fail={"osascript"}))
        self.assertIsNone(state["mute_mode"])
        self.assertIsNone(state["muted"])


class AudioAndMediaStateTests(unittest.TestCase):
    def test_audio_devices_and_media_are_read(self):
        state = s.read_state(FakeRunner())
        self.assertEqual(state["audio"]["default"], "BuiltIn")
        self.assertEqual([d["uid"] for d in state["audio"]["devices"]], ["BuiltIn", "DAC-1"])
        self.assertEqual(state["media"]["title"], "Song A")
        self.assertIs(state["media"]["playing"], True)

    def test_missing_media_and_audio_are_none(self):
        state = s.read_state(FakeRunner(media=None if False else {"title": ""}, audio={"default": None, "devices": "?"}))
        self.assertIsNone(state["media"])
        self.assertIsNone(state["audio"])
