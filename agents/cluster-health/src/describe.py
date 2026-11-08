"""Pure: `kubectl describe pod|node` blocks, read by section headers and indentation.

Pod: `Name:`, `Namespace:`, `Status:`, each container's `State:` / `Last State:` with their `Reason:` and
`Exit Code:`, `Restart Count:`, and the `Events:` table. Node: `Name:`, `Unschedulable:` and the
`Conditions:` table (type, status). Evidence lines are kept verbatim (stripped of indentation)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import tables as t

_KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z][A-Za-z ]*?):(?:\s+(?P<value>.*))?$")
_EVENTS_HEADER = re.compile(r"^\s+Type\s+Reason\s+Age\s+From\s+Message\s*$")


@dataclass
class Container:
    name: str
    state: str = ""
    state_reason: str = ""
    last_state: str = ""
    last_reason: str = ""
    exit_code: str = ""
    restart_count: int = 0
    evidence: list[str] = field(default_factory=list)


@dataclass
class DescribedEvent:
    type: str
    reason: str
    message: str
    count: int
    line: str


@dataclass
class PodDetail:
    name: str = ""
    namespace: str = "default"
    status: str = ""
    containers: list[Container] = field(default_factory=list)
    events: list[DescribedEvent] = field(default_factory=list)
    name_line: str = ""

    def last_terminated(self) -> Container | None:
        return next((c for c in self.containers if c.last_reason), None)


@dataclass
class NodeDetail:
    name: str = ""
    unschedulable: bool = False
    conditions: dict[str, tuple[str, str]] = field(default_factory=dict)   # type → (status, line)


def pod(lines: Sequence[str]) -> PodDetail:
    out = PodDetail()
    section, container, sub = "", None, ""
    events_header = ""
    for raw in lines:
        line = raw.rstrip()
        if section == "Events" and events_header and line.startswith(" ") and line.strip():
            _event_row(out, events_header, line)
            continue
        if section == "Events" and _EVENTS_HEADER.match(line):
            events_header = line
            continue
        match = _KEY.match(line)
        if not match:
            continue
        indent, key, value = len(match.group("indent")), match.group("key"), (match.group("value") or "").strip()
        if indent == 0:
            section, container, sub = key, None, ""
            _top_level(out, key, value, line)
            continue
        if section not in ("Containers", "Init Containers"):
            continue
        if indent == 2 and not value:
            container = Container(key)
            out.containers.append(container)
        elif container is not None:
            sub = _container_line(container, indent, key, value, line.strip(), sub)
    return out


def _top_level(out: PodDetail, key: str, value: str, line: str) -> None:
    if key == "Name":
        out.name, out.name_line = value, line.strip()
    elif key == "Namespace":
        out.namespace = value
    elif key == "Status":
        out.status = value


def _container_line(c: Container, indent: int, key: str, value: str, line: str, sub: str) -> str:
    if key == "State":
        c.state = value
        return "State"
    if key == "Last State":
        c.last_state = value
        c.evidence.append(line)
        return "Last State"
    if key == "Restart Count":
        c.restart_count = t.as_int(value) or 0
        c.evidence.append(line)
        return ""
    if key == "Reason" and indent >= 6:
        if sub == "State":
            c.state_reason = value
        elif sub == "Last State":
            c.last_reason = value
            c.evidence.append(line)
    elif key == "Exit Code" and sub == "Last State":
        c.exit_code = value
        c.evidence.append(line)
    elif indent <= 4:
        return ""
    return sub


def _event_row(out: PodDetail, header: str, line: str) -> None:
    rows, _ = t.parse_rows(header, [line])
    if rows:
        row = rows[0]
        out.events.append(DescribedEvent(row.get("TYPE"), row.get("REASON"), row.get("MESSAGE"),
                                         t.times(row.get("AGE")), line.strip()))


def node(lines: Sequence[str]) -> NodeDetail:
    out = NodeDetail()
    section = ""
    for raw in lines:
        line = raw.rstrip()
        match = _KEY.match(line)
        if match and not match.group("indent"):
            section, value = match.group("key"), (match.group("value") or "").strip()
            if section == "Name":
                out.name = value
            elif section == "Unschedulable":
                out.unschedulable = value.casefold() == "true"
            continue
        if section == "Conditions" and line.startswith(" ") and line.strip():
            parts = line.split()
            if len(parts) >= 2 and parts[0] != "Type" and not parts[0].startswith("-"):
                out.conditions[parts[0]] = (parts[1], line.strip())
    return out
