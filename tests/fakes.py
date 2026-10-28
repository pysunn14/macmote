"""A runner that answers state reads like the real commands and remembers volume changes."""
import json
import os
import re

from macmote import commands as c


HELPER_JSON = json.dumps({
    "keyboard": 0.001,
    "brightness": 0.5625,
    "displays": [{"id": 1, "builtin": True, "asleep": True}, {"id": 3, "builtin": False, "asleep": True}],
    "locked": False,
})
AWAKE_HELPER_JSON = json.dumps({
    "keyboard": 0.3,
    "brightness": 0.5,
    "displays": [{"id": 1, "builtin": True, "asleep": False}],
    "locked": False,
})
BATTERY_TEXT = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1234)\t87%; charging; 1:02 remaining present: true\n"


class FakeRunner:
    """Records argv calls and answers state reads like the real commands do."""

    def __init__(self, helper=HELPER_JSON, fail=None, volume=56, hardware_mute=None):
        self.calls = []
        self.helper = helper
        self.fail = fail or set()
        self.volume = volume
        self.hardware_mute = hardware_mute  # None mimics a device without a mute control

    def volume_text(self):
        muted = "missing value" if self.hardware_mute is None else str(self.hardware_mute).lower()
        return f"output volume:{self.volume}, input volume:missing value, alert volume:100, output muted:{muted}"

    def __call__(self, argv, timeout=5.0):
        self.calls.append(argv)
        name = os.path.basename(argv[0])
        if name in self.fail:
            raise c.ActionError(f"{name} failed (1): boom")
        if argv[:3] == [c.OSASCRIPT, "-e", "get volume settings"]:
            return self.volume_text()
        if argv[:2] == [c.OSASCRIPT, "-e"]:
            set_volume = re.fullmatch(r"set volume output volume (\d+)", argv[2])
            if set_volume:
                self.volume = int(set_volume.group(1))
            set_mute = re.fullmatch(r"set volume output muted (true|false)", argv[2])
            if set_mute and self.hardware_mute is not None:
                self.hardware_mute = set_mute.group(1) == "true"
        if argv == [c.PMSET, "-g", "batt"]:
            return BATTERY_TEXT
        if argv == [c.HELPER, "state"]:
            return self.helper
        return ""
