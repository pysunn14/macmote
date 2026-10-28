"""Fixed command paths, the subprocess runner and the shared error types."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
HELPER = str(ROOT / "build" / "macctl")
NOWPLAYING = str(Path(__file__).resolve().parent / "nowplaying.js")  # run by osascript, see the file
STATE_DIR = Path.home() / ".local" / "state" / "macmote"

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


_logger: Optional[logging.Logger] = None


def open_log(path: Optional[Path], max_bytes: int = 1_000_000, backups: int = 5) -> None:
    """Send log lines to a size-rotated file instead of stderr (None restores stderr).

    The server runs under launchd, which would otherwise keep one ever-growing stderr file open.
    Rotation is done here, in-process, so no external newsyslog signal is needed.
    """
    global _logger
    if path is None:
        _logger = None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("macmote")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _logger = logger


def log(event: str, **fields: Any) -> None:
    line = json.dumps({"ts": now(), "event": event, **fields}, ensure_ascii=False)
    if _logger is not None:
        _logger.info(line)
    else:
        print(line, file=sys.stderr, flush=True)


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
