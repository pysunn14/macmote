"""The `macmote` command.

One entry point for everything a person does with Macmote: installing, pairing a phone,
checking what is going on, driving the remote from a terminal, and the root-side tools that
otherwise live under /usr/local/libexec. Subcommands print short text for people; the ones
that describe state take --json for scripts. Errors are one line on stderr and exit code 1.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import __version__, agent, config, setup
from .actions import ACTIONS
from .commands import ROOT, STATE_DIR, ActionError, run
from .server import App, serve

ENROLL_TOOL = Path("/usr/local/libexec/macmote/macmote-enroll")
PAM_MODULE = Path("/usr/local/lib/pam/pam_macmote.so")
SUDO_LOCAL = Path("/etc/pam.d/sudo_local")
PAM_LOG = Path("/var/log/macmote-pam.log")


class CommandError(Exception):
    """A failure the person can act on; printed as one line."""


# ---- talking to the running server -------------------------------------------------------

def _api(method: str, path: str, body: Optional[dict] = None, timeout: float = 5.0) -> Any:
    port = config.port()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Macmote": "1"} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("error", "")
        except ValueError:
            detail = ""
        raise CommandError(f"server answered {error.code}: {detail}") from None
    except (urllib.error.URLError, OSError):
        raise CommandError(f"the server is not answering on 127.0.0.1:{port} (try: macmote agent install)") from None


def _ago(iso: Optional[str]) -> str:
    if not iso:
        return "never"
    try:
        seconds = int((datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds())
    except ValueError:
        return iso
    return f"{seconds}s ago" if seconds < 90 else f"{seconds // 60} min ago"


def _row(label: str, value: str) -> None:
    print(f"  {label:<10} {value}")


# ---- subcommands ----------------------------------------------------------------------------

def cmd_status(args) -> int:
    agent_state = agent.status()
    origin = config.https_origin()
    try:
        state = _api("GET", "/api/state")
        push = _api("GET", "/api/push/status")
        pending = _api("GET", "/api/approvals/sudo")["items"]
        server_error = None
    except CommandError as error:
        state, push, pending, server_error = None, None, [], str(error)
    hooked = PAM_MODULE.exists() and SUDO_LOCAL.exists() and str(PAM_MODULE) in SUDO_LOCAL.read_text()
    report = {"version": __version__, "agent": agent_state, "origin": origin, "server": None if state is None else state["server"],
              "errors": None if state is None else state["errors"], "push": push, "pending": pending,
              "sudo_hook": {"module": str(PAM_MODULE), "installed": hooked}, "server_error": server_error}
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"Macmote {__version__}")
    if state is None:
        _row("server", f"not answering: {server_error}")
    else:
        _row("server", f"running, pid {state['server']['pid']}, port {config.port()}, heartbeat {_ago(state['server']['heartbeat_at'])}")
        if state["errors"]:
            _row("errors", "; ".join(f"{k}: {v}" for k, v in state["errors"].items()))
    _row("agent", f"{agent_state['label']} " + ("loaded" if agent_state["loaded"] else "not loaded" if agent_state["installed"] else "not installed"))
    _row("origin", origin or "none (passkeys and push need an HTTPS origin; see macmote config)")
    _row("sudo hook", f"installed, {PAM_MODULE}" if hooked else "not installed (macmote sudo install)")
    _row("phones", "see: macmote phones")
    if push is not None:
        _row("push", f"{push['subscriptions']} subscription(s)" if push["configured"] else "not configured")
    _row("pending", f"{len(pending)} sudo request(s)" + ("" if not pending else ": " + ", ".join(" ".join(i["command"]) for i in pending)))
    return 0


def cmd_state(args) -> int:
    print(json.dumps(App(run).state() if args.local else _api("GET", "/api/state"), ensure_ascii=False, indent=2))
    return 0


def _cli_value(text: Optional[str]) -> Any:
    if text is None:
        return None
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        return text


def cmd_run(args) -> int:
    body = {"action": args.action}
    if args.value is not None:
        body["value"] = _cli_value(args.value)
    try:
        state = App(run).act(args.action, body.get("value")) if args.local else _api("POST", "/api/action", body)
    except ActionError as error:
        raise CommandError(str(error)) from None
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(f"{args.action}: " + ("done" if state["action"]["reached"] else "sent, but the Mac did not reach that state"))
    return 0 if state["action"]["reached"] else 1


def cmd_actions(args) -> int:
    shapes = {"none": "", "percent": " <0-100>", "bool": " true|false", "text": " <id>"}
    for name in sorted(ACTIONS):
        print(f"  {name}{shapes[ACTIONS[name].kind]}")
    return 0


SETTABLE = ("origin", "port", "https_port")


def cmd_config(args) -> int:
    words = args.words
    data = config.load()
    if words[:1] == ["set"]:
        if len(words) != 3 or words[1] not in SETTABLE:
            raise CommandError(f"usage: macmote config set <{'|'.join(SETTABLE)}> <value>")
        key, value = words[1], words[2]
        if key == "origin" and not value.startswith("https://"):
            raise CommandError("origin must start with https://")
        data[key] = int(value) if key in ("port", "https_port") else value.rstrip("/")
        config.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"{key} = {data[key]} (apply with: macmote agent restart)")
        return 0
    if len(words) > 1:
        raise CommandError("usage: macmote config [<key>] | macmote config set <key> <value>")
    effective = {"origin": config.https_origin(), "detected_origin": config.detect_origin(data), "port": config.port(),
                 "https_port": int(data.get("https_port", config.DEFAULT_HTTPS_PORT)), "agent_label": config.AGENT_LABEL,
                 "file": str(config.CONFIG_FILE)}
    if words:
        if words[0] not in effective:
            raise CommandError(f"unknown key {words[0]}; one of {', '.join(effective)}")
        print("" if effective[words[0]] is None else effective[words[0]])
    else:
        print(json.dumps(effective, indent=2))
    return 0


def cmd_agent(args) -> int:
    result = {"install": lambda: agent.install(config.port()), "uninstall": agent.uninstall,
              "restart": agent.restart, "status": agent.status}[args.verb]()
    print(json.dumps(result, indent=2))
    return 0


def _sudo(argv: list) -> int:
    """Run a root-side tool in the foreground so Touch ID, the phone or the password can answer."""
    return subprocess.run(["sudo", *argv]).returncode


def cmd_pair(args) -> int:
    if not ENROLL_TOOL.exists():
        raise CommandError("the sudo hook is not installed yet (macmote sudo install, or macmote setup)")
    origin = config.https_origin()
    if origin is None:
        raise CommandError("no HTTPS origin; set one with: macmote config set origin https://host:port")
    command = [str(ENROLL_TOOL), "enroll", "--origin", origin, "--name", args.name, "--test"]
    if args.no_qr:
        command.append("--no-qr")
    return _sudo(command)


def cmd_phones(args) -> int:
    if not ENROLL_TOOL.exists():
        raise CommandError("the sudo hook is not installed yet")
    return _sudo([str(ENROLL_TOOL), "list"])


def cmd_unpair(args) -> int:
    if not ENROLL_TOOL.exists():
        raise CommandError("the sudo hook is not installed yet")
    return _sudo([str(ENROLL_TOOL), "remove", args.id])


def cmd_sudo(args) -> int:
    script = ROOT / "scripts" / "install-sudo.sh"
    if args.verb == "remove":
        return _sudo([str(script), "remove"])
    if not (ROOT / "build" / "pam_macmote.so").exists():
        raise CommandError("helpers are not built; run scripts/build.sh (or macmote setup)")
    return _sudo([str(script)])


def cmd_approvals(args) -> int:
    if args.verb == "deny":
        if not args.id:
            raise CommandError("usage: macmote approvals deny <id>")
        print(json.dumps(_api("POST", f"/api/approvals/sudo/{args.id}/deny", {}), indent=2))
        return 0
    items = _api("GET", "/api/approvals/sudo")["items"]
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    elif not items:
        print("no pending sudo requests")
    else:
        for item in items:
            risk = f" [{item['risk']['level']}: {item['risk']['key']}]" if item.get("risk") else ""
            print(f"  {item['id']}  {' '.join(item['command'])}{risk}  (from {item['parent']}, {_ago(item['created_at'])})")
    return 0


def cmd_push(args) -> int:
    if args.verb == "test":
        _api("POST", "/api/push/test", {})
        print("test notification sent")
        return 0
    print(json.dumps(_api("GET", "/api/push/status"), indent=2))
    return 0


def cmd_logs(args) -> int:
    path = PAM_LOG if args.pam else STATE_DIR / "server.log"
    if not path.exists():
        raise CommandError(f"{path} does not exist yet")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines:]
    print("\n".join(lines))
    return 0


def cmd_serve(args) -> int:
    serve(args.host, config.port() if args.port is None else args.port)
    return 0


# ---- parser ---------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="macmote", description="Phone remote for your Mac, with Face ID approval for sudo.")
    parser.add_argument("--version", action="version", version=f"macmote {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.required = True

    p = commands.add_parser("setup", help="guided install: helpers, agent, Tailscale Serve, sudo hook, phone pairing")
    p.add_argument("--name", default="iPhone", help="name stored with the phone's passkey")
    p.add_argument("--yes", action="store_true", help="accept defaults without asking")
    p.add_argument("--rebuild", action="store_true", help="rebuild helpers and reinstall the sudo hook")
    p.add_argument("--skip-sudo", action="store_true", help="only the remote, no sudo approval")
    p.add_argument("--no-qr", action="store_true", help="print the pairing code instead of a QR code")
    p.set_defaults(func=setup.run)

    p = commands.add_parser("status", help="what is running, exposed, hooked and pending")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = commands.add_parser("state", help="the Mac's current state as JSON")
    p.add_argument("--local", action="store_true", help="read directly instead of asking the server")
    p.set_defaults(func=cmd_state)

    p = commands.add_parser("run", help="run one remote action (see: macmote actions)")
    p.add_argument("action", choices=sorted(ACTIONS), metavar="action")
    p.add_argument("value", nargs="?")
    p.add_argument("--json", action="store_true")
    p.add_argument("--local", action="store_true", help="run directly instead of through the server")
    p.set_defaults(func=cmd_run)

    commands.add_parser("actions", help="list remote actions and their values").set_defaults(func=cmd_actions)

    p = commands.add_parser("config", help="show effective configuration (optionally one key), or: config set <key> <value>")
    p.add_argument("words", nargs="*", metavar="[set] [key] [value]")
    p.set_defaults(func=cmd_config)

    p = commands.add_parser("agent", help="the launchd user agent that keeps the server running")
    p.add_argument("verb", choices=["install", "uninstall", "restart", "status"])
    p.set_defaults(func=cmd_agent)

    p = commands.add_parser("pair", help="pair a phone: prints a QR code, verifies a test approval (sudo)")
    p.add_argument("--name", default="iPhone")
    p.add_argument("--no-qr", action="store_true")
    p.set_defaults(func=cmd_pair)
    commands.add_parser("phones", help="list paired phones (sudo)").set_defaults(func=cmd_phones)
    p = commands.add_parser("unpair", help="remove a paired phone by id prefix (sudo)")
    p.add_argument("id")
    p.set_defaults(func=cmd_unpair)

    p = commands.add_parser("sudo", help="install or remove the sudo approval hook (sudo)")
    p.add_argument("verb", choices=["install", "remove"])
    p.set_defaults(func=cmd_sudo)

    p = commands.add_parser("approvals", help="pending sudo requests; deny one")
    p.add_argument("verb", nargs="?", default="list", choices=["list", "deny"])
    p.add_argument("id", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_approvals)

    p = commands.add_parser("push", help="notification subscriptions; send a test")
    p.add_argument("verb", nargs="?", default="status", choices=["status", "test"])
    p.set_defaults(func=cmd_push)

    p = commands.add_parser("logs", help="recent server log lines (or the sudo module's with --pam)")
    p.add_argument("-n", "--lines", type=int, default=30)
    p.add_argument("--pam", action="store_true")
    p.set_defaults(func=cmd_logs)

    p = commands.add_parser("serve", help="run the HTTP server in the foreground (what the agent runs)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except CommandError as error:
        print(f"macmote: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
