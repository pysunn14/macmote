"""Where the remote is reachable from the phone.

Nothing machine-specific lives in the source: the HTTPS origin Safari needs for passkeys and
Web Push comes from ~/.config/mac-remote/config.json, and when that file says nothing it is
derived from Tailscale (the MagicDNS name plus the HTTPS port). The enrollment tool still takes
the origin on its own command line, so the root-side key file never depends on what this
user-level process claims.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

CONFIG_FILE = Path.home() / ".config" / "mac-remote" / "config.json"
TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
DEFAULT_PORT = 18790        # loopback HTTP port the server listens on
DEFAULT_HTTPS_PORT = 18791  # port Tailscale Serve (or another TLS proxy) exposes to the phone
AGENT_LABEL = "io.github.pysunn.mac-remote"  # launchd label of the user agent


def load() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read {CONFIG_FILE}: {error}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"{CONFIG_FILE} must hold a JSON object")
    return data


def tailscale_dns_name() -> Optional[str]:
    """The MagicDNS name Tailscale issues certificates for, or None without Tailscale."""
    try:
        status = json.loads(subprocess.run([TAILSCALE, "status", "--json"], capture_output=True, text=True,
                                           timeout=5, check=True).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    domains = status.get("CertDomains") or []
    name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
    return name if name and name in domains else (domains[0] if domains else None)


def port() -> int:
    return int(load().get("port", DEFAULT_PORT))


def detect_origin(data: Optional[dict] = None) -> Optional[str]:
    """The Tailscale-derived HTTPS origin, or None without Tailscale."""
    data = load() if data is None else data
    name = tailscale_dns_name()
    if name is None:
        return None
    return f"https://{name}:{int(data.get('https_port', DEFAULT_HTTPS_PORT))}"


def https_origin() -> Optional[str]:
    """The configured origin, or None (plain http only).

    Detection is not attempted here on purpose: the server starts from launchd, where the
    Tailscale CLI may not answer yet, and a value that flips between restarts would confuse
    the page and the push subject. `remember_origin()` runs at install time instead.
    """
    origin = load().get("origin")
    return origin.rstrip("/") if isinstance(origin, str) and origin.startswith("https://") else None


def remember_origin() -> Optional[str]:
    """Writes the detected origin into the config file unless one is already set."""
    data = load()
    if https_origin() is not None:
        return https_origin()
    origin = detect_origin(data)
    if origin is not None:
        data["origin"] = origin
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return origin
