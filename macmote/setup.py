"""`macmote setup`: one guided run from a fresh checkout to an enrolled phone.

Each step observes the real state (launchctl, the HTTP endpoint, `tailscale serve status`, the
files sudo loads) before deciding whether it has anything to do, so the wizard can be re-run at
any time. Steps that need root run `sudo` in the foreground so Touch ID or the password prompt
works as usual. The pure decisions (`prerequisites`, `serve_plan`) take plain data and are unit
tested; the side effects live in `run`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import agent, config
from .commands import ROOT

HELPERS = ("macctl", "webpush", "pam_macmote.so", "macmote-enroll")
PAM_MODULE = Path("/usr/local/lib/pam/pam_macmote.so")
ENROLL_TOOL = Path("/usr/local/libexec/macmote/macmote-enroll")
SUDO_LOCAL = Path("/etc/pam.d/sudo_local")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fatal: bool = True


def tailscale_status() -> Optional[dict]:
    try:
        return json.loads(subprocess.run([config.TAILSCALE, "status", "--json"], capture_output=True, text=True,
                                         timeout=5, check=True).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def serve_status() -> dict:
    try:
        return json.loads(subprocess.run([config.TAILSCALE, "serve", "status", "--json"], capture_output=True, text=True,
                                         timeout=5, check=True).stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def prerequisites(status: Optional[dict], swiftc: Optional[str], python: tuple, origin: Optional[str]) -> list:
    """What must be true before anything is installed. `status` is `tailscale status --json`."""
    checks = [
        Check("Swift compiler", swiftc is not None, swiftc or "xcode-select --install"),
        Check("Python", python >= (3, 9), ".".join(map(str, python[:3]))),
    ]
    if status is None:
        checks.append(Check("Tailscale", origin is not None,
                            "not running; set origin in the config file to use another HTTPS proxy" if origin is None
                            else f"not running; using configured origin {origin}", fatal=origin is None))
        return checks
    running = status.get("BackendState") == "Running"
    checks.append(Check("Tailscale", running, status.get("BackendState", "?")))
    domains = status.get("CertDomains") or []
    checks.append(Check("HTTPS certificates", bool(domains), domains[0] if domains else
                        "enable MagicDNS and HTTPS in the Tailscale admin console (DNS → HTTPS Certificates)"))
    return checks


def serve_plan(serve: dict, host: str, port: int, https_port: int) -> list:
    """Tailscale Serve commands still needed so both ports proxy to the loopback server."""
    backend = f"http://127.0.0.1:{port}"
    web = serve.get("Web") or {}
    def proxied(listen_port: int) -> bool:
        handlers = (web.get(f"{host}:{listen_port}") or {}).get("Handlers") or {}
        return (handlers.get("/") or {}).get("Proxy") == backend
    plan = []
    if not proxied(port):
        plan.append([config.TAILSCALE, "serve", "--bg", f"--http={port}", backend])
    if not proxied(https_port):
        plan.append([config.TAILSCALE, "serve", "--bg", f"--https={https_port}", backend])
    return plan


def fetch_json(url: str, timeout: float = 3.0, resolve_ip: Optional[str] = None) -> Optional[dict]:
    """GET a JSON document; with `resolve_ip`, pins the host like curl --resolve (MagicDNS may be
    off on the Mac itself while the phone resolves the name fine)."""
    if resolve_ip:
        out = subprocess.run(["curl", "-s", "--max-time", str(int(timeout)), "--resolve",
                              f"{url.split('/')[2]}:{resolve_ip}", url], capture_output=True, text=True)
        try:
            return json.loads(out.stdout) if out.returncode == 0 else None
        except ValueError:
            return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError, OSError):
        return None


def wait_until(predicate: Callable[[], bool], seconds: float, every: float = 0.5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(every)
    return predicate()


class Console:
    def __init__(self, assume_yes: bool):
        self.assume_yes = assume_yes
        self.step_number = 0

    def step(self, title: str) -> None:
        self.step_number += 1
        print(f"\n[{self.step_number}] {title}")

    def line(self, ok: Optional[bool], text: str) -> None:
        mark = "  " if ok is None else ("✓ " if ok else "✗ ")
        print(f"   {mark}{text}")

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.assume_yes:
            return default
        answer = input(f"   {question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not answer else answer in ("y", "yes")


def run(args) -> int:
    out = Console(args.yes)
    origin = config.https_origin()
    status = tailscale_status()

    out.step("Prerequisites")
    checks = prerequisites(status, shutil.which("swiftc"), sys.version_info, origin)
    for check in checks:
        out.line(check.ok, f"{check.name}: {check.detail}")
    if any(not c.ok and c.fatal for c in checks):
        print("\nFix the items marked ✗ and run setup again.")
        return 1

    out.step("Native helpers")
    if args.rebuild or any(not (ROOT / "build" / name).exists() for name in HELPERS):
        subprocess.run([str(ROOT / "scripts" / "build.sh")], check=True)
        out.line(True, "built with scripts/build.sh")
    else:
        out.line(True, "already built (use --rebuild to rebuild)")

    out.step("Server (launchd user agent)")
    port = config.port()
    state = agent.install(port)
    origin = config.https_origin()  # install remembers the detected origin
    ready = wait_until(lambda: fetch_json(f"http://127.0.0.1:{port}/api/state") is not None, 15)
    out.line(ready, f"{state['label']} pid {state['pid']} on 127.0.0.1:{port}" if ready else "server did not answer")
    if not ready:
        return 1

    if status is not None:
        out.step("Tailscale Serve")
        host = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
        https_port = int(config.load().get("https_port", config.DEFAULT_HTTPS_PORT))
        plan = serve_plan(serve_status(), host, port, https_port)
        if not plan:
            out.line(True, "both ports already proxy to the server")
        else:
            for command in plan:
                out.line(None, " ".join(command[1:]))
            if out.confirm("Apply these Tailscale Serve rules?"):
                for command in plan:
                    subprocess.run(command, check=True)
                out.line(not serve_plan(serve_status(), host, port, https_port), "applied")
            else:
                out.line(False, "skipped; passkeys and notifications need the HTTPS port")
        ip = ((status.get("Self") or {}).get("TailscaleIPs") or [None])[0]
        if origin and ip:
            reachable = fetch_json(f"{origin}/api/state", resolve_ip=ip) is not None
            out.line(reachable, f"{origin}/ answers over HTTPS" if reachable else f"{origin}/ not reachable yet")
    if origin is None:
        print("\nNo HTTPS origin. Set \"origin\" in", config.CONFIG_FILE, "and run setup again.")
        return 1

    if not args.skip_sudo:
        out.step("sudo approval (PAM module, needs sudo)")
        installed = PAM_MODULE.exists() and SUDO_LOCAL.exists() and str(PAM_MODULE) in SUDO_LOCAL.read_text()
        if installed and not args.rebuild:
            out.line(True, f"{PAM_MODULE} is wired into {SUDO_LOCAL}")
        else:
            subprocess.run(["sudo", str(ROOT / "scripts" / "install-sudo.sh")], check=True)
            out.line(True, "installed")

        out.step("Pair the phone (QR code)")
        enrolled = subprocess.run(["sudo", str(ENROLL_TOOL), "list"], capture_output=True, text=True)
        existing = [line for line in enrolled.stdout.splitlines()[1:] if line.strip()]
        if existing:
            for line in existing:
                out.line(True, line)
        if not existing or out.confirm("Pair another phone?", default=False):
            command = ["sudo", str(ENROLL_TOOL), "enroll", "--origin", origin, "--name", args.name, "--test"]
            if args.no_qr:
                command.append("--no-qr")
            if subprocess.run(command).returncode != 0:
                out.line(False, "pairing did not complete; run setup again to retry")
                return 1

    out.step("Notifications")
    before = (fetch_json(f"http://127.0.0.1:{port}/api/push/status") or {}).get("subscriptions", 0)
    if before:
        out.line(True, f"{before} phone(s) subscribed")
    else:
        print("   On the phone: add the page to the Home Screen, open it from there and turn on")
        print("   \"sudo approval alerts\". Waiting up to 10 minutes...")
        subscribed = wait_until(lambda: (fetch_json(f"http://127.0.0.1:{port}/api/push/status") or {})
                                .get("subscriptions", 0) > before, 600, every=3)
        out.line(subscribed, "subscribed" if subscribed else "no subscription yet; the page still shows requests")
    print("\nSetup complete. Open", origin + "/", "on the phone.")
    return 0
