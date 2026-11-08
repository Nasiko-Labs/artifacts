"""Pure: which sources (email, documents) the turn could actually read, and the one line to show for each
that it could not. Decided from the offered tools, the tools dropped as unavailable and the steps skipped
for time, never from the model's prose. Adapted from the promise-tracker pattern."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool
from prompts import NO_TOOLS_PASTE

SOURCES = ("mail", "drive")
_NOUN = {"mail": "email", "drive": "shared documents"}

State = Literal["ok", "missing", "unavailable", "skipped", "unreadable", "not_asked"]


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
        return tuple(s for s in self.statuses if s.state not in ("ok", "not_asked"))

    @property
    def covered(self) -> frozenset[str]:
        return frozenset(s.source for s in self.statuses if s.state == "ok")

    @property
    def all_failed(self) -> bool:
        return not self.covered


def assess(offered: Mapping[str, Sequence[McpTool]], dropped: Mapping[str, str],
           overrides: Mapping[str, State] | None = None, helpers: frozenset[str] = frozenset()) -> Coverage:
    """`overrides` carries a step's own outcome (skipped for time, unreadable answer, not asked)."""
    return Coverage(tuple(_status(s, [t for t in offered.get(s, ()) if t.name not in helpers], dropped,
                                  (overrides or {}).get(s)) for s in SOURCES))


def _status(source: str, own: Sequence[McpTool], dropped: Mapping[str, str], override: State | None) -> SourceStatus:
    if override == "not_asked":
        return SourceStatus(source, "not_asked")
    if not own or override == "missing":
        return SourceStatus(source, "missing", "no read tool is enabled for this agent")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, override or "ok")


def line(status: SourceStatus) -> str:
    noun = _NOUN[status.source]
    if status.state == "skipped":
        return f"I didn't finish reading {noun} (time limit)."
    if status.state == "unreadable":
        return f"I read {noun} but couldn't turn it into feedback I can check word for word, so it is left out."
    return f"I couldn't search {noun} ({status.reason}); {NO_TOOLS_PASTE[0].lower()}{NO_TOOLS_PASTE[1:]}"


def notes(coverage: Coverage) -> tuple[str, ...]:
    return tuple(line(s) for s in coverage.failed)
