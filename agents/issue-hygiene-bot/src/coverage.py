"""Pure: whether a source (tracker, chat) could actually be read this turn, and the one line the user gets
when it could not. Decided from the offered tools and the tools dropped as unavailable, never from the
model's prose. (Adapted from the hardened kit; owned by this agent.)"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable"]
_LABEL = {"tracker": "tracker", "chat": "chat"}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(source: str, offered: Sequence[McpTool], dropped: Mapping[str, str]) -> SourceStatus:
    """`missing` when no tool was offered for the source, `unavailable` when every one was dropped."""
    if not offered:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in offered if t.name in dropped]
    if len(reasons) == len(offered):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    label = _LABEL.get(status.source, status.source)
    if status.state == "unavailable":
        return f"The {label} isn't available for your connection ({status.reason}), so it was not read."
    if status.state == "missing":
        return f"No {label} tool is available to this agent, so the {label} was not read."
    return ""
