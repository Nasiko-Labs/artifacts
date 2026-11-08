"""Pure: one line per source that was not read (missing or unavailable, with the reason), worded for the
user. Decided from the catalog and the tools dropped as unavailable, never from the model's prose.
(Adapted from the MCP kit; owned here; wording is this agent's.)"""

from __future__ import annotations

from collections.abc import Mapping

LABELS = {"sheet_search": "Spreadsheet search", "sheet_read": "Spreadsheet reading", "history": "Chat history",
          "channels": "Chat channel listing", "post": "Posting in a chat thread"}


def missing(role: str) -> str:
    return f"{LABELS[role]} isn't available to this agent (no such tool is enabled for it)."


def unavailable(role: str, reason: str) -> str:
    return f"{LABELS[role]} isn't available for your connection ({reason})."


def failed(role: str, tool: str, dropped: Mapping[str, str]) -> str:
    reason = dropped.get(tool)
    return unavailable(role, reason) if reason else f"{LABELS[role]} failed this time, so that source was not read."
