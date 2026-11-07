"""Pure: follow-up commands on the stored handoff, parsed in code (zero calls unless noted).

    "only hot" / "shorter" / "show sources" / "show all" / "show the handoff" -> re-render
    "as json"                                                                -> the envelope as fenced JSON
    "mark #4812 resolved" (one or more refs)                                 -> status override in code
    "add a note: <text>"                                                     -> one extract call on that note
    "tell / send / post / email / call ..."                                  -> fixed reply: it never acts
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from entries import ref_key

_VIEWS = (
    ("hot", re.compile(r"^(?:(?:show |give me )?(?:only|just) (?:the )?hot(?: ones| items| tickets)?|hot (?:only|items|ones)"
                       r"|what'?s hot)$")),
    ("short", re.compile(r"^(?:shorter|short(?:er)? version|make it short(?:er)?|brief(?:er)?|tl;?dr|condense it)$")),
    ("sources", re.compile(r"^(?:show (?:the |me the )?(?:sources|quotes)|sources|with sources|where is that from)$")),
    ("all", re.compile(r"^(?:show all|show everything|everything|full(?: version)?|show (?:all|the rest))$")),
    ("default", re.compile(r"^(?:show (?:me )?(?:the )?handoff(?: again)?|again|default|the full handoff)$")),
)
_JSON = re.compile(r"^(?:(?:give it to me |show it |return it )?(?:as|in) json|json(?: only)?)$")
_MARK = re.compile(r"^(?:please )?(?:mark|set|move)\s+(.+?)\s+(?:as\s+|to\s+)?(?:resolved|done|closed|fixed|solved)$")
_MARK_REF = re.compile(r"#?[a-z][a-z0-9]{1,9}-\d{1,7}|#?\d{2,}")
_ADD = re.compile(r"^(?:please )?add (?:a |this |another |one more |the )?(?:note|update|entry)s?\s*[:\-]?\s*(.+)$",
                  re.IGNORECASE | re.DOTALL)
_ACT = re.compile(r"^(?:please |can you |could you )?(?:tell|send|post|email|e-mail|notify|message|ping|call|contact|"
                  r"text|page|assign|close|reply to|escalate to)\b")


@dataclass(frozen=True)
class Command:
    kind: str                 # view | json | mark | add | act
    view: str = "default"
    refs: tuple[str, ...] = ()
    text: str = ""


def _plain(text: str) -> str:
    return " ".join((text or "").casefold().split()).strip(" .!?")


def parse_command(text: str) -> Command | None:
    added = _ADD.match((text or "").strip())
    if added and added.group(1).strip():
        return Command("add", text=added.group(1).strip())
    plain = _plain(text)
    for view, pattern in _VIEWS:
        if pattern.match(plain):
            return Command("view", view=view)
    if _JSON.match(plain):
        return Command("json")
    marked = _MARK.match(plain)
    if marked:
        refs = tuple(ref_key(r) for r in _MARK_REF.findall(marked.group(1)))
        if refs:
            return Command("mark", refs=refs)
    if _ACT.match(plain):
        return Command("act")
    return None


def wants_new_handoff(text: str) -> bool:
    """A short ask for a handoff with no notes ("write my handoff") -> ask for the notes."""
    return bool(re.search(r"\bhand-?\s?off|\bhand ?over|\bend of (?:my )?shift\b", text or "", re.IGNORECASE))
