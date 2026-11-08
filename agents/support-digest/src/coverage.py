"""Pure: whether a step could reach its source (the support desk), and the one line the
user gets when it could not. Decided from the offered tools and the tools dropped as unavailable,
never from the model's prose. Adapted from promise-tracker to one source."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

LABELS = {"desk": "support desk"}

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(source: str, offered: Sequence[McpTool], source_of: Callable[[McpTool], str | None],
           dropped: Mapping[str, str] | None = None) -> SourceStatus:
    """`missing` when no offered tool belongs to the source (and none is unplaced, which might),
    `unavailable` when every one of its tools was dropped this turn, else `ok`."""
    dropped = dropped or {}
    own = [t for t in offered if source_of(t) == source]
    unplaced = [t for t in offered if source_of(t) not in LABELS]
    if not own and not unplaced:
        return SourceStatus(source, "missing")
    candidates = own or unplaced
    reasons = [dropped[t.name] for t in candidates if t.name in dropped]
    if len(reasons) == len(candidates):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    label = LABELS.get(status.source, status.source)
    if status.state == "unavailable":
        return f"The {label} isn't available for your connection ({status.reason})."
    if status.state == "missing":
        return f"No {label} tool is available to this agent: check that the {label} connector is enabled for it."
    return ""
