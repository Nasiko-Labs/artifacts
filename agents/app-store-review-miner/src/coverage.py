"""Pure: whether the optional review tool could be read this turn, and the one line the user gets when it
could not. Decided from the offered tools and the tools dropped as unavailable, never from model prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from nasiko_mcp import McpTool

MISSING = ("No app-review tool is connected to this agent, so I can only analyse reviews you paste "
           "(an optional review connector can be enabled for it).")
UNAVAILABLE = "The app-review tool isn't available for your connection ({reason}), so no reviews were fetched."


def line(offered: Sequence[McpTool], dropped: Mapping[str, str]) -> str | None:
    """None when at least one offered tool is still usable."""
    if not offered:
        return MISSING
    reasons = [dropped[t.name] for t in offered if t.name in dropped]
    if len(reasons) == len(offered):
        return UNAVAILABLE.format(reason=", ".join(dict.fromkeys(reasons)))
    return None
