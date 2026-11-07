"""Paste mode: with no tool to read, the user pastes yesterday's notes or a PR list and the agent sorts
each pasted line under a person and a section. One LLM call; every line it returns must appear in the
pasted text (whitespace and case aside) and every name must appear in it as whole words, else dropped.
Confidence is always low: nothing was read from a tool."""

from __future__ import annotations

import re
from collections.abc import Mapping

import prompts
from checks import parse_answer
from deps import TurnDeps
from identity import clean_label, tokens
from llm import ask_once
from standup import Line, PersonStandup

MIN_LINES = 3
MIN_CHARS = 200
MAX_PEOPLE = 15
SECTIONS = ("shipped", "in_progress", "blocked")
_SPACES = re.compile(r"\s+")


def looks_pasted(text: str) -> bool:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return len(lines) >= MIN_LINES or len(text or "") >= MIN_CHARS


async def format_paste(deps: TurnDeps, text: str) -> tuple[str, tuple[PersonStandup, ...]]:
    """("ok" | "off_topic", the people found)."""
    messages = ({"role": "system", "content": prompts.PASTE_SYSTEM},
                {"role": "user", "content": prompts.fence_paste(text)})
    answer = parse_answer(await ask_once(deps, messages)) or {}
    if answer.get("status") == "off_topic":
        return "off_topic", ()
    return "ok", checked_people(answer, text)


def checked_people(answer: Mapping, text: str) -> tuple[PersonStandup, ...]:
    pasted = _norm(text)
    words = f" {' '.join(tokens(text))} "
    people: dict[str, PersonStandup] = {}
    raw_people = answer.get("people") if isinstance(answer.get("people"), list) else []
    for raw in raw_people:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
            continue
        name = clean_label(raw["name"])
        if not name or f" {' '.join(tokens(name))} " not in words or name.casefold() in people:
            continue
        sections = {s: tuple(_line(v) for v in _strings(raw.get(s)) if _norm(v) and _norm(v) in pasted)
                    for s in SECTIONS}
        if any(sections.values()):
            people[name.casefold()] = PersonStandup(name=name, shipped=sections["shipped"],
                                                    in_progress=sections["in_progress"], blocked=sections["blocked"])
    return tuple(people.values())[:MAX_PEOPLE]


def _line(text: str) -> Line:
    return Line("note", "", "", _SPACES.sub(" ", text).strip(), "", None)


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text or "").strip().casefold()
