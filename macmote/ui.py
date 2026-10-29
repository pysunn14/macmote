"""Terminal output for the `macmote` command: colour only on a terminal, plain otherwise."""
from __future__ import annotations

import os
import sys
from typing import Iterable, List, Sequence


def _enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb"


def paint(text: str, *codes: str) -> str:
    if not _enabled() or not codes:
        return text
    return "".join(f"\033[{code}m" for code in codes) + text + "\033[0m"


def bold(text: str) -> str: return paint(text, "1")
def dim(text: str) -> str: return paint(text, "2")
def green(text: str) -> str: return paint(text, "32")
def red(text: str) -> str: return paint(text, "31")
def yellow(text: str) -> str: return paint(text, "33")
def cyan(text: str) -> str: return paint(text, "36")


OK = green("✓")
BAD = red("✗")
WARN = yellow("!")
DOT = dim("·")


def line(mark: str, label: str, text: str, hint: str = "") -> str:
    out = f" {mark} {bold(label.ljust(10))} {text}"
    return out + (f"\n   {dim(hint)}" if hint else "")


def table(rows: Sequence[Sequence[str]], header: Sequence[str] = ()) -> str:
    """Left-aligned columns; the last column is free text and not padded."""
    everything: List[Sequence[str]] = ([header] if header else []) + list(rows)
    widths = [max(len(str(r[i])) for r in everything) for i in range(len(everything[0]) - 1)] if everything else []
    lines = []
    for n, row in enumerate(everything):
        cells = [str(c).ljust(widths[i]) for i, c in enumerate(row[:-1])] + [str(row[-1])]
        text = "  ".join(cells)
        lines.append(dim(text) if header and n == 0 else text)
    return "\n".join(" " + l for l in lines)
