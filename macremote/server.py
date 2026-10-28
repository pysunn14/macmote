"""HTTP server: the page, the state endpoint and the action endpoint, plus request guards."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

from .actions import apply
from .approvals import KINDS, Relay
from . import config
from .commands import HELPER, STATE_DIR, WEB, ActionError, InvalidAction, Unavailable, log, now, run, write_json_atomic
from .push import Push, validate_subscription
from .state import read_name, read_state

MAX_WAIT = 30.0  # seconds a single long-poll may hang; askers loop until their own deadline


def check_request_headers(headers: Any) -> Optional[str]:
    """Reject requests a cross-site page could forge.

    A custom header turns any cross-origin fetch into a CORS preflight, which this
    server never approves, and plain HTML forms cannot set headers at all.
    """
    if headers.get("X-Mac-Remote") != "1":
        return "requests without the X-Mac-Remote header are refused"
    if (headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
        return "only JSON requests are accepted"
    site = headers.get("Sec-Fetch-Site")
    if site not in (None, "same-origin", "none"):
        return f"request came from another site ({site})"
    return None


class App:
    def __init__(self, run: Callable = run, name: Optional[str] = None, state_dir: Optional[Path] = None,
                 push: Optional[Any] = None):
        self.run = run
        self.name = read_name(run) if name is None else name
        self.state_dir = state_dir
        self.push = push
        self.https_origin = config.https_origin()
        self.relay = Relay(notify=self._announce)
        self.started_at = now()
        self.heartbeat_at: Optional[str] = None
        self.last_action: Optional[dict] = None

    def _announce(self, item: dict) -> None:
        # Delivery happens off the asker's thread so the PAM module gets its id back immediately.
        log("approval", id=item["id"], kind=item["kind"], command=item["command"], parent=item["parent"])
        if self.push is None:
            return
        payload = {"title": "sudo approval request", "body": " ".join(item["command"])[:200],
                   "url": f"/?approval={item['id']}", "id": item["id"]}
        threading.Thread(target=self.push.send, args=(payload,), daemon=True, name="push").start()

    def _decorate(self, state: dict) -> dict:
        state["name"] = self.name
        state["https_origin"] = self.https_origin
        state["server"] = {"pid": os.getpid(), "started_at": self.started_at,
                           "heartbeat_at": self.heartbeat_at, "last_action": self.last_action}
        return state

    def state(self) -> dict:
        return self._decorate(read_state(self.run))

    def act(self, action: str, value: Any) -> dict:
        started = time.monotonic()
        record = {"name": action, "value": value, "at": now()}
        try:
            state = apply(action, value, self.run)
        except ActionError as error:
            record.update(ok=False, error=str(error), ms=round((time.monotonic() - started) * 1000))
            self.last_action = record
            log("action", **record)
            raise
        record.update(ok=True, reached=state["action"]["reached"], ms=round((time.monotonic() - started) * 1000))
        self.last_action = record
        log("action", **record)
        return self._decorate(state)

    def beat(self, interval: float = 30.0) -> None:
        while True:
            self.heartbeat_at = now()
            if self.state_dir is not None:
                write_json_atomic(self.state_dir / "heartbeat.json",
                                  {"pid": os.getpid(), "started_at": self.started_at,
                                   "heartbeat_at": self.heartbeat_at, "last_action": self.last_action})
            time.sleep(interval)


STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/approvals.js": ("approvals.js", "text/javascript; charset=utf-8"),
}

PAGE_POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "MacRemote/1.0"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # actions are logged by App; per-request access lines are noise

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", PAGE_POLICY)
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The phone closed the connection first (page hidden, poll abandoned). Nothing to
            # recover; one log line instead of a traceback keeps the log readable.
            log("client_gone", path=self.path)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        split = urlsplit(self.path)
        path = split.path
        app = self.server.app
        if path == "/api/state":
            self._json(200, app.state())
            return
        if path == "/api/push/key":
            if app.push is None:
                self._json(503, {"error": "push is not configured"})
            else:
                self._json(200, {"public_key": app.push.public_key})
            return
        if path == "/api/push/status":
            self._json(200, {"configured": app.push is not None,
                             "subscriptions": 0 if app.push is None else len(app.push.subscriptions)})
            return
        parts = path.split("/")
        if parts[1:3] == ["api", "approvals"] and len(parts) == 4 and parts[3] in KINDS:
            self._json(200, {"items": app.relay.pending(parts[3])})
            return
        if parts[1:3] == ["api", "approvals"] and len(parts) == 6 and parts[3] in KINDS and parts[5] == "wait":
            try:
                timeout = min(MAX_WAIT, max(0.0, float(parse_qs(split.query).get("timeout", ["0"])[0])))
            except ValueError:
                timeout = 0.0
            item = app.relay.wait(parts[4], timeout)
            if item is None:
                self._json(404, {"error": "no such request"})
            else:
                self._json(200, item)
            return
        entry = STATIC.get(path)
        if entry is None:
            self._json(404, {"error": "no such path"})
            return
        name, content_type = entry
        cache = "max-age=86400" if name.endswith(".png") else "no-cache"
        self._send(200, (WEB / name).read_bytes(), content_type, cache)

    def _read_json_body(self) -> Optional[dict]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= 4096:
            self._json(400, {"error": "request body size is not acceptable"})
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json(400, {"error": "body is not valid JSON"})
            return None
        if not isinstance(body, dict):
            self._json(400, {"error": "a JSON object is required"})
            return None
        return body

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        parts = path.split("/")
        route = None
        if path == "/api/action":
            route = self._post_action
        elif path in ("/api/push/subscribe", "/api/push/unsubscribe", "/api/push/test"):
            route = self._post_push
        elif path == "/api/log":
            route = self._post_log
        elif parts[1:3] == ["api", "approvals"] and len(parts) in (4, 6) and parts[3] in KINDS \
                and (len(parts) == 4 or parts[5] in ("answer", "deny")):
            route = self._post_approval
        if route is None:
            self._json(404, {"error": "no such path"})
            return
        problem = check_request_headers(self.headers)
        if problem:
            self._json(403, {"error": problem})
            return
        body = self._read_json_body()
        if body is None:
            return
        try:
            route(parts, body)
        except InvalidAction as error:
            self._json(400, {"error": str(error)})

    def _post_action(self, parts: list, body: dict) -> None:
        if not isinstance(body.get("action"), str):
            raise InvalidAction("an action name is required")
        try:
            state = self.server.app.act(body["action"], body.get("value"))
        except InvalidAction:
            raise
        except Unavailable as error:
            self._json(409, {"error": str(error), "state": self.server.app.state()})
            return
        except ActionError as error:
            self._json(500, {"error": str(error), "state": self.server.app.state()})
            return
        self._json(200, state)

    def _post_approval(self, parts: list, body: dict) -> None:
        relay = self.server.app.relay
        if len(parts) == 4:
            self._json(201, relay.create(parts[3], body))
        elif parts[5] == "answer":
            self._json(200, relay.answer(parts[4], body))
        else:
            self._json(200, relay.deny(parts[4]))

    def _post_log(self, parts: list, body: dict) -> None:
        # The phone is the only screen this UI has; its errors land in the server log so they can
        # be read from the Mac. Values are clipped so a client cannot flood the log.
        fields = {k[:32]: str(v)[:300] for k, v in list(body.items())[:12] if isinstance(k, str)}
        log("client", **fields)
        self._json(200, {"ok": True})

    def _post_push(self, parts: list, body: dict) -> None:
        push = self.server.app.push
        if push is None:
            self._json(503, {"error": "push is not configured"})
            return
        if parts[3] == "subscribe":
            push.subscribe(validate_subscription(body))
        elif parts[3] == "test":
            # Setup's last step: prove that notifications reach this phone.
            push.send({"title": "Mac Remote", "body": "Notifications work. Setup is complete.", "url": "/", "id": "test"})
        else:
            endpoint = body.get("endpoint")
            if not isinstance(endpoint, str):
                raise InvalidAction("endpoint is required")
            push.unsubscribe(endpoint)
        self._json(200, {"ok": True})


def make_server(host: str, port: int, app: App) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app  # type: ignore[attr-defined]
    return server


def serve(host: str, port: int) -> None:
    if host not in ("127.0.0.1", "::1"):
        raise SystemExit("the server only listens on loopback; expose it with Tailscale Serve or another TLS proxy")
    if not os.access(HELPER, os.X_OK):
        raise SystemExit(f"helper missing: {HELPER} (run scripts/build.sh)")
    try:
        push: Optional[Push] = Push(STATE_DIR, subject=config.https_origin())
    except (RuntimeError, OSError, ValueError) as error:
        # Push is optional: without the helper the remote still works and the page shows requests.
        log("push_unavailable", error=str(error))
        push = None
    app = App(run, state_dir=STATE_DIR, push=push)
    server = make_server(host, port, app)
    threading.Thread(target=app.beat, daemon=True, name="heartbeat").start()
    log("start", host=host, port=port, pid=os.getpid())
    server.serve_forever()
