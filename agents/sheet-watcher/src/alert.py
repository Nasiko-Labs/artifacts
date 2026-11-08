"""Pure: the chat alert, from a fixed template filled by code: "Sheet <title> update: N events." and the top
five event lines (new events first). The model writes nothing that is posted."""

from __future__ import annotations

from collections.abc import Sequence

from models import Event
from render import line

TOP_LINES = 5


def text(title: str, events: Sequence[Event]) -> str:
    shown = [e for e in events if e.type != "row_changed"]
    if not shown:
        return ""
    count = len(shown)
    head = f"Sheet {title} update: {count} event{'s' * (count != 1)}."
    body = [f"- {line(e)}{' (still)' if e.repeat else ''}" for e in shown[:TOP_LINES]]
    more = [f"- ...and {count - TOP_LINES} more"] if count > TOP_LINES else []
    return "\n".join([head, *body, *more])
