"""The action table: what each remote action runs and how its success is observed."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .commands import (CAFFEINATE, HELPER, NOWPLAYING, OSASCRIPT, PMSET, STATE_DIR, InvalidAction, Unavailable, now, run,
                       write_json_atomic)
from .state import read_state

# Volume to restore on unmute when a device without a mute control was silenced before any software
# mute ran (the slider was dragged to 0), so nothing is remembered. Kept low to protect headphones.
MUTE_MEMORY = STATE_DIR / "mute.json"
DEFAULT_UNMUTE_VOLUME = 25


@dataclass(frozen=True)
class Action:
    kind: str  # "none", "percent", "bool" or "text": the only value shapes accepted
    steps: Callable[[Any, dict], list]  # argv lists to run, chosen from the value and the observed state
    expect: Callable[[dict, Any], bool]  # observed state that confirms the action took effect
    settle: float = 0.0  # display and lock changes land asynchronously, so keep re-reading this long
    precondition: Optional[Callable[[dict], Optional[str]]] = None  # reason the action cannot work now
    before: Optional[Callable[[Any, dict], None]] = None  # bookkeeping to do once the action is about to run
    # For actions whose success is a change rather than a target value (play/pause, next track):
    # given the state before and after, did it happen? Checked instead of `expect` when set.
    changed: Optional[Callable[[dict, dict, Any], bool]] = None


def _asleep(state: dict, _value: Any) -> bool:
    return state["displays_asleep"] is True


KEYBOARD_NEEDS_DISPLAY = "The display is asleep: macOS keeps the keyboard backlight off and ignores changes. Wake the display first"


def _keyboard_needs_display(state: dict) -> Optional[str]:
    # Observed 2026-10-28: while every display slept, keyboard sets ran without error but never
    # took effect, so refuse up front instead of reporting a silent miss. "away" sets the keyboard
    # to 0 before sleeping the display and is deliberately not gated.
    return KEYBOARD_NEEDS_DISPLAY if state["displays_asleep"] is True else None


MUTE_NEEDS_VOLUME = "Volume could not be read, so mute cannot be changed"


def _mute_needs_volume(state: dict) -> Optional[str]:
    return MUTE_NEEDS_VOLUME if state["mute_mode"] is None else None


def _set_volume(value: int) -> list:
    return [OSASCRIPT, "-e", f"set volume output volume {value}"]


def _remembered_volume() -> Optional[int]:
    try:
        volume = json.loads(MUTE_MEMORY.read_text(encoding="utf-8")).get("volume")
    except (OSError, ValueError, AttributeError):
        return None
    return volume if type(volume) is int and 0 < volume <= 100 else None


def _remember_volume(mute: bool, state: dict) -> None:
    # Only a software mute needs to recall the volume; a hardware mute leaves it in place. Muting
    # while already silent keeps the earlier memory so a double tap cannot lose the real volume.
    if mute and state["mute_mode"] == "software" and state["volume"] > 0:
        write_json_atomic(MUTE_MEMORY, {"volume": state["volume"], "at": now()})


def _mute_steps(mute: bool, state: dict) -> list:
    if state["mute_mode"] == "hardware":
        return [[OSASCRIPT, "-e", f"set volume output muted {'true' if mute else 'false'}"]]
    # Observed 2026-10-28: USB DACs such as the TANCHJIM SPACE PRO expose no mute control, so
    # `output muted` reads `missing value`. Silence is then produced by driving the volume to 0.
    if mute:
        return [_set_volume(0)]
    remembered = _remembered_volume()
    return [_set_volume(DEFAULT_UNMUTE_VOLUME if remembered is None else remembered)]


OUTPUT_UNKNOWN = "No output device with that id is connected"
MEDIA_NONE = "Nothing is playing that can be controlled"


def _output_precondition(state: dict) -> Optional[str]:
    return None if state["audio"] and state["audio"]["devices"] else OUTPUT_UNKNOWN


def _media_precondition(state: dict) -> Optional[str]:
    return None if state["media"] is not None else MEDIA_NONE


def _media_command(code: int) -> list:
    return [OSASCRIPT, "-l", "JavaScript", NOWPLAYING, "send", str(code)]


def _media_changed(field: str) -> Callable[[dict, dict, Any], bool]:
    def check(before: dict, after: dict, _value: Any) -> bool:
        if before["media"] is None or after["media"] is None:
            return False
        if field == "playing":
            return before["media"]["playing"] != after["media"]["playing"]
        # A track change shows as a new title, or the same title restarted (single-track loops).
        return before["media"]["title"] != after["media"]["title"] or after["media"]["elapsed"] < before["media"]["elapsed"]
    return check


ACTIONS = {
    "display_off": Action("none", lambda _, s: [[PMSET, "displaysleepnow"]], _asleep, 2.0),
    "display_on": Action("none", lambda _, s: [[CAFFEINATE, "-u", "-t", "1"]],
                         lambda s, _: s["displays_asleep"] is False, 2.0),
    "lock": Action("none", lambda _, s: [[HELPER, "lock"]], lambda s, _: s["locked"] is True, 2.0),
    "away": Action("none", lambda _, s: [[HELPER, "keyboard", "0"], [HELPER, "lock"], [PMSET, "displaysleepnow"]],
                   lambda s, v: _asleep(s, v) and s["locked"] is True, 2.0),
    "volume": Action("percent", lambda v, s: [_set_volume(v)], lambda s, v: s["volume"] == v),
    "mute": Action("bool", _mute_steps, lambda s, v: s["muted"] is v,
                   precondition=_mute_needs_volume, before=_remember_volume),
    "keyboard": Action("percent", lambda v, s: [[HELPER, "keyboard", str(v)]], lambda s, v: s["keyboard"] == v,
                       precondition=_keyboard_needs_display),
    "brightness": Action("percent", lambda v, s: [[HELPER, "brightness", str(v)]],
                         lambda s, v: s["brightness"] == v),
    "output": Action("text", lambda v, s: [[HELPER, "output", v]],
                     lambda s, v: bool(s["audio"]) and s["audio"]["default"] == v, 1.0, precondition=_output_precondition),
    "media_toggle": Action("none", lambda _, s: [_media_command(2)], lambda s, _: s["media"] is not None, 1.5,
                           precondition=_media_precondition, changed=_media_changed("playing")),
    "media_next": Action("none", lambda _, s: [_media_command(4)], lambda s, _: s["media"] is not None, 1.5,
                         precondition=_media_precondition, changed=_media_changed("title")),
    "media_previous": Action("none", lambda _, s: [_media_command(5)], lambda s, _: s["media"] is not None, 1.5,
                             precondition=_media_precondition, changed=_media_changed("title")),
}

_ACTION_LOCK = threading.Lock()


def _validate(kind: str, value: Any) -> Any:
    if kind == "none":
        if value is not None:
            raise InvalidAction("this action takes no value")
        return None
    if kind == "bool":
        if type(value) is not bool:
            raise InvalidAction("value must be true or false")
        return value
    if kind == "text":
        if not isinstance(value, str) or not 0 < len(value) <= 256:
            raise InvalidAction("value must be a string of at most 256 characters")
        return value
    # type() rather than isinstance(): bool is an int subclass and must not pass as 0 or 1.
    if type(value) is not int or not 0 <= value <= 100:
        raise InvalidAction("value must be an integer from 0 to 100")
    return value


def _spec(action: str, value: Any) -> tuple:
    spec = ACTIONS.get(action)
    if spec is None:
        raise InvalidAction(f"unknown action: {action}")
    return spec, _validate(spec.kind, value)


def plan(action: str, value: Any, state: dict) -> list:
    spec, value = _spec(action, value)
    return spec.steps(value, state)


def apply(action: str, value: Any, run: Callable = run, wait: Optional[float] = None) -> dict:
    spec, value = _spec(action, value)
    budget = spec.settle if wait is None else wait
    with _ACTION_LOCK:
        state = read_state(run)
        if spec.precondition is not None:
            reason = spec.precondition(state)
            if reason:
                raise Unavailable(reason)
        if spec.before is not None:
            spec.before(value, state)
        if action == "output" and value not in [d["uid"] for d in state["audio"]["devices"]]:
            raise Unavailable(OUTPUT_UNKNOWN)
        before = state
        for argv in spec.steps(value, state):
            run(argv)
        reached = (lambda s: spec.changed(before, s, value)) if spec.changed else (lambda s: spec.expect(s, value))
        state = read_state(run)
        deadline = time.monotonic() + budget
        while not reached(state) and time.monotonic() < deadline:
            time.sleep(0.15)
            state = read_state(run)
    state["action"] = {"name": action, "reached": bool(reached(state))}
    return state
