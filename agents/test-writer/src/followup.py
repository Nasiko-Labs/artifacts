"""Pure: follow-ups on the stored tests, parsed in code.

    "as json"                                     -> re-render, zero calls
    "next batch" / "add tests for `parse_date`"   -> write tests for more callables, merged into the file
    anything else ("use unittest", "more edge cases for negative amounts", "mock the HTTP call")
                                                  -> one revise call over the stored tests
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import request

_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the (?:result|tests))? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_NEXT = re.compile(r"\bnext batch\b|\bthe rest\b|\bremaining (?:functions|ones|callables)\b", re.IGNORECASE)
_ADD = re.compile(r"\b(?:add|also|too|as well|tests? for|write tests)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str                       # json | more | revise
    targets: tuple[str, ...] = ()
    text: str = ""


def parse(text: str, known: list[str], tested: list[str], remaining: list[str]) -> Command:
    """`known` = every callable in the stored source; `tested` = the ones the stored tests cover."""
    message = (text or "").strip()
    if _JSON.match(message):
        return Command("json")
    if _NEXT.search(message) and remaining:
        return Command("more", tuple(remaining))
    explicit, loose = request.targets_in(message)
    lookup = {k.casefold(): k for k in known} | {k.rsplit(".", 1)[-1].casefold(): k for k in known}
    named = [lookup[n.casefold()] for n in (*explicit, *loose) if n.casefold() in lookup]
    new = [n for n in dict.fromkeys(named) if n not in tested]
    if new and _ADD.search(message):
        return Command("more", tuple(new))
    return Command("revise", text=message)
