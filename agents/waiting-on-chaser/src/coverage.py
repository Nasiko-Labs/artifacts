"""Pure: which sources (email, chat) the sweep could actually read, and what to tell the user when one
or both could not be searched. Decided from the offered tools and the tools dropped as unavailable,
never from the model's prose. (Copied in once from the hardened kit; owned by this agent; wording is
this agent's.)"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

SOURCES = ("email", "chat")
_LABEL = {"email": "Email", "chat": "Chat"}

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""


@dataclass(frozen=True)
class Coverage:
    statuses: tuple[SourceStatus, ...]
    any_working: bool  # some offered tool was never dropped

    @property
    def failed(self) -> tuple[SourceStatus, ...]:
        return tuple(s for s in self.statuses if s.state != "ok")

    @property
    def all_failed(self) -> bool:
        return not self.any_working or len(self.failed) == len(SOURCES)


def assess(offered: Sequence[McpTool], source_of: Callable[[McpTool], str | None],
           dropped: Mapping[str, str], helpers: frozenset[str] = frozenset()) -> Coverage:
    """A source is `missing` when no offered tool belongs to it (and no unclassified tool might),
    `unavailable` when every one of its tools was dropped, else `ok`."""
    tools = [t for t in offered if t.name not in helpers]
    unknown = [t for t in tools if source_of(t) not in SOURCES]
    statuses = tuple(_status(s, [t for t in tools if source_of(t) == s], unknown, dropped) for s in SOURCES)
    return Coverage(statuses, any(t.name not in dropped for t in tools))


def _status(source: str, own: Sequence[McpTool], unknown: Sequence[McpTool],
            dropped: Mapping[str, str]) -> SourceStatus:
    if not own:
        return SourceStatus(source, "ok" if unknown else "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def notes(coverage: Coverage, wanted: Sequence[str] = SOURCES) -> tuple[str, ...]:
    """One line per wanted source that could not be searched, saying what the results cover."""
    failed = tuple(s for s in coverage.failed if s.source in wanted)
    if len(failed) != 1 or len(wanted) != len(SOURCES):
        return tuple(_line(s) for s in failed)
    other = next(s for s in SOURCES if s != failed[0].source)
    return (f"{_line(failed[0])} Results cover {other} only.",)


def all_failed_reply(coverage: Coverage) -> str:
    lines = [_line(s) for s in coverage.failed] or ["Every search tool I tried failed for your connection."]
    return ("I couldn't search your email or your chat, so I can't check who you're waiting on, and I won't "
            "guess from memory.\n\n" + "\n".join(f"- {line}" for line in lines))


def _line(status: SourceStatus) -> str:
    label = _LABEL[status.source]
    if status.state == "unavailable":
        return f"{label} search isn't available for your connection ({status.reason})."
    return (f"No {status.source} search tool is available to this agent: check that the {status.source} "
            "connector is enabled for it.")
