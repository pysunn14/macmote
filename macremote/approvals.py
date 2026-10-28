"""Relay between a root-side asker (the PAM module or the enrollment tool) and the phone page.

The relay never judges a decision. A sudo request carries a challenge minted by the PAM module;
the phone answers with a passkey assertion over that challenge, and the module verifies it with
the public key it keeps in a root-only file. Keeping verification out of this user-level process
means an agent that edits or restarts this server still cannot forge an approval. An enrollment
works the same way: the page sends back the code the user typed plus the new public key, and the
root-side tool decides whether to keep it.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any, Callable, Optional

from .commands import InvalidAction, now

KEEP_DECIDED = 600.0  # seconds a decided or expired item stays readable for late pollers
MAX_TIMEOUT = 600
# Any local process can create requests, and each sudo request becomes a phone notification.
# These bounds keep a runaway or hostile process from burying real requests under a flood.
RATE_LIMIT = 6        # creations per kind within RATE_WINDOW
RATE_WINDOW = 60.0    # seconds


class RateLimited(InvalidAction):
    """Too many requests of one kind in the window; the asker should fall back to local auth."""


class Duplicate(InvalidAction):
    """An identical request is already waiting for the phone."""

    def __init__(self, existing_id: str):
        super().__init__(f"an identical request is already pending ({existing_id})")
        self.existing_id = existing_id
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]{1,8192}$")


def _base64url(value: Any) -> str:
    if not isinstance(value, str) or not _BASE64URL.match(value):
        raise InvalidAction("must be a base64url string")
    return value


def _text(limit: int) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if not isinstance(value, str) or not 0 < len(value) <= limit:
            raise InvalidAction(f"must be a string of at most {limit} characters")
        return value
    return check


def _argv(value: Any) -> list:
    if not isinstance(value, list) or not value or not all(isinstance(a, str) and len(a) <= 4096 for a in value):
        raise InvalidAction("command must be a list of strings")
    return value


def _pid(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise InvalidAction("pid must be a non-negative integer")
    return value


def _timeout(value: Any) -> int:
    if type(value) is not int or not 0 < value <= MAX_TIMEOUT:
        raise InvalidAction(f"timeout must be 1 to {MAX_TIMEOUT} seconds")
    return value


# What each kind of request must carry and what its answer must carry. An enrollment's one-time
# code is deliberately not part of the request: the root-side tool keeps it and only compares it
# with the code typed on the phone, so nothing on this server can read it and enroll a key.
KINDS = {
    "sudo": {
        "request": {"command": _argv, "cwd": _text(4096), "user": _text(256), "pid": _pid, "ppid": _pid,
                    "parent": _text(256), "tty": _text(256), "challenge": _base64url, "timeout": _timeout},
        "answer": {"credential_id": _base64url, "authenticator_data": _base64url,
                   "client_data_json": _base64url, "signature": _base64url},
        "notify": True,
        "identity": ("command", "cwd", "user"),  # fields that make two pending requests "the same"
    },
    "enroll": {
        "request": {"user": _text(256), "challenge": _base64url, "rp_id": _text(256), "timeout": _timeout},
        "answer": {"code": _text(16), "credential_id": _base64url, "public_key": _base64url,
                   "client_data_json": _base64url},
        "notify": False,
        "identity": (),  # only one enrollment window at a time
    },
}


def _validated(fields: dict, body: Any) -> dict:
    if not isinstance(body, dict):
        raise InvalidAction("a JSON object is required")
    out = {}
    for name, check in fields.items():
        if name not in body:
            raise InvalidAction(f"{name} is required")
        try:
            out[name] = check(body[name])
        except InvalidAction as error:
            raise InvalidAction(f"{name}: {error}") from None
    return out


class Relay:
    def __init__(self, notify: Optional[Callable[[dict], None]] = None, clock: Callable[[], float] = time.monotonic):
        self._items: dict = {}
        self._window: list = []  # (kind, created clock) of recent creations, for the rate limit
        self._condition = threading.Condition()
        self._notify = notify
        self._clock = clock

    def create(self, kind: str, body: Any) -> dict:
        spec = KINDS.get(kind)
        if spec is None:
            raise InvalidAction(f"unknown request kind: {kind}")
        item = _validated(spec["request"], body)
        item.update(id=secrets.token_urlsafe(12), kind=kind, status="pending", created_at=now(),
                    answer=None, decided_at=None)
        clock = self._clock()
        item["_deadline"] = clock + item["timeout"]
        with self._condition:
            self._sweep()
            self._window = [(k, t) for k, t in self._window if t > clock - RATE_WINDOW]
            if sum(1 for k, _ in self._window if k == kind) >= RATE_LIMIT:
                raise RateLimited(f"more than {RATE_LIMIT} {kind} requests in {int(RATE_WINDOW)}s")
            identity = spec["identity"]
            for other in self._items.values():
                if other["kind"] == kind and other["status"] == "pending" \
                        and all(other[f] == item[f] for f in identity):
                    raise Duplicate(other["id"])
            self._window.append((kind, clock))
            self._items[item["id"]] = item
        if spec["notify"] and self._notify is not None:
            self._notify(self._public(item))
        return self._public(item)

    @staticmethod
    def _public(item: dict) -> dict:
        return {k: v for k, v in item.items() if not k.startswith("_")}

    def _sweep(self) -> None:
        # Runs under the lock. Expiry is judged by the clock, never by a timer the caller trusts.
        clock = self._clock()
        for item in list(self._items.values()):
            if item["status"] == "pending" and clock > item["_deadline"]:
                item["status"], item["decided_at"] = "expired", now()
                item["_decided"] = clock
            if item["status"] != "pending" and clock > item.get("_decided", clock) + KEEP_DECIDED:
                del self._items[item["id"]]

    def pending(self, kind: str) -> list:
        with self._condition:
            self._sweep()
            return [self._public(i) for i in self._items.values() if i["kind"] == kind and i["status"] == "pending"]

    def get(self, item_id: str) -> Optional[dict]:
        with self._condition:
            self._sweep()
            item = self._items.get(item_id)
            return None if item is None else self._public(item)

    def _decide(self, item_id: str, status: str, answer: Optional[dict]) -> dict:
        with self._condition:
            self._sweep()
            item = self._items.get(item_id)
            if item is None:
                raise InvalidAction("no such request, or it has already been pruned")
            if item["status"] != "pending":
                raise InvalidAction(f"request already settled ({item['status']})")
            if answer is not None:
                answer = _validated(KINDS[item["kind"]]["answer"], answer)
            item.update(status=status, answer=answer, decided_at=now())
            item["_decided"] = self._clock()
            self._condition.notify_all()
            return self._public(item)

    def answer(self, item_id: str, answer: Any) -> dict:
        return self._decide(item_id, "answered", answer)

    def deny(self, item_id: str) -> dict:
        return self._decide(item_id, "denied", None)

    def wait(self, item_id: str, timeout: float) -> Optional[dict]:
        """Block until the item leaves `pending` or `timeout` passes; the asker reads the result."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                self._sweep()
                item = self._items.get(item_id)
                if item is None:
                    return None
                remaining = deadline - time.monotonic()
                if item["status"] != "pending" or remaining <= 0:
                    return self._public(item)
                self._condition.wait(min(remaining, 1.0))
