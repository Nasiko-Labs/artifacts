"""Pure: what the user asked to audit, read in code: the quarter, where the commitments come from
(tracker items carrying a label or field, or a linked plan), and an optional team filter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from quarter import Quarter, parse_quarter

DEFAULT_LABEL = "committed"
Source = Literal["tracker", "plan"]

_LINK = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_LABEL = re.compile(r"\blabel(?:l?ed)?\s+(?:as\s+)?[`'\"]?([A-Za-z0-9][\w.-]*)", re.IGNORECASE)
_FIELD = re.compile(r"\bfield\s+[`'\"]([^`'\"]{1,60})[`'\"]", re.IGNORECASE)
_TEAM_AFTER = re.compile(r"\b(?:only|just)\s+(?:for\s+)?(?:the\s+)?team\s+([A-Za-z0-9][\w-]*(?: [A-Za-z0-9][\w-]*)?)",
                         re.IGNORECASE)
_TEAM_BEFORE = re.compile(r"\b(?:only|just)\s+(?:for\s+)?(?:the\s+)?([A-Za-z0-9][\w-]*)\s+team\b", re.IGNORECASE)
_TRAILING = ".,;:!?)"


@dataclass(frozen=True)
class AuditRequest:
    quarter: Quarter
    source: Source
    plan_link: str | None
    marker_kind: Literal["label", "field"]
    marker: str
    team: str | None


def parse_request(text: str, today: date, start_month: int) -> AuditRequest:
    message = text or ""
    link = _link(message)
    field = _FIELD.search(message)
    label = _LABEL.search(message)
    kind, marker = ("field", field.group(1).strip()) if field else ("label", label.group(1) if label else DEFAULT_LABEL)
    return AuditRequest(quarter=parse_quarter(message, today, start_month), source="plan" if link else "tracker",
                        plan_link=link, marker_kind=kind, marker=marker, team=_team(message))


def _link(message: str) -> str | None:
    match = _LINK.search(message)
    return match.group(0).rstrip(_TRAILING) if match else None


def _team(message: str) -> str | None:
    match = _TEAM_AFTER.search(message) or _TEAM_BEFORE.search(message)
    return match.group(1).strip() if match else None
