"""Reading the Mac's observable state: volume, brightness, keyboard, displays, lock, battery."""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .commands import HELPER, NOWPLAYING, OSASCRIPT, PMSET, SCUTIL, ActionError, log, run


def parse_volume(text: str) -> tuple:
    volume = re.search(r"output volume:(\d+)", text)
    muted = re.search(r"output muted:(true|false)", text)
    return (int(volume.group(1)) if volume else None, (muted.group(1) == "true") if muted else None)


def parse_battery(text: str) -> Optional[dict]:
    match = re.search(r"(\d+)%;\s*([^;]+);", text)
    if match is None:
        return None
    source = re.search(r"drawing from '([^']+)'", text)
    return {"percent": int(match.group(1)), "state": match.group(2).strip(),
            "source": source.group(1) if source else None}


def _percent_of(fraction: Optional[float]) -> Optional[int]:
    return None if fraction is None else int(round(fraction * 100))


def read_state(run: Callable = run) -> dict:
    state: dict = {"volume": None, "muted": None, "mute_mode": None, "brightness": None, "keyboard": None,
                   "displays_asleep": None, "locked": None, "battery": None, "audio": None, "media": None, "errors": {}}
    errors = state["errors"]
    try:
        state["volume"], muted = parse_volume(run([OSASCRIPT, "-e", "get volume settings"]))
    except ActionError as error:
        errors["volume"] = str(error)
    else:
        # "hardware": the output device has a mute control and macOS reports it.
        # "software": it has none (`output muted` is `missing value`), so muted means volume 0.
        if muted is not None:
            state["muted"], state["mute_mode"] = muted, "hardware"
        elif state["volume"] is not None:
            state["muted"], state["mute_mode"] = state["volume"] == 0, "software"
    try:
        helper = json.loads(run([HELPER, "state"]))
        state["keyboard"] = _percent_of(helper.get("keyboard"))
        state["brightness"] = _percent_of(helper.get("brightness"))
        state["locked"] = helper.get("locked")
        displays = helper.get("displays") or []
        state["displays_asleep"] = all(d["asleep"] for d in displays) if displays else None
        audio = helper.get("audio")
        if isinstance(audio, dict) and isinstance(audio.get("devices"), list):
            state["audio"] = {"default": audio.get("default"),
                              "devices": [d for d in audio["devices"] if isinstance(d, dict) and d.get("uid")]}

        for field in ("keyboard", "brightness"):
            if helper.get(f"{field}_error"):
                errors[field] = helper[f"{field}_error"]
    except (ActionError, ValueError) as error:
        errors["helper"] = str(error)
    try:
        state["battery"] = parse_battery(run([PMSET, "-g", "batt"]))
    except ActionError as error:
        errors["battery"] = str(error)
    try:
        state["media"] = parse_media(run([OSASCRIPT, "-l", "JavaScript", NOWPLAYING]))
    except (ActionError, ValueError) as error:
        errors["media"] = str(error)
    return state


def parse_media(text: str) -> Optional[dict]:
    """The now-playing document from nowplaying.js, or None when nothing is playing."""
    media = json.loads(text or "null")
    if not isinstance(media, dict) or not media.get("title"):
        return None
    if "error" in media:
        raise ActionError(str(media["error"]))
    return {"title": media["title"], "artist": media.get("artist") or "", "album": media.get("album") or "",
            "app": media.get("app") or "", "playing": bool(media.get("playing")),
            "elapsed": float(media.get("elapsed") or 0), "duration": float(media.get("duration") or 0)}


def read_name(run: Callable = run) -> str:
    try:
        return run([SCUTIL, "--get", "ComputerName"]).strip() or "Mac"
    except ActionError as error:
        # The name is only a page title; a failed lookup is logged instead of stopping the remote.
        log("name_error", error=str(error))
        return "Mac"
