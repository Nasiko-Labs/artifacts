"""Follow-ups on the stored result: re-render commands parsed in code (zero calls), questions answered in
code from the computed table where possible, else one scope-checked call against the theme table only."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import filters
import llm
import prompts
import render
import smalltalk
from filters import Focus
from report import View

COMMAND_MAX_WORDS = 12
ASK_STATUSES = ("ok", "not_in_result", "off_topic")
_WORDS = re.compile(r"[^a-z0-9\- ]+")
_CHURN = re.compile(r"\bchurn\b|\bat risk\b|\bwho might leave\b")
_TOP = re.compile(r"\btop (\d{1,2})\b")
_SORT = re.compile(r"\b(?:sort|order|rank)(?:ed)? by (count|items?|customers?|accounts?|weight|arr|score)\b")
_QUOTES = re.compile(r"\b(?:quotes?|verbatims?)\b(?:.*?\btheme (\d{1,2})\b|.*?\b(\d{1,2})\b)?")
_ITEMS_FOR = re.compile(r"\b(?:items?|tickets?|feedback|rows?)\b.*?\btheme (\d{1,2})\b")
_RESET = re.compile(r"\b(?:all (?:customers|tiers|items)|clear (?:the )?filters?|no filter|remove (?:the )?filters?)\b")
_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b")
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|"
                             r"name|define|describe|draft|generate|create|make)\b")
_MOST = (
    (re.compile(r"\bmost (?:customers|accounts)\b|\bwidest\b"), "customers"),
    (re.compile(r"\bmost (?:items|feedback|tickets|mentions|complaints)\b|\bbiggest\b|\blargest\b"), "count"),
    (re.compile(r"\b(?:highest|most) (?:weight|arr|revenue|score)\b|\bmost important\b|\btop theme\b"), "weighted_score"),
)
_TREND = re.compile(r"\b(rising|growing|trending up|falling|declining|new)\b")


@dataclass(frozen=True)
class Command:
    view: View
    focus: Focus | None = None     # a new filter replaces the stored one; None keeps it


def plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str, current: Focus) -> Command | None:
    words = plain(text)
    if not words or len(words.split()) > COMMAND_MAX_WORDS or _QUESTION_START.match(words) and "?" in text:
        return None
    view, focus, matched = View(as_json=bool(_JSON.search(words))), None, bool(_JSON.search(words))
    if _CHURN.search(words):
        view, matched = replace(view, only_churn=True), True
    if m := _TOP.search(words):
        view, matched = replace(view, top=int(m.group(1))), True
    if m := _SORT.search(words):
        key = m.group(1)
        view = replace(view, sort="count" if key.startswith("item") or key == "count" else
                       "customers" if key.startswith(("customer", "account")) else "weight")
        matched = True
    if m := _ITEMS_FOR.search(words):
        view, matched = replace(view, items_for=int(m.group(1))), True
    elif m := _QUOTES.search(words):
        view, matched = replace(view, quotes=m.group(1) or m.group(2) or "all"), True
    if _RESET.search(words):
        focus, matched = Focus(hint=current.hint), True
    else:
        asked = filters.parse_focus(text)
        if not asked.is_empty:
            focus, matched = replace(asked, hint=current.hint), True
    return Command(view, focus) if matched else None


def is_question_like(text: str) -> bool:
    return (text or "").strip().endswith("?") or bool(_QUESTION_START.match(plain(text)))


def code_answer(question: str, themes: Sequence[Mapping[str, Any]]) -> str | None:
    """Answers "which theme has the most customers?" and "which themes are rising?" from the table."""
    words = plain(question)
    if not themes:
        return None
    for pattern, key in _MOST:
        if pattern.search(words):
            n, best = max(enumerate(themes, start=1), key=lambda t: (_value(t[1], key), -t[0]))
            return (f"Theme {n}, {render.clean(best['name'])}: {best['count']} items from {len(best['customers'])} "
                    f"customers, {best['weighted_score']:.1f}% of customer weight.")
    if m := _TREND.search(words):
        wanted = {"growing": "rising", "trending up": "rising", "declining": "falling"}.get(m.group(1), m.group(1))
        hits = [f"{n}. {render.clean(t['name'])}" for n, t in enumerate(themes, start=1) if t["trend"] == wanted]
        return (f"Themes with a {wanted} trend: " + "; ".join(hits) + ".") if hits else f"No theme is {wanted}."
    return None


def _value(theme: Mapping[str, Any], key: str) -> float:
    return float(len(theme["customers"]) if key == "customers" else theme[key])


def theme_table(themes: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(f"{n}. {t['name']} | type {t['type']} | {t['count']} items | {len(t['customers'])} customers | "
                     f"weight {t['weighted_score']:.1f}% | trend {t['trend']}" for n, t in enumerate(themes, start=1))


async def ask(deps: llm.TurnDeps, question: str, themes: Sequence[Mapping[str, Any]], context: Sequence[str] = ()) -> str:
    table = theme_table(themes) or "(no feedback has been clustered yet)"
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages(table, question, context), prompts.ASK_CONTRACT,
                                         _parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if not themes:
        return smalltalk.NO_ITEMS_YET
    answer = render.clean(parsed["answer"])
    numbers = set(re.findall(r"\d+(?:\.\d+)?", answer))
    if parsed["status"] != "ok" or not answer or not numbers <= set(re.findall(r"\d+(?:\.\d+)?", table)):
        return prompts.NOT_IN_RESULT
    return answer


def _parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or "")}
