"""Pure: the optional alias map (`@pdev = Priya Dev`, `pdev -> Priya Dev`) the user pastes under `Aliases:`.
Keys are normalised handles (no `@`, casefolded); values are canonical names as written."""

from __future__ import annotations

import re

_LINE = re.compile(r"^\s*[-*•]?\s*@?(?P<handle>[\w.'-]+(?:\s+[\w.'-]+)?)\s*(?:=|->|→|=>)\s*(?P<name>[^=<>]{2,80}?)\s*$")


def handle_key(text: str) -> str:
    return " ".join((text or "").strip().lstrip("@").casefold().split())


def parse_line(line: str) -> tuple[str, str] | None:
    match = _LINE.match(line or "")
    if not match:
        return None
    name = " ".join(match["name"].split()).strip(" ,;")
    return (handle_key(match["handle"]), name) if name else None


def parse(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        pair = parse_line(line)
        if pair:
            out[pair[0]] = pair[1]
    return out
