"""Pure: one declared dependency, name normalisation per ecosystem, and finding the manifest line a
declaration sits on (the line a bump edits)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_PIP_SEPARATORS = re.compile(r"[-_.]+")


@dataclass(frozen=True)
class Dep:
    name: str
    spec: str          # the declared version text, verbatim ("" when none)
    ecosystem: str
    scope: str         # "runtime" | "dev"
    line: int          # 1-based line holding the version; 0 when not one line
    path: str
    editable: bool = True


def normalise(eco: str, name: str) -> str:
    text = (name or "").strip().casefold()
    if eco == "pip":
        return _PIP_SEPARATORS.sub("-", text)
    if eco == "cargo":
        return text.replace("_", "-")
    return text


def same_package(eco: str, declared: str, wanted: str) -> bool:
    """Maven: `group:artifact` matches exactly; a bare artifact matches the artifact part."""
    a, b = normalise(eco, declared), normalise(eco, wanted)
    if eco == "maven" and ":" in a and ":" not in b:
        return a.split(":", 1)[1] == b
    return a == b


def locate_line(lines: Sequence[str], needles: Sequence[str], start: int = 0, end: int | None = None) -> int:
    """The one line (1-based) in `lines[start:end]` containing every needle; 0 if none or several."""
    stop = len(lines) if end is None else end
    hits = [i + 1 for i in range(start, stop) if all(n in lines[i] for n in needles if n)]
    return hits[0] if len(hits) == 1 else 0


def scope_word(group: str) -> str:
    lowered = (group or "").casefold()
    dev_words = ("dev", "test", "lint", "doc", "typing", "check", "bench")
    return "dev" if any(w in lowered for w in dev_words) else "runtime"
