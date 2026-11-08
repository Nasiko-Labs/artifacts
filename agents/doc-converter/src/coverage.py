"""Pure: which storage places could actually be used this turn, and one line per place that could not
(missing tool, or every tool dropped as unavailable). Decided from the catalog and the tool-health record,
never from prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import sources
from nasiko_mcp import McpTool


def notes(place: str | None, tools: Mapping[str, Sequence[McpTool]], dropped: Mapping[str, str]) -> tuple[str, ...]:
    """`tools` maps each place this turn looked in to the tools offered there."""
    places = (place,) if place else tuple(tools)
    lines = []
    for p in places:
        offered = tools.get(p, ())
        label = sources.LABEL.get(p, p)
        if not offered:
            if place:
                lines.append(f"No {label} tool is available to this agent: check that the connector is enabled for it.")
            continue
        reasons = [dropped[t.name] for t in offered if t.name in dropped]
        if len(reasons) == len(offered):
            lines.append(f"{label} isn't available for your connection ({', '.join(dict.fromkeys(reasons))}).")
    return tuple(lines)
