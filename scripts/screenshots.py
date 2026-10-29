#!/usr/bin/python3
"""Renders the README screenshots from a mock Mac, so they show no real machine.

Starts the real server on a spare port with a runner that answers like a Mac in a flattering
state (charging, music playing, a couple of sudo requests waiting), then photographs the page
with headless Chrome at iPhone size. Output: docs/screenshots/*.png.

    scripts/screenshots.py            # needs Google Chrome
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from macmote import commands as c  # noqa: E402
from macmote import server as srv  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 18799
OUT = ROOT / "docs" / "screenshots"

HELPER = {"keyboard": 0.2, "brightness": 0.6, "displays": [{"id": 1, "builtin": True, "asleep": False}], "locked": False,
          "audio": {"default": "speakers", "devices": [{"uid": "speakers", "name": "MacBook Pro Speakers", "transport": "builtin"},
                                                        {"uid": "airpods", "name": "AirPods Pro", "transport": "bluetooth"},
                                                        {"uid": "display", "name": "Studio Display", "transport": "displayport"}]}}
MEDIA = {"title": "Weightless", "artist": "Marconi Union", "album": "Ambient Works", "app": "Music", "playing": True,
         "elapsed": 142.0, "duration": 480.0}
BATTERY = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t82%; charging; 0:41 remaining present: true\n"
REQUESTS = [
    {"command": ["sudo", "brew", "services", "restart", "postgresql@16"], "cwd": "~/Projects/api", "user": "alex",
     "pid": 48213, "ppid": 48100, "parent": "claude", "tty": "/dev/ttys003", "challenge": "bW9ja2NoYWxsZW5nZQ", "timeout": 600},
    {"command": ["sudo", "rm", "-rf", "/Library/Caches/com.example.build"], "cwd": "~/Projects/api", "user": "alex",
     "pid": 48220, "ppid": 48100, "parent": "claude", "tty": "/dev/ttys003", "challenge": "bW9ja2NoYWxsZW5nZTI", "timeout": 600},
]


def mock_runner(argv: list, timeout: float = 5.0) -> str:
    if argv[:3] == [c.OSASCRIPT, "-e", "get volume settings"]:
        return "output volume:35, input volume:50, alert volume:100, output muted:false"
    if argv == [c.PMSET, "-g", "batt"]:
        return BATTERY
    if argv == [c.HELPER, "state"]:
        return json.dumps(HELPER)
    if argv == [c.OSASCRIPT, "-l", "JavaScript", c.NOWPLAYING]:
        return json.dumps(MEDIA)
    return ""


def shoot(url: str, name: str, lang: str, height: int) -> None:
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--lang={lang}", f"--accept-lang={lang}",
                    # Headless Chrome will not go narrower than 500 CSS px; the page centres its 440 px column.
                    "--window-size=500,%d" % height, "--force-device-scale-factor=2", "--virtual-time-budget=5000",
                    f"--screenshot={OUT / name}", url], check=True, capture_output=True)
    print("wrote", OUT / name)


def main() -> None:
    if not os.access(CHROME, os.X_OK):
        raise SystemExit("Google Chrome is needed for screenshots")
    OUT.mkdir(parents=True, exist_ok=True)
    app = srv.App(mock_runner, name="Studio")
    app.https_origin = "https://studio.example.ts.net:18791"
    server = srv.make_server("127.0.0.1", PORT, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    try:
        shoot(f"{base}/", "remote-en.png", "en-US", 1040)
        shoot(f"{base}/", "remote-ko.png", "ko-KR", 1040)
        for request in REQUESTS:
            app.relay.create("sudo", request)
        shoot(f"{base}/", "approvals-en.png", "en-US", 1180)
        for item in app.relay.pending("sudo"):
            app.relay.deny(item["id"])
        app.relay.create("enroll", {"user": "alex", "challenge": "bW9ja2NoYWxsZW5nZQ", "rp_id": "studio.example.ts.net", "timeout": 600})
        shoot(f"{base}/#pair=AbCdEfGhIjKlMnOp", "setup-en.png", "en-US", 760)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
