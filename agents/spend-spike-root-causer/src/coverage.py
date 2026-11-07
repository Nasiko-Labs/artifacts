"""Pure: whether a lookup's source (code host, chat) could actually be read, and the one-line note to show when
it could not. Decided from the offered tools and the tools dropped as unavailable, never from the model's prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable"]

_WHAT = {
    "code": ("code-host", "deploy correlation not checked"),
    "chat": ("chat", "the owner was not looked up in chat"),
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
    """`missing` when no tool was offered, `unavailable` when every offered tool was dropped, else `ok`."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def note(status: SourceStatus) -> str | None:
    if status.ok:
        return None
    label, effect = _WHAT[status.source]
    if status.state == "unavailable":
        return f"{label.capitalize()} reads aren't available for your connection ({status.reason}): {effect}."
    return f"No {label} tool is available to this agent: {effect} (enable the {label} connector for it)."


NO_SEND_TOOL = "No chat post tool is available to this agent, so here is the message to copy:"
SEND_BLOCKED = "Posting is blocked for this agent, so here is the message to copy:"
