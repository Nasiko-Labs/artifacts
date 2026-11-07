"""The two model writers (welcome, per-PR lessons). Each is one small tool-less call over fenced text,
and its prose passes `check_prose` before it reaches the guide: a sentence carrying a command, path,
link or number that the fetched text does not contain is removed and counted. With no budget, time or
usable answer, the welcome falls back to the repository's own description, copied."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import prompts
from deps import TurnDeps
from reader import BudgetExceeded, TurnBudget, ask_once
from verify import check_prose, parse_object, text_of

log = logging.getLogger("onboarding.sections")

README_PROMPT_CHARS = 4_000
PR_BODY_CHARS = 600
MAX_LESSON_CHARS = 140


async def welcome(deps: TurnDeps, budget: TurnBudget, description: str, readme: str, seen: str) -> tuple[str, int]:
    """(1-3 sentences on what the repository is, sentences dropped by the check)."""
    fallback = description or first_paragraph(readme)
    if not description and not readme:
        return "", 0
    supplied = f"Description: {description or '(none)'}\n\nREADME:\n{readme[:README_PROMPT_CHARS]}"
    messages = ({"role": "system", "content": prompts.WELCOME_SYSTEM},
                {"role": "user", "content": prompts.fence(supplied)})
    try:
        answer = parse_object(await ask_once(deps, messages, budget)) or {}
    except BudgetExceeded:
        return fallback, 0
    text, dropped = check_prose(text_of(answer.get("welcome")), seen)
    return (text or fallback), dropped


async def lessons(deps: TurnDeps, budget: TurnBudget, prs: Sequence[dict], seen: str) -> tuple[dict[int, str], int]:
    """number -> one checked "what to learn" clause."""
    if not prs:
        return {}, 0
    supplied = json.dumps([{"number": p["number"], "title": p["title"], "body": (p.get("body") or "")[:PR_BODY_CHARS]}
                           for p in prs], ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.LESSONS_SYSTEM},
                {"role": "user", "content": prompts.fence(supplied)})
    try:
        answer = parse_object(await ask_once(deps, messages, budget)) or {}
    except BudgetExceeded:
        return {}, 0
    wanted = {p["number"] for p in prs}
    out: dict[int, str] = {}
    dropped = 0
    for row in answer.get("lessons") or ():
        number = row.get("number") if isinstance(row, dict) else None
        if number not in wanted or number in out:
            continue
        text, lost = check_prose(text_of(row.get("lesson"))[:MAX_LESSON_CHARS], seen)
        dropped += lost
        if text:
            out = {**out, number: text}
    return out, dropped


def first_paragraph(readme: str) -> str:
    """The README's first prose paragraph, copied (no heading, badge, list, code or table)."""
    para: list[str] = []
    in_fence = False
    for raw in (readme or "").splitlines():
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or line.startswith(("#", "!", "[!", "<", "|", "-", "*", ">")):
            if para:
                break
            continue
        if not line:
            if para:
                break
            continue
        para.append(line)
    return " ".join(para)[:400]
