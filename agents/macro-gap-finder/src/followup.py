"""Pure: read a one-line follow-up on the stored result. Everything here is answered in code, except a
changed draft ("shorter draft for 3", "friendlier"), which costs one model call and is checked again."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SHOW = re.compile(r"\b(?:show|list|see|give|display)\b.*?\b(?:repl(?:y|ies)|examples?|tickets?|members?|sources?)\b"
                   r"(?:\D{0,25}?)(\d{1,3})\b", re.IGNORECASE)
_MIN = re.compile(r"\b(?:min(?:imum)?|at least)\s*(?:of\s*)?(\d{1,4})\b|"
                  r"\b(?:under|below|fewer than|less than)\s+(\d{1,4})\s*(?:repl(?:y|ies)|times|occurrences)?",
                  re.IGNORECASE)
_TOP = re.compile(r"\btop\s*(\d{1,4})\b", re.IGNORECASE)
_BOILERPLATE = re.compile(r"\b(?:boilerplate|footers?|ignored|signature)\b", re.IGNORECASE)
_REDRAFT = re.compile(r"\b(?:shorter|longer|friendlier|warmer|more formal|formal|simpler|more concise|concise|"
                      r"redraft|rewrite|rephrase|reword|casual|more casual|polite|clearer)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?:#|\bpattern\s*|\bfor\s*|\bnumber\s*|\bno\.?\s*|\bdraft\s*|\bmacro\s*)(\d{1,3})\b|\b(\d{1,3})\b",
                     re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_AGAIN = re.compile(r"^\s*(?:show (?:it|the (?:result|table|candidates)) again|again|show all|the table)\s*[.!?]?\s*$",
                    re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    min_freq: int | None = None
    top: int | None = None
    show: int | None = None
    boilerplate: bool = False
    redraft: int | None = None
    redraft_needs_number: bool = False
    instruction: str = ""
    as_json: bool = False

    @property
    def changes_view(self) -> bool:
        return self.min_freq is not None or self.top is not None or self.as_json


def _number(text: str) -> int | None:
    for match in _NUMBER.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            return int(value)
    return None


def parse(text: str) -> Command | None:
    """A command, or None when the text is not one (it then goes to the scope check)."""
    line = " ".join((text or "").split())
    if not line:
        return None
    show = _SHOW.search(line)
    if show:
        return Command(show=int(show.group(1)), as_json=bool(_JSON.search(line)))
    if _REDRAFT.search(line):
        n = _number(line)
        return Command(redraft=n, redraft_needs_number=n is None, instruction=line[:300])
    found_min, found_top = _MIN.search(line), _TOP.search(line)
    command = Command(min_freq=int(found_min.group(1) or found_min.group(2)) if found_min else None,
                      top=int(found_top.group(1)) if found_top else None,
                      boilerplate=bool(_BOILERPLATE.search(line)), as_json=bool(_JSON.search(line)))
    if command.changes_view or command.boilerplate:
        return command
    return Command() if _AGAIN.match(line) else None
