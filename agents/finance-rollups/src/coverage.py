"""Pure: whether the payments source could actually be read this turn, and the one line to tell the user when it
could not. Decided from the offered tools and the tools dropped as unavailable, never from the model's prose.
(Adapted from the hardened kit; owned by this agent.)"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(offered: Sequence[McpTool], dropped: Mapping[str, str],
           helpers: frozenset[str] = frozenset()) -> SourceStatus:
    """`missing` when no payments tool was offered, `unavailable` when every one was dropped for this user."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return SourceStatus("missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return SourceStatus("unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus("ok")


def line(status: SourceStatus) -> str:
    if status.state == "unavailable":
        return f"Payments access isn't available for your connection ({status.reason})."
    return "No payments tool is available to this agent: check that the payments connector is enabled for it."
