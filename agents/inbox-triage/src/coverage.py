"""Pure: what to tell the user when the email source could not be read, decided from the tools offered and
the tools dropped as unavailable, never from the model's prose. (Copied in once from the hardened kit;
owned by this agent; one source here, email.)"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable"]


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""


def assess(offered: Sequence[McpTool], dropped: Mapping[str, str]) -> SourceStatus:
    """`missing` when no email tool was offered, `unavailable` when every one was dropped, else `ok`."""
    if not offered:
        return SourceStatus("email", "missing")
    reasons = [dropped[t.name] for t in offered if t.name in dropped]
    if len(reasons) == len(offered):
        return SourceStatus("email", "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus("email", "ok")


def note(status: SourceStatus) -> str | None:
    """One line for a source that could not be read; None when it was read."""
    if status.state == "unavailable":
        return f"Email search isn't available for your connection ({status.reason})."
    if status.state == "missing":
        return "No email search tool is available to this agent: check that the email connector is enabled for it."
    return None


def unavailable_reply(status: SourceStatus) -> str:
    return ("I couldn't read your inbox, so I can't triage it, and I won't guess from memory.\n\n"
            f"- {note(status) or 'Every email tool I tried failed for your connection.'}\n\n"
            "You can paste the threads instead, each starting with a From: line.")
