"""Pure: a turn's result (reply text, envelope, state to keep) and how a report becomes one."""

from __future__ import annotations

from dataclasses import dataclass

import commands
import prompts
import render
from inputs import Incoming


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    stored: dict | None = None


def reply(text: str, notes: tuple[str, ...] = ()) -> TurnResult:
    return TurnResult("\n\n".join(p for p in (text, *notes) if p))


def finish(result: dict, incoming: Incoming, notes: tuple[str, ...] = (), lead: str = "",
           stored: dict | None = None, issues: list | None = None, full: bool = False) -> TurnResult:
    """The markdown (or the fenced JSON envelope when asked) plus the envelope as data."""
    data = render.envelope(result)
    text = render.as_json(data) if incoming.wants_json else render.render(result, issues, notes, full, lead)
    return TurnResult(text, data=data, stored=stored)


def paste_next(result: dict) -> str:
    cmds = commands.what_next(result["issues"], result["present"])
    if not cmds:
        return "You have pasted every kind of output I read; nothing else to add."
    return prompts.paste_list("To go further, paste the output of:", cmds)
