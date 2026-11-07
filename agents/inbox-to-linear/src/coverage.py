"""Pure: one line per source (email, tracker) that could not be read this turn, decided from the offered
tools and the tools dropped as unavailable, never from the model's prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from nasiko_mcp import McpTool

_LABEL = {"email": "Email", "tracker": "Issue tracker"}


def missing_line(source: str) -> str:
    return (f"{_LABEL[source]}: no {source} tool is available to this agent; check that the {source} connector "
            "is enabled for it.")


def unavailable_line(source: str, reason: str) -> str:
    return f"{_LABEL[source]}: not available for your connection ({reason})."


def all_dropped(offered: Sequence[McpTool], dropped: Mapping[str, str]) -> str | None:
    """The reason line when every offered tool was dropped as unavailable, else None."""
    if not offered or any(t.name not in dropped for t in offered):
        return None
    return ", ".join(dict.fromkeys(dropped[t.name] for t in offered))
