"""Pure: the sign convention. Each row is an expense unless its label reads like revenue, or the user said
which way is favourable. A table with both kinds is handled row by row and the convention is always shown."""

from __future__ import annotations

import re
from collections.abc import Sequence

AUTO, REVENUE, EXPENSE = "auto", "revenue", "expense"
REVENUE_LABEL = re.compile(r"\b(?:revenue|sales|income|bookings|arr|gross profit|margin|profit)\b", re.IGNORECASE)
_COST_LABEL = re.compile(r"\b(?:cost of|cogs|expenses?|costs?)\b", re.IGNORECASE)
_NET_LABEL = re.compile(r"\b(?:net|profit|ebitda)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.\n;]*\b(?:favou?rable|unfavou?rable|better|worse)\b[^.\n;]*", re.IGNORECASE)
_SUBJECT = re.compile(r"\b(?:revenue|sales|income|bookings|arr|expenses?|costs?|spend(?:ing)?)\b", re.IGNORECASE)
_UP = re.compile(r"\b(?:above|over|higher|more|exceeds?|exceeding)\b", re.IGNORECASE)
_DOWN = re.compile(r"\b(?:below|under|lower|less)\b", re.IGNORECASE)
_GOOD = re.compile(r"\b(?:favou?rable|better|good)\b", re.IGNORECASE)
_BAD = re.compile(r"\b(?:unfavou?rable|worse|bad)\b", re.IGNORECASE)


def mode_from(text: str) -> tuple[str, bool]:
    """(mode, stated) from the user's words: `auto` keeps the label rule; a subject-less rule ("above budget
    is favourable") applies to every line."""
    for sentence in _SENTENCE.findall(text or ""):
        if _SUBJECT.search(sentence):
            return AUTO, True
        up, down, good, bad = (bool(p.search(sentence)) for p in (_UP, _DOWN, _GOOD, _BAD))
        if (up and good and not bad) or (down and bad):
            return REVENUE, True
        if (up and bad) or (down and good):
            return EXPENSE, True
    return AUTO, False


def kind_of(name: str, mode: str) -> str:
    if mode in (REVENUE, EXPENSE):
        return mode
    if REVENUE_LABEL.search(name or "") and not _COST_LABEL.search(name or ""):
        return REVENUE
    return EXPENSE


def total_kind(total_label: str, item_kinds: Sequence[str], mode: str) -> str:
    if mode in (REVENUE, EXPENSE):
        return mode
    if total_label:
        return REVENUE if _NET_LABEL.search(total_label) or kind_of(total_label, AUTO) == REVENUE else EXPENSE
    return REVENUE if item_kinds and all(k == REVENUE for k in item_kinds) else EXPENSE


def convention(item_kinds: Sequence[str], mode: str, stated: bool, revenue_names: Sequence[str]) -> tuple[str, bool]:
    """(the sentence shown in the reply, guessed): guessed when a mixed table had no stated rule."""
    if mode == REVENUE:
        return "Above budget is favourable for every line, as you said.", False
    if mode == EXPENSE:
        return "Above budget is unfavourable for every line, as you said.", False
    kinds = set(item_kinds)
    if kinds == {REVENUE}:
        return "Revenue over budget is favourable.", False
    if kinds != {REVENUE, EXPENSE}:
        return "Expenses over budget are unfavourable.", False
    names = ", ".join(revenue_names[:5]) + (" and others" if len(revenue_names) > 5 else "")
    text = (f"Expense lines over budget are unfavourable; revenue lines ({names}) over budget are favourable.")
    if stated:
        return text, False
    return text + " Revenue lines were picked by their names; tell me if that is wrong.", True
