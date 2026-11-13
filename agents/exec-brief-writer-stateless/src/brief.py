"""Pure: the brief's data model and reading the model's JSON into it (leniently; code checks follow).

    {"status": "ok" | "off_topic" | "nothing_to_brief",
     "headline": {"text", "cites": [{"section", "snippet"}]},
     "risks": [{"text", "severity", "cites"}],
     "decisions": [{"text", "owner", "by", "cites"}]}
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

STATUSES = frozenset({"ok", "off_topic", "nothing_to_brief"})
SEVERITIES = ("high", "medium", "low")
DEFAULT_SEVERITY = "medium"


@dataclass(frozen=True)
class Cite:
    section: str
    snippet: str


@dataclass(frozen=True)
class Line:
    text: str
    cites: tuple[Cite, ...] = ()
    severity: str = ""
    owner: str = ""
    by: str = ""


@dataclass(frozen=True)
class Brief:
    status: str
    headline: Line | None = None
    risks: tuple[Line, ...] = ()
    decisions: tuple[Line, ...] = ()
    notes: tuple[str, ...] = field(default=())

    def with_lines(self, risks: tuple[Line, ...] | None = None, decisions: tuple[Line, ...] | None = None) -> Brief:
        return replace(self, risks=self.risks if risks is None else risks,
                       decisions=self.decisions if decisions is None else decisions)


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _cites(value: Any) -> tuple[Cite, ...]:
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        if isinstance(item, Mapping):
            section, snippet = _text(item.get("section")).upper(), _text(item.get("snippet"))
            if section and snippet:
                out.append(Cite(section, snippet))
    return tuple(out)


def _severity(value: Any) -> str:
    text = _text(value).lower()
    return text if text in SEVERITIES else DEFAULT_SEVERITY


def _line(item: Any, kind: str) -> Line | None:
    if not isinstance(item, Mapping) or not _text(item.get("text")):
        return None
    base = Line(_text(item.get("text")), _cites(item.get("cites")))
    if kind == "risk":
        return replace(base, severity=_severity(item.get("severity")))
    if kind == "decision":
        return replace(base, owner=_text(item.get("owner")), by=_text(item.get("by")))
    return base


def parse_lines(value: Any, kind: str) -> tuple[Line, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(line for line in (_line(item, kind) for item in value) if line is not None)


def parse(data: Mapping[str, Any] | None) -> Brief | None:
    """The model's object as a Brief, or None when it is not a brief at all (then repair)."""
    if not isinstance(data, Mapping):
        return None
    status = _text(data.get("status")).lower()
    if status not in STATUSES:
        return None
    if status == "off_topic":
        return Brief("off_topic")
    return Brief(status, _line(data.get("headline"), "headline"), parse_lines(data.get("risks"), "risk"),
                 parse_lines(data.get("decisions"), "decision"))
