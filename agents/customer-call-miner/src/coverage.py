"""Pure: whether a source (call recordings, CRM, chat) could actually be read this turn, and the one line
the user gets when it could not. Decided from the offered tools and the tools dropped as unavailable,
never from the model's prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable"]
_LABEL = {"calls": "call-recording", "crm": "CRM", "chat": "chat"}
_EFFECT = {
    "calls": "I can't read your customer calls.",
    "crm": "Themes are shown without ARR (not available) and no CRM notes are offered.",
    "chat": "The digest is shown for you to copy instead of being posted.",
}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(source: str, offered: Sequence[McpTool], dropped: Mapping[str, str],
           helpers: frozenset[str] = frozenset()) -> SourceStatus:
    """`missing` when no tool is offered for the source, `unavailable` when every one was dropped."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    label = _LABEL[status.source]
    if status.state == "unavailable":
        head = f"The {label} tools aren't available for your connection ({status.reason})."
    else:
        head = f"No {label} tool is available to this agent: check that the {label} connector is enabled for it."
    return f"{head} {_EFFECT[status.source]}"
