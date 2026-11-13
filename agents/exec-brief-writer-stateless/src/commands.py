"""Pure: follow-up commands read in code, and the code-only edits (no model call).

A command counts only when it is the WHOLE message ("drop risk 2", "only top 3", "show sources",
"start over"), so a longer revision ("drop risk 3 and make it shorter") goes to the model instead.
`wants_add` / `wants_new` read the add-or-replace intent of a message that carries a new paste."""

from __future__ import annotations

import re
from dataclasses import dataclass

from brief import Brief

_NOISE = re.compile(r"[^\w#&,' ]+")
_POLITE = re.compile(r"^(?:please |can you |could you |ok |okay )+|(?: please| thanks| thank you)+$")
_SPACES = re.compile(r"\s+")

_START_OVER = re.compile(r"(?:let's )?(?:start over|start again|start fresh|new brief|reset|clear(?: it| everything| all)?)")
_SHOW = re.compile(r"(?:show|with|include|add|see|give)(?: me)?(?: the)? (?:sources|citations|quotes|snippets|evidence)")
_DROP = re.compile(r"(?:drop|remove|delete|cut)(?: the)?(?: (risk|decision)s?)? #?(\d+(?:(?: ?, ?| and | & )#?\d+)*)")
_TOP = re.compile(r"(?:only|just|keep)(?: the)? top (\d+)(?: (risk|decision)s?)?|top (\d+)(?: (risk|decision)s?)? only")
_NUMBER = re.compile(r"\d+")

_ADD = re.compile(r"\b(?:add (?:this|these|that|it|them|the following|more|to (?:it|this|the brief))|also include|"
                  r"include (?:this|these|that)|plus this|here'?s more|one more|append)\b", re.IGNORECASE)
_NEW = re.compile(r"\b(?:start over|start fresh|start again|new brief|fresh brief|instead of the earlier|"
                  r"replace (?:it|the earlier|everything))\b", re.IGNORECASE)
_BRIEF_REQUEST = re.compile(r"\b(?:brief|briefing|one[- ]pager|exec(?:utive)? summary|headline|risks?|decisions?)\b",
                            re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str = ""                 # start_over | show_sources | drop | top | "" (none)
    target: str = ""               # risk | decision | "" (both, for top)
    numbers: tuple[int, ...] = ()


def _normal(text: str) -> str:
    text = _SPACES.sub(" ", _NOISE.sub(" ", (text or "").casefold())).strip()
    return _POLITE.sub("", text).strip()


def parse(text: str) -> Command:
    norm = _normal(text)
    if _START_OVER.fullmatch(norm):
        return Command("start_over")
    if _SHOW.fullmatch(norm):
        return Command("show_sources")
    match = _DROP.fullmatch(norm)
    if match:
        return Command("drop", match.group(1) or "risk", tuple(int(n) for n in _NUMBER.findall(match.group(2))))
    match = _TOP.fullmatch(norm)
    if match:
        count = match.group(1) or match.group(3)
        return Command("top", match.group(2) or match.group(4) or "", (int(count),))
    return Command()


def wants_add(text: str) -> bool:
    return bool(_ADD.search(text or ""))


def wants_new(text: str) -> bool:
    return bool(_NEW.search(text or ""))


def asks_for_brief(text: str) -> bool:
    return bool(_BRIEF_REQUEST.search(text or ""))


def apply(brief: Brief, command: Command) -> tuple[Brief, str | None]:
    """The edited brief, or (unchanged, a reason) when the numbers do not exist."""
    if command.kind == "drop":
        lines = brief.risks if command.target == "risk" else brief.decisions
        missing = [n for n in command.numbers if not 1 <= n <= len(lines)]
        if missing or not command.numbers:
            return brief, f"There is no {command.target} {', '.join(map(str, missing or command.numbers))} in the brief."
        kept = tuple(line for n, line in enumerate(lines, 1) if n not in command.numbers)
        edited = brief.with_lines(risks=kept) if command.target == "risk" else brief.with_lines(decisions=kept)
        return edited, None
    if command.kind == "top":
        count = command.numbers[0]
        if count < 1:
            return brief, "Keep at least one line."
        risks = brief.risks[:count] if command.target in ("", "risk") else None
        decisions = brief.decisions[:count] if command.target in ("", "decision") else None
        return brief.with_lines(risks=risks, decisions=decisions), None
    return brief, None
