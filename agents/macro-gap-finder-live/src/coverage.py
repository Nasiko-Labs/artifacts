"""Pure: whether each desk step (tickets, macros) was actually read, decided from the tools offered, the tools
dropped as unavailable and the calls that returned data, never from the model's prose."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable", "unread"]
_LABEL = {"tickets": "Solved tickets", "macros": "Existing macros"}


@dataclass(frozen=True)
class StepStatus:
    step: str
    state: State
    reason: str = ""

    @property
    def read(self) -> bool:
        return self.state == "ok"


def assess(step: str, offered: Sequence[McpTool], dropped: Mapping[str, str], succeeded: Collection[str],
           said_no_tool: bool, helpers: frozenset[str] = frozenset()) -> StepStatus:
    """`missing`: nothing offered; `unavailable`: every offered tool dropped; `unread`: no call returned data
    (or the model said no offered tool fits); else `ok`."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return StepStatus(step, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return StepStatus(step, "unavailable", ", ".join(dict.fromkeys(reasons)))
    names = {t.name for t in tools}
    if said_no_tool or not names & set(succeeded):
        return StepStatus(step, "unread", ", ".join(dict.fromkeys(reasons)))
    return StepStatus(step, "ok")


def line(status: StepStatus) -> str:
    label = _LABEL.get(status.step, status.step)
    if status.state == "unavailable":
        return f"{label}: not read, the tool isn't available for your connection ({status.reason})."
    if status.state == "missing":
        return f"{label}: not read, no tool for it is enabled for this agent."
    why = f" (a tool failed: {status.reason})" if status.reason else ""
    return f"{label}: not read, none of the offered tools returned them{why}."
