"""Pure: refinement commands on the stored stories, parsed in code, and the ones that need no model call.

    split story 3 / merge 4 and 5 / add acceptance for X to story 2     -> model calls (turns.py)
    drop story 6 / rename 2 to X / move 4 before 2 / max 5              -> zero calls
    which can ship first / show all / as json                           -> re-render, zero calls
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import prompts
from inputs import clamp_max
from order import move_before
from stories import Story

MAX_TITLE_CHARS = 120
_N = r"(?:story\s+|no\.?\s*|#)?(\d{1,2})"
_SPLIT = re.compile(rf"\b(?:split|break(?: up| down)?|slice)\s+(?:up\s+)?{_N}\b", re.IGNORECASE)
_MERGE = re.compile(rf"\b(?:merge|combine|join)\s+(?:stories\s+)?{_N}\s*(?:and|&|,|with|\+)\s*{_N}\b",
                    re.IGNORECASE)
_DROP = re.compile(rf"\b(?:drop|remove|delete)\s+{_N}\b", re.IGNORECASE)
_RENAME = re.compile(rf"\b(?:rename|retitle)\s+{_N}\s+(?:to|as)\s*:?\s*(.+)$", re.IGNORECASE | re.DOTALL)
_MOVE = re.compile(rf"\bmove\s+{_N}\s+(?:before|ahead of|above)\s+{_N}\b", re.IGNORECASE)
_ADD = re.compile(r"\badd\s+(?:an?\s+|more\s+)?acceptance(?:\s+criteri(?:on|a))?\s*(?:for\s+)?(.*)$",
                  re.IGNORECASE | re.DOTALL)
_ADD_TARGET = re.compile(rf"\s*(?:to|on|for|in)\s+{_N}\s*\.?\s*$", re.IGNORECASE)
_MAX = re.compile(r"^\W*(?:max(?:imum)?|at most|only|keep)\s+(\d{1,2})(?:\s+stor(?:y|ies))?\W*$", re.IGNORECASE)
_FIRST = re.compile(r"\bship first\b|\bcan ship\b|\bstart with\b|\bwhich (?:one|story|stories)? ?first\b|"
                    r"\bfirst slice\b|\bwalking skeleton\b|\bwhat first\b", re.IGNORECASE)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the stories)? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_ALL = re.compile(r"^\W*(?:show )?(?:all|everything|the (?:full )?(?:list|stories)|(?:all )?stories)\W*$",
                  re.IGNORECASE)
_QUOTES = " \"'“”."


@dataclass(frozen=True)
class Command:
    kind: str               # split | merge | drop | rename | move | add | max | first | json | all
    a: int = 0
    b: int = 0
    text: str = ""


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    if (m := _MERGE.search(message)) is not None:
        return Command("merge", int(m.group(1)), int(m.group(2)))
    if (m := _RENAME.search(message)) is not None:
        return Command("rename", int(m.group(1)), text=m.group(2).strip(_QUOTES))
    if (m := _ADD.search(message)) is not None:
        target = _ADD_TARGET.search(m.group(1))
        topic = m.group(1)[:target.start()] if target else m.group(1)
        return Command("add", int(target.group(1)) if target else 0, text=topic.strip(_QUOTES))
    for kind, pattern in (("split", _SPLIT), ("drop", _DROP)):
        if (m := pattern.search(message)) is not None:
            return Command(kind, int(m.group(1)))
    if (m := _MOVE.search(message)) is not None:
        return Command("move", int(m.group(1)), int(m.group(2)))
    if (m := _MAX.match(message)) is not None:
        return Command("max", clamp_max(int(m.group(1))))
    if _JSON.match(message):
        return Command("json")
    if _FIRST.search(message):
        return Command("first")
    if _ALL.match(message):
        return Command("all")
    return None


def missing(command: Command, count: int) -> str | None:
    """A reply when the command names a story that doesn't exist (or none), else None."""
    numbers = [n for n in (command.a, command.b) if n] if command.kind in ("merge", "move") else [command.a]
    if command.kind in ("split", "drop", "rename", "add", "merge", "move"):
        if command.kind == "add" and not command.a:
            return "Which story? For example: \"add acceptance for the empty state to story 2\"."
        bad = [n for n in numbers if not 1 <= n <= count]
        if bad:
            return f"There's no story {bad[0]}; this list has {count}."
        if command.kind in ("merge", "move") and command.a == command.b:
            return "Pick two different stories."
    return None


def drop(stories: list[Story], n: int) -> tuple[list[Story], str]:
    gone = stories[n - 1]
    kept = [s for s in stories if s.key != gone.key]
    inherited = {gone.key: gone.depends_on}
    result = [replace(s, depends_on=tuple(dict.fromkeys(d2 for d in s.depends_on
                                                        for d2 in inherited.get(d, (d,)))))
              for s in kept]
    return result, f"_Dropped story {n} (\"{gone.title}\"); stories after it moved up._"


def rename(stories: list[Story], n: int, title: str) -> tuple[list[Story], str]:
    clean = " ".join(prompts.strip_markers(title).split())[:MAX_TITLE_CHARS]
    if not clean:
        return stories, "_Give the new title after \"to\"._"
    result = [replace(s, title=clean) if i == n - 1 else s for i, s in enumerate(stories)]
    return result, f"_Renamed story {n}._"


def move(stories: list[Story], a: int, b: int) -> tuple[list[Story], str]:
    key, anchor = stories[a - 1].key, stories[b - 1].key
    moved = move_before(stories, key, anchor)
    landed = next(i for i, s in enumerate(moved) if s.key == key)
    if landed > next(i for i, s in enumerate(moved) if s.key == anchor):
        return stories, f"_Story {a} depends on story {b} (directly or through others), so it can't go first._"
    return moved, f"_Moved story {a} before story {b}._"
