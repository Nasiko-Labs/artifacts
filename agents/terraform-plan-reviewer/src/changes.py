"""Pure: the records a parsed plan is made of, the address grammar, and value helpers the rules share.

A `Change` is one resource instance the plan acts on. Attribute values are kept as the plan printed them
(strings, quotes removed); `None` means absent or null. Unknown and sensitive values are never compared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ACTIONS = ("create", "update", "delete", "replace", "read", "noop", "import", "move", "forget")
UNKNOWN = "(known after apply)"
SENSITIVE = "(sensitive)"
TRUE_VALUES = frozenset({"true"})

_INDEX = re.compile(r"\[[^\]]*\]$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Attr:
    name: str
    before: str | None = None
    after: str | None = None
    forces: bool = False
    line: str = ""   # the verbatim plan line (human) or the JSON key path (show-json)


@dataclass(frozen=True)
class Change:
    address: str
    resource_type: str
    action: str
    mode: str = "managed"
    module_path: str = ""
    reason: str = ""
    cbd: bool = False                        # create-before-destroy replace
    attrs: tuple[Attr, ...] = ()
    before_text: str = ""                    # every before value, for pattern rules (exposure, IAM)
    after_text: str = ""
    evidence: str = ""                       # the header line (human) or "resource_changes[address]"

    def attr(self, name: str) -> Attr | None:
        for attr in self.attrs:
            if attr.name == name:
                return attr
        return None

    def attrs_named(self, *names: str) -> tuple[Attr, ...]:
        wanted = set(names)
        return tuple(a for a in self.attrs if a.name in wanted or a.name.rsplit(".", 1)[-1] in wanted)

    @property
    def is_destructive(self) -> bool:
        return self.action in ("delete", "replace")


@dataclass(frozen=True)
class ParsedPlan:
    format: str                              # human | show-json | stream-json
    changes: tuple[Change, ...] = ()
    drift: tuple[str, ...] = ()
    stated: dict | None = None               # the plan's own counts: add/change/destroy/import
    caveats: tuple[str, ...] = ()            # integrity notes (PL01)
    soft_notes: tuple[str, ...] = ()         # notes that do not lower confidence
    hard: bool = False                       # partial parse: confidence low
    has_attributes: bool = True
    empty: bool = False                      # "No changes."
    workspace: str = ""
    raw_lines: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Finding:
    id: str
    rule_id: str
    severity: str                            # high | medium | low
    address: str
    action: str
    reason: str
    question: str
    evidence: str
    attribute: str = ""
    before: str | None = None
    after: str | None = None
    blocking: bool = False                   # prod guardrail: only a confirmation lifts it
    confirmed: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "rule_id": self.rule_id, "severity": self.severity, "address": self.address,
                "action": self.action, "attribute": self.attribute, "before": self.before, "after": self.after,
                "reason": self.reason, "question": self.question, "evidence": self.evidence,
                "blocking": self.blocking, "confirmed": self.confirmed}

    @staticmethod
    def from_dict(data: dict) -> Finding:
        keys = Finding.__dataclass_fields__
        return Finding(**{k: v for k, v in data.items() if k in keys})


def split_address(address: str) -> tuple[str, str, str, str]:
    """(module_path, mode, type, name) from `module.a.module.b.data.type.name["key"]`."""
    text = address.strip()
    segments: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "." and depth == 0:
            segments.append(text[start:i])
            start = i + 1
    segments.append(text[start:])
    module: list[str] = []
    while len(segments) >= 2 and segments[0] == "module":
        module.append(f"module.{segments[1]}")
        segments = segments[2:]
    mode = "managed"
    if segments and segments[0] == "data":
        mode, segments = "data", segments[1:]
    rtype = _INDEX.sub("", segments[0]) if segments else ""
    name = segments[1] if len(segments) > 1 else ""
    return ".".join(module), mode, rtype, name


def known(value: str | None) -> bool:
    return value is not None and value not in (UNKNOWN, SENSITIVE)


def is_true(value: str | None) -> bool:
    return (value or "").strip().casefold() in TRUE_VALUES


def number(value: str | None) -> float | None:
    text = (value or "").strip()
    return float(text) if _NUMBER.match(text) else None


def turned_true(attr: Attr | None) -> bool:
    return attr is not None and known(attr.after) and is_true(attr.after) and not is_true(attr.before)


def turned_false(attr: Attr | None) -> bool:
    """true before, explicitly false (or removed) after."""
    if attr is None or not is_true(attr.before) or attr.after == UNKNOWN:
        return False
    return attr.after is None or (attr.after or "").casefold() == "false"


def changed(attr: Attr | None) -> bool:
    return attr is not None and known(attr.before) and known(attr.after) and attr.before != attr.after


def make(rule: str, severity: str, change: Change, reason: str, question: str, attr: Attr | None = None,
         blocking: bool = False) -> Finding:
    return Finding(id="", rule_id=rule, severity=severity, address=change.address, action=change.action,
                   reason=reason, question=question, evidence=(attr.line if attr and attr.line else change.evidence),
                   attribute=attr.name if attr else "", before=attr.before if attr else None,
                   after=attr.after if attr else None, blocking=blocking)
