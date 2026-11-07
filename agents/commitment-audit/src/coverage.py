"""Pure: whether a read step could actually use its source (tracker, document, chat), and the note
that says so. Decided from the offered tools and the tools dropped as unavailable, never from the
model's prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

_LABEL = {"tracker": "Tracker", "document": "Document", "chat": "Chat"}

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class StepCoverage:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(source: str, offered: Sequence[McpTool], dropped: Mapping[str, str],
           helpers: frozenset[str] = frozenset()) -> StepCoverage:
    """`missing` when the step had no tool, `unavailable` when every one was dropped, else `ok`."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return StepCoverage(source, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return StepCoverage(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return StepCoverage(source, "ok")


def note(coverage: StepCoverage) -> str:
    """One line for a source that could not be read, saying what that costs."""
    label = _LABEL.get(coverage.source, coverage.source.title())
    cost = (" Confidence is lower and chat risks are not shown." if coverage.source == "chat"
            else " Evidence from it is missing.")
    if coverage.state == "unavailable":
        return f"{label} search isn't available for your connection ({coverage.reason}).{cost}"
    return f"No {coverage.source} search tool is available to this agent: check that the connector is enabled.{cost}"
