"""Pure: which context sources (email, chat, documents, tickets) the cards could actually read, and the
one line to show for each that could not. Decided from the offered tools and the tools dropped as
unavailable, never from the model's prose. Adapted from the promise-tracker agent; owned here."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

CONTEXT_SOURCES = ("email", "chat", "document", "ticket")
LINK_SOURCES = ("document", "ticket")
_LABEL = {"email": "Email", "chat": "Chat", "document": "Documents", "ticket": "Tickets"}
_NOUN = {"email": "email", "chat": "chat", "document": "document", "ticket": "tracker"}

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""


def assess(offered: Sequence[McpTool], source_of: Callable[[McpTool], str | None], dropped: Mapping[str, str],
           sources: Sequence[str] = CONTEXT_SOURCES,
           helpers: frozenset[str] = frozenset()) -> tuple[SourceStatus, ...]:
    """A source is `missing` when no offered tool belongs to it (and no unclassified tool might),
    `unavailable` when every one of its tools was dropped, else `ok`."""
    tools = [t for t in offered if t.name not in helpers]
    unknown = [t for t in tools if source_of(t) not in (*CONTEXT_SOURCES, "calendar")]
    return tuple(_status(s, [t for t in tools if source_of(t) == s], unknown, dropped) for s in sources)


def _status(source: str, own: Sequence[McpTool], unknown: Sequence[McpTool],
            dropped: Mapping[str, str]) -> SourceStatus:
    if not own:
        return SourceStatus(source, "ok" if unknown else "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def notes(statuses: Sequence[SourceStatus], *, links_seen: bool) -> tuple[str, ...]:
    """One line per source that could not be read, then what the cards do cover. Document and ticket
    gaps are mentioned only when a meeting actually linked something."""
    failed = [s for s in statuses if s.state != "ok" and (links_seen or s.source not in LINK_SOURCES)]
    if not failed:
        return ()
    covered = ["calendar", *(s.source for s in statuses if s.state == "ok")]
    lines = [_line(s) for s in failed]
    return (*lines, f"Cards cover {_join(covered)} only.")


def _line(status: SourceStatus) -> str:
    label = _LABEL[status.source]
    if status.state == "unavailable":
        return f"{label} isn't available for your connection ({status.reason})."
    noun = _NOUN[status.source]
    return f"No {noun} read tool is available to this agent: check that the {noun} connector is enabled for it."


def _join(words: Sequence[str]) -> str:
    plural = {"document": "documents", "ticket": "tickets"}
    named = [plural.get(w, w) for w in words]
    return named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"
