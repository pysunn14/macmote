"""The launchd user agent that keeps the server running.

The plist is generated here rather than shipped, because it must carry this user's home
directory and the checkout path. `launchctl` is the authority on whether the agent runs: every
command re-reads `launchctl print` instead of trusting what it just did.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from . import config
from .commands import ROOT, STATE_DIR

PLIST = Path.home() / "Library" / "LaunchAgents" / f"{config.AGENT_LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def status() -> dict:
    out = _launchctl("print", f"{_domain()}/{config.AGENT_LABEL}", check=False)
    running = out.returncode == 0
    pid = None
    for line in out.stdout.splitlines():
        if line.strip().startswith("pid = "):
            pid = int(line.split("=")[1])
    return {"label": config.AGENT_LABEL, "plist": str(PLIST), "loaded": running, "pid": pid, "installed": PLIST.exists()}


def install(port: int) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.remember_origin()
    plist = {
        "Label": config.AGENT_LABEL,
        "ProgramArguments": ["/usr/bin/python3", "-m", "macmote", "serve", "--port", str(port)],  # the CLT python, a stable path
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "LimitLoadToSessionType": "Aqua",   # the helper talks to the user's display session
        "ProcessType": "Interactive",
        "StandardOutPath": str(STATE_DIR / "launchd.out.log"),
        "StandardErrorPath": str(STATE_DIR / "launchd.err.log"),
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    _launchctl("bootout", f"{_domain()}/{config.AGENT_LABEL}", check=False)
    _launchctl("bootstrap", _domain(), str(PLIST))
    return status()


def uninstall() -> dict:
    _launchctl("bootout", f"{_domain()}/{config.AGENT_LABEL}", check=False)
    if PLIST.exists():
        PLIST.unlink()
    return status()


def restart() -> dict:
    _launchctl("kickstart", "-k", f"{_domain()}/{config.AGENT_LABEL}")
    return status()
