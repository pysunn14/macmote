"""Web Push subscriptions and delivery.

Subscriptions live in the user's state directory; delivery (VAPID signing and RFC 8291 payload
encryption) is done by the Swift helper `build/webpush`, which carries the CryptoKit code the
Python standard library lacks. Push is a convenience, not a security boundary: the page always
shows the real pending requests from the relay, whether or not a notification arrived.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .commands import ROOT, InvalidAction, log, now, write_json_atomic

WEBPUSH = str(ROOT / "build" / "webpush")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


def validate_subscription(body: Any) -> dict:
    if not isinstance(body, dict):
        raise InvalidAction("a JSON object is required")
    endpoint = body.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048:
        raise InvalidAction("endpoint must be an https URL")
    keys = body.get("keys")
    if not isinstance(keys, dict):
        raise InvalidAction("keys are required")
    out = {"endpoint": endpoint, "keys": {}}
    for name in ("p256dh", "auth"):
        value = keys.get(name)
        if not isinstance(value, str) or not _BASE64URL.match(value):
            raise InvalidAction(f"keys.{name} must be a base64url string")
        out["keys"][name] = value
    return out


def run_helper(argv: list, stdin: str) -> str:
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}")
    return proc.stdout


class Push:
    def __init__(self, state_dir: Path, helper: Callable[[list, str], str] = run_helper, subject: Optional[str] = None):
        # RFC 8292 wants a contact in the VAPID token; the site's HTTPS origin serves. Apple
        # answers 403 BadJwtToken to anything mentioning "localhost" (observed 2026-10).
        self.subject = subject
        self._lock = threading.Lock()
        self._file = state_dir / "push.json"
        self._helper = helper
        self._data = self._load()
        if "vapid" not in self._data:
            self._data["vapid"] = json.loads(helper([WEBPUSH, "keygen"], ""))
            self._save()

    def _load(self) -> dict:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("subscriptions", [])
        return data

    def _save(self) -> None:
        write_json_atomic(self._file, self._data)

    @property
    def public_key(self) -> str:
        return self._data["vapid"]["public_key"]

    @property
    def subscriptions(self) -> list:
        with self._lock:
            return list(self._data["subscriptions"])

    def subscribe(self, subscription: dict) -> None:
        with self._lock:
            self._data["subscriptions"] = [s for s in self._data["subscriptions"]
                                           if s["endpoint"] != subscription["endpoint"]]
            self._data["subscriptions"].append({**subscription, "added_at": now()})
            self._save()

    def unsubscribe(self, endpoint: str) -> None:
        with self._lock:
            self._data["subscriptions"] = [s for s in self._data["subscriptions"] if s["endpoint"] != endpoint]
            self._save()

    def send(self, payload: dict) -> None:
        """Deliver to every subscription; a subscription the push service reports gone is dropped."""
        if self.subject is None:
            log("push_error", error="no HTTPS origin configured; nothing to put in the VAPID subject")
            return
        for subscription in self.subscriptions:
            request = {"vapid": self._data["vapid"], "subject": self.subject, "subscription": subscription,
                       "payload": payload, "ttl": 120}
            try:
                result = json.loads(self._helper([WEBPUSH, "send"], json.dumps(request)))
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                log("push_error", endpoint=subscription["endpoint"][:60], error=str(error))
                continue
            log("push", endpoint=subscription["endpoint"][:60], status=result.get("status"))
            if result.get("status") in (404, 410):
                self.unsubscribe(subscription["endpoint"])
