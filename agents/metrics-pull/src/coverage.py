"""Pure: whether a source (spreadsheet) could actually be read this turn, and the one line to
tell the user when it could not. Decided from the offered tools and the tools dropped as unavailable,
never from the model's prose. (Copied in once from the hardened kit; owned by this agent; wording and
sources are this agent's.)"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

_LABEL = {"spreadsheet": "Spreadsheets", "database": "Databases"}

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
           dropped: Mapping[str, str], helpers: frozenset[str] = frozenset()) -> SourceStatus:
    """`missing` when no offered tool belongs to the source, `unavailable` when every one of them was
    dropped for this user, else `ok`."""
    own = [t for t in offered if t.name not in helpers and source_of(t) == source]
    if not own:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in own if t.name in dropped]
    if len(reasons) == len(own):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    label = _LABEL.get(status.source, status.source)
    if status.state == "unavailable":
        return f"{label} isn't available for your connection ({status.reason})."
    return (f"No {status.source} tool is available to this agent: check that the {status.source} connector "
            "is enabled for it.")
