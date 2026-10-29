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
from .ui import BAD, DOT, OK, WARN, bold, cyan, dim, line, table

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
    print(bold(f"Macmote {__version__}") + (f" {DOT} {state['name']}" if state else ""))
    if state is None:
        print(line(BAD, "server", server_error or "not answering", "macmote agent install"))
    else:
        print(line(OK, "server", f"running {DOT} pid {state['server']['pid']} {DOT} port {config.port()} {DOT} heartbeat {_ago(state['server']['heartbeat_at'])}"))
        for key, message in state["errors"].items():
            print(line(WARN, key, message))
    if agent_state["loaded"]:
        print(line(OK, "agent", f"{agent_state['label']} loaded"))
    else:
        print(line(BAD, "agent", "not installed" if not agent_state["installed"] else "installed but not loaded", "macmote agent install"))
    if origin:
        print(line(OK, "origin", origin))
    else:
        print(line(BAD, "origin", "none", "passkeys and notifications need an HTTPS origin: macmote config set origin https://host:port"))
    if hooked:
        print(line(OK, "sudo hook", str(PAM_MODULE)))
    else:
        print(line(BAD, "sudo hook", "not installed", "macmote sudo install (or macmote setup)"))
    if push is None:
        pass
    elif not push["configured"]:
        print(line(WARN, "push", "not configured", "build/webpush is missing; run scripts/build.sh"))
    elif push["subscriptions"] == 0:
        print(line(WARN, "push", "no phone subscribed", "turn on notifications in the Home Screen app"))
    else:
        print(line(OK, "push", f"{push['subscriptions']} phone(s) subscribed"))
    print(line(DOT, "phones", dim("macmote phones")))
    if pending:
        print(line(WARN, "pending", f"{len(pending)} sudo request(s)"))
        print(table([[i["id"], _ago(i["created_at"]), i["parent"], " ".join(i["command"])] for i in pending]))
    else:
        print(line(DOT, "pending", dim("no sudo requests")))
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
    elif state["action"]["reached"]:
        print(f" {OK} {args.action}" + (f" {args.value}" if args.value is not None else ""))
    else:
        print(f" {BAD} {args.action}: sent, but the Mac did not reach that state")
    return 0 if state["action"]["reached"] else 1


ACTION_HELP = {
    "display_off": "sleep the displays", "display_on": "wake the displays", "lock": "lock the session",
    "away": "keyboard off, lock, displays off", "volume": "output volume", "mute": "mute or unmute",
    "keyboard": "keyboard backlight", "brightness": "built-in display brightness",
    "output": "default audio output device (uid from: macmote state)", "media_toggle": "play or pause",
    "media_next": "next track", "media_previous": "previous track",
}


def cmd_actions(args) -> int:
    shapes = {"none": "", "percent": "<0-100>", "bool": "true|false", "text": "<uid>"}
    print(table([[name, shapes[ACTIONS[name].kind], ACTION_HELP.get(name, "")] for name in sorted(ACTIONS)]))
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
        print(f" {OK} {key} = {data[key]}")
        print(f"   {dim('apply with: macmote agent restart')}")
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
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["loaded"]:
        print(line(OK, "agent", f"{result['label']} loaded {DOT} pid {result['pid']}"))
    else:
        print(line(BAD if args.verb != "uninstall" else OK, "agent", "not loaded" if result["installed"] else "not installed"))
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
        print(dim(" no pending sudo requests"))
    else:
        rows = [[i["id"], _ago(i["created_at"]), i["parent"], (i["risk"]["level"] + ":" + i["risk"]["key"]) if i.get("risk") else "",
                 " ".join(i["command"])] for i in items]
        print(table(rows, header=["id", "age", "from", "risk", "command"]))
    return 0


def cmd_push(args) -> int:
    if args.verb == "test":
        _api("POST", "/api/push/test", {})
        print(f" {OK} test notification sent")
        return 0
    status = _api("GET", "/api/push/status")
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(line(OK if status["configured"] else BAD, "push", f"{status['subscriptions']} phone(s) subscribed" if status["configured"] else "not configured"))
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

GROUPS = [
    ("Set up", [("setup", "guided install, ends with a QR code to pair the phone"), ("pair", "pair another phone (sudo)"),
                ("phones", "list paired phones (sudo)"), ("unpair <id>", "remove a paired phone (sudo)"),
                ("sudo install|remove", "the PAM hook that asks the phone (sudo)")]),
    ("Every day", [("status", "what is running, exposed, hooked and pending"), ("approvals [deny <id>]", "pending sudo requests"),
                   ("run <action> [value]", "drive the remote from the terminal"), ("actions", "the actions and their values"),
                   ("push [test]", "notification subscriptions; send a test")]),
    ("Under the hood", [("state", "the Mac's state as JSON"), ("logs [--pam]", "recent log lines"),
                        ("config [set <key> <value>]", "the HTTPS origin and ports"),
                        ("agent install|restart|uninstall|status", "the launchd user agent"),
                        ("serve", "run the server in the foreground")]),
]


class _Help(argparse.HelpFormatter):
    """Top-level help as grouped commands instead of argparse's flat list."""


def _usage() -> str:
    out = [bold("macmote") + " " + dim("<command> [options]") + "\n",
           "Phone remote for your Mac, with Face ID approval for sudo.\n"]
    width = max(len(name) for _, items in GROUPS for name, _ in items)
    for title, items in GROUPS:
        out.append(cyan(title))
        for name, text in items:
            out.append(f"  {name.ljust(width)}  {dim(text)}")
        out.append("")
    out.append(dim("macmote <command> --help for options; --json where a command describes state."))
    return "\n".join(out)


class _Parser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return _usage() + "\n"

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"macmote: {message}\n")

    def print_usage(self, file=None) -> None:
        print(_usage() if self.prog == "macmote" else self.format_usage(), file=file or sys.stdout)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="macmote", description="Phone remote for your Mac, with Face ID approval for sudo.")
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
    p.add_argument("--json", action="store_true")
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
    p.add_argument("--json", action="store_true")
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
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(_usage())
        return 0
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except CommandError as error:
        print(f"macmote: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
