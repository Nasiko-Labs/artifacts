"""Pure: the coverage lines under the table. Each says what could not be read and what the results
therefore cover. Decided from the offered and dropped tools and from code counts, never from prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from nasiko_mcp import McpTool
from records import MAX_ITEMS, MAX_POSTMORTEMS
from send_guard import name_tokens

NO_DOCS = "No document source connected: action items come from the tracker only."
NO_ALERTING = "No alerting source: repeats are checked against tracker incidents only."


def comments_tool_dropped(tools: Sequence[McpTool], dropped: Mapping[str, str]) -> str | None:
    """The reason a comment-reading tool was dropped as unavailable this turn, if one was."""
    for tool in tools:
        if tool.name not in dropped:
            continue
        if {"comment", "comments"} & set(name_tokens(tool.name)) or "comment" in tool.description.casefold():
            return dropped[tool.name]
    return None


def comments_note(reason: str, count: int) -> str:
    return (f"Tracker comments are unavailable ({reason}): 'closed without evidence' could not be checked for "
            f"{count} closed item{'s' if count != 1 else ''}; they're listed as 'closed (not verified)'.")


def cap_notes(postmortems_capped: bool, items_capped: bool) -> tuple[str, ...]:
    notes = ()
    if postmortems_capped:
        notes = (*notes, (f"More than {MAX_POSTMORTEMS} postmortems matched; only the first {MAX_POSTMORTEMS} "
                          "were checked. Narrow the window or name a team to see the rest."))
    if items_capped:
        notes = (*notes, f"More than {MAX_ITEMS} action items matched; only the first {MAX_ITEMS} were checked.")
    return notes


def dropped_note(count: int) -> tuple[str, ...]:
    if not count:
        return ()
    return ((f"{count} record{'s' if count != 1 else ''} the tools did not return verbatim "
             f"{'were' if count != 1 else 'was'} left out."),)


def unknown_status_note(count: int) -> tuple[str, ...]:
    if not count:
        return ()
    return ((f"The current status of {count} action item{'s' if count != 1 else ''} could not be read, so "
             f"{'they are' if count != 1 else 'it is'} not in the table."),)


def unreadable_docs_note(count: int) -> tuple[str, ...]:
    if not count:
        return ()
    return ((f"{count} postmortem document{'s' if count != 1 else ''} could not be read and "
             f"{'were' if count != 1 else 'was'} skipped."),)


def unavailable_lines(dropped: Mapping[str, str], skip: str | None) -> tuple[str, ...]:
    """Other tools dropped as unavailable this turn, one line each (the comments tool is said above)."""
    reasons = sorted({r for r in dropped.values() if r != skip})
    return tuple(f"Some tools were unavailable for your connection ({r}); results may be incomplete."
                 for r in reasons)
