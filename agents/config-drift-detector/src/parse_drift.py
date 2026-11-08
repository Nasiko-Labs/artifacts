"""Pure: a pasted `terraform plan -refresh-only` report (or a plan made from unchanged code) into
per-resource before/after trees.

Each `# <address> has changed` block is split line by line into the text before the change and the text
after it (`~ key = old -> new` goes to both sides, `-` lines to before only, `+` lines to after only,
including the closing brackets of a removed or added object), and each side is read by `hcl_lite`.
`(known after apply)` and `(sensitive value)` become `Unresolved` and are skipped and counted.

In a refresh-only report the before side is the last known state (expected) and the after side is live
(actual). In a normal plan (`will be updated in-place`) the order is reversed: before is the refreshed
live state and after is the configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import hcl_lite

_HEADER = re.compile(r"^\s*#\s+(\S+)\s+(has changed|has been deleted|will be updated in-place)\s*$")
_SENSITIVE_CHANGE = re.compile(r"^\s*~\s+\"?([\w.\-]+)\"?\s*=\s*\(sensitive value\)")
_MARKER = re.compile(r"^(\s*)(-/\+|\+/-|[~+\-])\s(.*)$")
_IDS = ("id", "arn", "name", "bucket", "identifier", "function_name", "cluster_name", "db_instance_identifier")
_OPEN, _CLOSE = "{[(", "}])"
REFRESH, PLANNED = "refresh", "planned"


@dataclass(frozen=True)
class ResourceChange:
    address: str
    mode: str                 # refresh (has changed / has been deleted) | planned (will be updated)
    expected: dict | None     # None when the resource could not be read
    actual: dict | None       # None when the resource is gone live
    deleted: bool = False
    sensitive: tuple[str, ...] = ()  # attributes shown as `~ key = (sensitive value)`: changed, values hidden

    @property
    def resource_type(self) -> str:
        parts = [p for p in self.address.split(".") if p not in ("data",) and not p.startswith("module")]
        return parts[-2] if len(parts) >= 2 else self.address

    def identifiers(self) -> tuple[str, ...]:
        """Ids, ARNs and names the resource carries: what audit events are matched on."""
        out = []
        for side in (self.expected or {}, self.actual or {}):
            for key in _IDS:
                value = side.get(key)
                if isinstance(value, str) and len(value) >= 3:
                    out.append(value)
        name = self.address.rsplit(".", 1)[-1].split("[")[0]
        return tuple(dict.fromkeys(out)) or ((name,) if len(name) >= 3 else ())


@dataclass(frozen=True)
class Report:
    changes: tuple[ResourceChange, ...]
    headers: int              # how many change headers the paste had (cross-check)
    unreadable: tuple[str, ...] = ()
    planned_only: bool = False  # only `will be updated` blocks: drift only if the code was unchanged


def is_report(text: str) -> bool:
    return any(_HEADER.match(line) for line in (text or "").splitlines())


def parse(text: str, unchanged_code: bool = False) -> Report:
    blocks = _blocks(text or "")
    refresh = [b for b in blocks if b[1] != "will be updated in-place"]
    use = refresh or (blocks if unchanged_code else [])
    planned_only = not refresh and bool(blocks) and not unchanged_code
    changes, unreadable = [], []
    for address, verb, lines in use:
        change = _change(address, verb, lines)
        (changes if change else unreadable).append(change or address)
    return Report(tuple(changes), len(use), tuple(unreadable), planned_only)


def _blocks(text: str) -> list[tuple[str, str, list[str]]]:
    out: list[tuple[str, str, list[str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _HEADER.match(lines[i])
        if not m:
            i += 1
            continue
        body, depth, started = [], 0, False
        i += 1
        while i < len(lines) and not _HEADER.match(lines[i]):
            line = lines[i]
            if not started and not line.strip():
                i += 1
                continue
            body.append(line)
            depth += _net(line)
            started = True
            i += 1
            if started and depth <= 0:
                break
        out.append((m.group(1), m.group(2), body))
    return out


def _change(address: str, verb: str, lines: list[str]) -> ResourceChange | None:
    before, after = split_sides(lines)
    old, new = _body(before), _body(after)
    if verb == "has been deleted":
        return ResourceChange(address, REFRESH, old, None, deleted=True) if old is not None else None
    if old is None or new is None:
        return None
    hidden = tuple(m.group(1) for line in lines if (m := _SENSITIVE_CHANGE.match(line)))
    if verb == "will be updated in-place":
        return ResourceChange(address, PLANNED, expected=new, actual=old, sensitive=hidden)
    return ResourceChange(address, REFRESH, expected=old, actual=new, sensitive=hidden)


def _body(text: str) -> dict | None:
    blocks = hcl_lite.parse(text)
    return dict(blocks[0].body) if blocks else None


def split_sides(lines: list[str]) -> tuple[str, str]:
    before: list[str] = []
    after: list[str] = []
    stack: list[tuple[str, int]] = []  # (side, depth when the removed/added structure opened)
    depth = 0
    for line in lines:
        m = _MARKER.match(line)
        marker, rest = (m.group(2), m.group(1) + "  " + m.group(3)) if m else ("", line)
        net = _net(rest)
        if stack:
            side = stack[-1][0]
        elif marker in ("-", "+") and net > 0:
            side = marker
            stack.append((marker, depth))
        else:
            side = marker if marker in ("-", "+") else "~"
        old, new = _arrow(rest)
        if side in ("~", "-"):
            before.append(old)
        if side in ("~", "+"):
            after.append(new)
        depth += net
        if stack and depth <= stack[-1][1]:
            stack.pop()
    return "\n".join(before), "\n".join(after)


def _arrow(rest: str) -> tuple[str, str]:
    """`key = old -> new` into (`key = old`, `key = new`); anything else unchanged on both sides."""
    at = _outside_quotes(rest, " -> ")
    if at == -1:
        return rest, rest
    eq = _outside_quotes(rest, "=")
    head = rest[:eq + 1] + " " if -1 < eq < at else ""
    return rest[:at], head + rest[at + 4:].strip()


def _outside_quotes(text: str, needle: str) -> int:
    quoted, i = False, 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quoted:
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        elif not quoted and text.startswith(needle, i):
            return i
        i += 1
    return -1


def _net(line: str) -> int:
    """Brackets opened minus closed on one line, outside strings and comments."""
    quoted, total, i = False, 0, 0
    while i < len(line):
        c = line[i]
        if c == "\\" and quoted:
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        elif not quoted and c == "#":
            break
        elif not quoted:
            total += (c in _OPEN) - (c in _CLOSE)
        i += 1
    return total


def value_at(tree: Any, path: tuple) -> tuple[bool, Any]:
    """The value at `path` inside a parsed body (keys matched as written)."""
    node = tree
    for part in path:
        if isinstance(part, int) and isinstance(node, list) and part < len(node) or isinstance(part, str) and isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False, None
    return True, node
