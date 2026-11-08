"""Pure: which sources (tracker, chat, calendar) the turn could actually read, and the one line to show
for each that it could not. Decided from the offered tools, the tools dropped as unavailable and the
steps skipped for time, never from the model's prose. Adapted from the promise-tracker pattern."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

SOURCES = ("tracker", "chat", "calendar")
LABEL = {"tracker": "Tracker", "chat": "Chat", "calendar": "Calendar"}
_EFFECT = {
    "tracker": "",
    "chat": " The reality check did not run (no reported statuses); paste last week's status update to run it.",
    "calendar": " \"Next week\" uses due dates only.",
}

State = Literal["ok", "missing", "unavailable", "skipped", "unreadable", "pasted"]


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
        return tuple(s for s in self.statuses if s.state not in ("ok", "pasted"))

    @property
    def read(self) -> frozenset[str]:
        return frozenset(s.source for s in self.statuses if s.state == "ok")

    def state(self, source: str) -> State:
        return next(s.state for s in self.statuses if s.source == source)


def assess(offered: Mapping[str, Sequence[McpTool]], dropped: Mapping[str, str],
           overrides: Mapping[str, State] | None = None, helpers: frozenset[str] = frozenset()) -> Coverage:
    """`overrides` carries a step's own outcome (skipped for time, unreadable answer, pasted instead)."""
    return Coverage(tuple(_status(s, [t for t in offered.get(s, ()) if t.name not in helpers], dropped,
                                  (overrides or {}).get(s)) for s in SOURCES))


def _status(source: str, own: Sequence[McpTool], dropped: Mapping[str, str], override: State | None) -> SourceStatus:
    if override == "pasted":
        return SourceStatus(source, "pasted")
    if not own:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, override or "ok")


def line(status: SourceStatus) -> str:
    label, effect = LABEL[status.source], _EFFECT[status.source]
    if status.state == "unavailable":
        return f"{label} isn't available for your connection ({status.reason}).{effect}"
    if status.state == "skipped":
        return f"{label} was not read (time limit).{effect}"
    if status.state == "unreadable":
        return f"{label} results could not be turned into records I can check, so they are left out.{effect}"
    noun = status.source
    return f"No {noun} read tool is available to this agent: check that a {noun} connector is enabled for it.{effect}"


def notes(coverage: Coverage) -> tuple[str, ...]:
    return tuple(line(s) for s in coverage.failed)


def heading(coverage: Coverage) -> str:
    """`tracker, chat, calendar all read` / `tracker and calendar read; chat not read`."""
    read = [s for s in SOURCES if s in coverage.read]
    if coverage.state("chat") == "pasted":
        read = [*read, "your pasted update"]
    missing = [s.source for s in coverage.failed]
    if not missing:
        return f"{_join(read)} all read" if len(read) > 1 else f"{_join(read)} read"
    return f"{_join(read) or 'nothing'} read; {_join(missing)} not read"


def _join(words: Sequence[str]) -> str:
    if not words:
        return ""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
