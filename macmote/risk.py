"""Flags sudo commands that deserve a second look before a tap on the phone.

This is a heuristic for the person, not a security control: the module never refuses anything
because of it, and an attacker can always spell a command differently. Each rule names a
reason key the page translates. The first matching rule wins; rules are ordered by severity.
"""
from __future__ import annotations

import re
from typing import Optional

# (level, key, regex over the command joined with single spaces)
RULES = [
    ("high", "wipe", r"\b(diskutil\s+(erase\w*|reformat\w*|partition\w*|zero\w*|secureErase)|dd\s+.*\bof=/dev/|mkfs|newfs)\b"),
    ("high", "delete-recursive", r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*\s+)+(/|~|/\*|/[A-Za-z]|\$HOME|\.\.)"),
    ("high", "system-integrity", r"\b(csrutil|nvram|spctl\s+--(master|global)-disable|bputil|kmutil|kextload|kextunload)\b"),
    ("high", "sudoers", r"\b(visudo|/etc/sudoers|/etc/pam\.d/|sudo_local|/etc/master\.passwd|dscl\s+.*\s-(create|passwd|append))\b"),
    ("high", "pipe-to-shell", r"\b(curl|wget)\b.*\|\s*(sudo\s+)?(sh|bash|zsh|python3?)\b"),
    ("high", "daemon", r"\blaunchctl\s+(bootstrap|load|enable|bootout|unload)\s+.*(system|/Library/Launch(Daemons|Agents))"),
    ("high", "power", r"\b(shutdown|reboot|halt|killall\s+(-9\s+)?(loginwindow|WindowServer|Finder))\b"),
    ("medium", "permissions-recursive", r"\b(chmod|chown|chgrp)\s+(-[a-zA-Z]*R|--recursive)\b"),
    ("medium", "installer", r"\b(installer\s+-pkg|softwareupdate\s+-i|pkgutil\s+--forget|brew\s+(install|uninstall)\s+--cask)\b"),
    ("medium", "network-config", r"\b(networksetup|pfctl|ifconfig\s+\w+\s+(up|down|inet)|route\s+(add|delete)|/etc/hosts)\b"),
    ("medium", "kill", r"\b(kill|pkill|killall)\s+(-9|-KILL)\b"),
    ("medium", "system-write", r"(\s|^)(>|>>|tee\s+(-a\s+)?)\s*/(etc|System|Library|usr|bin|sbin|var|private)/"),
]
_COMPILED = [(level, key, re.compile(pattern)) for level, key, pattern in RULES]


def assess(command: list) -> Optional[dict]:
    """None for an ordinary command, else {"level": "high"|"medium", "key": <reason key>}."""
    text = " ".join(command)
    for level, key, pattern in _COMPILED:
        if pattern.search(text):
            return {"level": level, "key": key}
    return None
