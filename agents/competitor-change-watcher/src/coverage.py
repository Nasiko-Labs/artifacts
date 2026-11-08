"""Pure: whether the issue tracker could be read this turn, and the one line the user gets when it could not.
Decided from the offered tools and the tools dropped as unavailable, never from model prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from nasiko_mcp import McpTool

CHECKED = "Roadmap checked."
NO_GATEWAY = "Roadmap was not checked: this agent has no tool gateway."
MISSING = "Roadmap was not checked: no issue tracker is connected to this agent."
UNREACHABLE = "Roadmap was not checked: the tool gateway couldn't be reached."
UNAVAILABLE = "Roadmap was not checked: the issue tracker isn't available for your connection ({reason})."
STEP_LIMIT = "Roadmap was not checked: no model calls or time were left this turn."
NOTHING_NEW = "Roadmap not searched: no new changes."


def line(offered: Sequence[McpTool], dropped: Mapping[str, str]) -> str | None:
    """None when at least one offered tool is still usable."""
    if not offered:
        return MISSING
    reasons = [dropped[t.name] for t in offered if t.name in dropped]
    if len(reasons) == len(offered):
        return UNAVAILABLE.format(reason=", ".join(dict.fromkeys(reasons)))
    return None
