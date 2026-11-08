"""Pure: which sources (code host, chat) the owner lookup could actually read, and the one coverage line
for each it could not. Decided from the offered tools and the tools dropped as unavailable, never from
the model's prose. (Copied in once from the hardened kit; owned by this agent; sources and wording are
this agent's.)"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

SOURCES = ("code", "chat", "email")
_LINES = {
    "code": "Code host: {why}, so owners come from tags only; the rest are unknown.",
    "chat": "Chat: {why}, so tag owners were not matched to chat users; drafts go by email or as text to copy.",
}

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""


def assess(offered: Sequence[McpTool], placed: Callable[[McpTool], str | None],
           dropped: Mapping[str, str], helpers: frozenset[str] = frozenset()) -> dict[str, SourceStatus]:
    """A source is `missing` when no offered tool is placed on it, `unavailable` when every one of its
    tools was dropped, else `ok`."""
    tools = [t for t in offered if t.name not in helpers]
    return {s: _status(s, [t for t in tools if placed(t) == s], dropped) for s in SOURCES}


def _status(source: str, own: Sequence[McpTool], dropped: Mapping[str, str]) -> SourceStatus:
    if not own:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def reason(status: SourceStatus) -> str:
    return f"unavailable ({status.reason})" if status.state == "unavailable" else "not connected"


def notes(statuses: Mapping[str, SourceStatus], wanted: Sequence[str]) -> tuple[str, ...]:
    """One line per wanted source (code, chat) that could not be read."""
    return tuple(_LINES[s].format(why=reason(statuses[s])) for s in wanted
                 if s in _LINES and statuses[s].state != "ok")
