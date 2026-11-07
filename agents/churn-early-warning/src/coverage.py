"""Pure: which sources (payments, email, support desk) this turn could actually read, and what to tell
the user about one that could not be read. Decided from the offered tools and the tools dropped as
unavailable, never from the model's prose. (Copied in once from the hardened kit; owned by this agent;
three sources and the wording are this agent's.)"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

SOURCES = ("payments", "email", "support")

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""


def assess(offered: Sequence[McpTool], source_of: Callable[[McpTool], str | None],
           dropped: Mapping[str, str], helpers: frozenset[str] = frozenset()) -> dict[str, SourceStatus]:
    """A source is `missing` when no offered tool belongs to it, `unavailable` when every one of its
    tools was dropped, else `ok`. Tools no rule places count for no source."""
    tools = [t for t in offered if t.name not in helpers]
    return {s: _status(s, [t for t in tools if source_of(t) == s], dropped) for s in SOURCES}


def _status(source: str, own: Sequence[McpTool], dropped: Mapping[str, str]) -> SourceStatus:
    if not own:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def reason(status: SourceStatus) -> str:
    """Why a source was not read, short enough for the footer: "not connected" / "unavailable: 403"."""
    return f"unavailable: {status.reason}" if status.state == "unavailable" else "not connected"


def payments_refused(status: SourceStatus) -> str:
    return (f"Your payments connection refused access ({status.reason}), so I can't read subscriptions and "
            "I won't guess from memory. Check that the payments connector has read access to subscriptions, "
            "customers, invoices and events.")
