"""Pure: whether a source (engineering, docs, support, marketing) could actually be read this turn, and the
one coverage line for a source that could not. Decided from the offered tools and the tools dropped as
unavailable, never from the model's prose. (Adapted from the hardened kit; owned by this agent.)"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable", "pasted"]
NOUN = {"engineering": "engineering issues (tracker)", "docs": "launch docs (document store)",
        "support": "support macros and articles (support desk)", "marketing": "marketing tasks (task manager)"}
PASTABLE = frozenset({"engineering", "marketing"})  # parse_paste.AREAS
CONNECT = {"engineering": "tracker", "docs": "document store", "support": "support desk",
           "marketing": "task manager"}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state in ("ok", "pasted")


def assess(source: str, offered: Sequence[McpTool], dropped: Mapping[str, str],
           helpers: frozenset[str] = frozenset()) -> SourceStatus:
    """`missing` when no tool was offered for the source, `unavailable` when every one was dropped."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    """`marketing tasks (task manager): no tool connected; connect a task manager or paste a checklist.`"""
    noun, connect = NOUN.get(status.source, status.source), CONNECT.get(status.source, status.source)
    if status.state == "unavailable":
        return f"{noun}: unavailable for your connection ({status.reason})."
    if status.state == "missing":
        paste = " or paste a checklist" if status.source in PASTABLE else ""
        return f"{noun}: no tool connected; enable a {connect} connector for this agent{paste}."
    return f"{noun}: {status.reason or 'not read'}."
