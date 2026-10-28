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


AUDIO = {"default": "BuiltIn", "devices": [{"uid": "BuiltIn", "name": "MacBook Pro Speakers", "transport": "builtin"},
                                            {"uid": "DAC-1", "name": "USB DAC", "transport": "usb"}]}
MEDIA = {"title": "Song A", "artist": "Band", "album": "Album", "app": "Music", "playing": True, "elapsed": 42.0, "duration": 200.0}
TRACKS = ["Song A", "Song B"]


class FakeRunner:
    """Records argv calls and answers state reads like the real commands do."""

    def __init__(self, helper=HELPER_JSON, fail=None, volume=56, hardware_mute=None, audio=None, media=None):
        self.calls = []
        self.helper = helper
        self.fail = fail or set()
        self.volume = volume
        self.hardware_mute = hardware_mute  # None mimics a device without a mute control
        self.audio = json.loads(json.dumps(AUDIO)) if audio is None else audio
        self.media = json.loads(json.dumps(MEDIA)) if media is None else media

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
            helper = json.loads(self.helper)
            helper["audio"] = self.audio
            return json.dumps(helper)
        if argv == [c.OSASCRIPT, "-l", "JavaScript", c.NOWPLAYING]:
            return json.dumps(self.media if self.media and self.media.get("title") else None)
        if argv[:2] == [c.HELPER, "output"]:
            if argv[2] not in [d["uid"] for d in self.audio["devices"]]:
                raise c.ActionError("macctl failed (1): no output device with uid " + argv[2])
            self.audio["default"] = argv[2]
        if argv[:5] == [c.OSASCRIPT, "-l", "JavaScript", c.NOWPLAYING, "send"] and self.media and self.media.get("title"):
            code = argv[5]
            if code == "2":
                self.media["playing"] = not self.media["playing"]
            else:
                index = TRACKS.index(self.media["title"])
                self.media["title"] = TRACKS[(index + (1 if code == "4" else -1)) % len(TRACKS)]
                self.media["elapsed"] = 0.0
            return json.dumps({"sent": int(code)})
        return ""
