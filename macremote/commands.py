"""Fixed command paths, the subprocess runner and the shared error types."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HELPER = str(ROOT / "build" / "macctl")
STATE_DIR = Path.home() / ".local" / "state" / "mac-remote"

OSASCRIPT = "/usr/bin/osascript"
PMSET = "/usr/bin/pmset"
CAFFEINATE = "/usr/bin/caffeinate"
SCUTIL = "/usr/sbin/scutil"


class ActionError(Exception):
    """A command failed or could not run."""


class InvalidAction(ActionError):
    """The request itself is not an allowed action or value."""


class Unavailable(ActionError):
    """The Mac is in a state where the action cannot take effect."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": now(), "event": event, **fields}, ensure_ascii=False), file=sys.stderr, flush=True)


def run(argv: list, timeout: float = 5.0) -> str:
    name = os.path.basename(argv[0])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ActionError(f"{name} timed out") from None
    except OSError as error:
        raise ActionError(f"{name} could not run: {error.strerror}") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ActionError(f"{name} failed ({proc.returncode}): {detail}")
    return proc.stdout


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
