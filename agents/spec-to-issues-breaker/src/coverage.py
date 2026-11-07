"""Pure: whether a step could reach its source (document store, tracker), and the one line the user gets when
it could not. Decided from the offered tools and the tools dropped as unavailable, never from the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

LABELS = {"docs": "document", "tracker": "tracker"}

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(source: str, tools: Sequence[McpTool], dropped: Mapping[str, str] | None = None) -> SourceStatus:
    """`missing` when the step has no tool for the source, `unavailable` when every one was dropped this turn."""
    dropped = dropped or {}
    if not tools:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    label = LABELS.get(status.source, status.source)
    if status.state == "unavailable":
        return f"The {label} tools aren't available for your connection ({status.reason})."
    if status.state == "missing":
        return f"No {label} tool is available to this agent: check that the {label} connector is enabled for it."
    return ""
