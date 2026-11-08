"""Pure: which sources (calendar, docs, chat) the turn could actually read, and the one line to show
for each that it could not. Decided from the offered tools, the tools dropped as unavailable and the
steps skipped for time, never from the model's prose. Adapted from the promise-tracker pattern."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

SOURCES = ("calendar", "docs", "chat")
LABEL = {"calendar": "Calendar", "docs": "Notes (documents)", "chat": "Chat"}
_NOUN = {"calendar": "calendar", "docs": "document", "chat": "chat"}
_EFFECT = {"calendar": "", "docs": " Notes and decisions are shown as not available, and no cancel is proposed.",
           "chat": " Follow-up is shown as not available."}

State = Literal["ok", "missing", "unavailable", "skipped", "unreadable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""


@dataclass(frozen=True)
class Coverage:
    statuses: tuple[SourceStatus, ...]

    @property
    def failed(self) -> tuple[SourceStatus, ...]:
        return tuple(s for s in self.statuses if s.state != "ok")

    @property
    def covered(self) -> frozenset[str]:
        return frozenset(s.source for s in self.statuses if s.state == "ok")

    @property
    def all_failed(self) -> bool:
        return not self.covered


def assess(offered: Mapping[str, Sequence[McpTool]], dropped: Mapping[str, str],
           overrides: Mapping[str, State] | None = None, helpers: frozenset[str] = frozenset()) -> Coverage:
    """`overrides` carries a step's own outcome (skipped for time, unreadable answer)."""
    return Coverage(tuple(_status(s, [t for t in offered.get(s, ()) if t.name not in helpers], dropped,
                                  (overrides or {}).get(s)) for s in SOURCES))


def _status(source: str, own: Sequence[McpTool], dropped: Mapping[str, str], override: State | None) -> SourceStatus:
    if not own:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, override or "ok")


def notes(coverage: Coverage) -> tuple[str, ...]:
    """One line per source that could not be read, saying what that leaves out."""
    return tuple(line(s) + _EFFECT[s.source] for s in coverage.failed)


def line(status: SourceStatus) -> str:
    label, noun = LABEL[status.source], _NOUN[status.source]
    if status.state == "unavailable":
        return f"{label} search isn't available for your connection ({status.reason})."
    if status.state == "skipped":
        return f"{label}: not computed (time limit)."
    if status.state == "unreadable":
        return f"{label} results could not be turned into records I can check, so they are left out."
    return f"No {noun} read tool is available to this agent: check that a {noun} connector is enabled for it."
