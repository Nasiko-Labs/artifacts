"""The headline: the only prose the model writes about the forecast. It gets exact, already-rendered facts and
returns {"status", "headline"}; a headline with a number not in the facts, more than two sentences, or no JSON is
replaced by a template built in code."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import prompts
import rank
import render
from burn import Forecast
from deps import TurnDeps
from llm import ask_once

MAX_CHARS = 400
MAX_SENTENCES = 2
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_JSON = re.compile(r"\{.*\}", re.DOTALL)
_RANK_PREFIX = re.compile(r"^\d+\. ")


def facts(forecasts: Sequence[Forecast]) -> list[str]:
    shares = rank.shares(forecasts)
    lines = [render.service_line(i, f, shares.get(f.name, 0)).replace("**", "") for i, f in enumerate(forecasts, 1)]
    return [_RANK_PREFIX.sub("", line) for line in lines]  # rank numbers are not facts to quote


def checked(text: str, fact_lines: Sequence[str]) -> str | None:
    """The headline when every number in it appears in the facts and it is short; else None."""
    headline = " ".join(str(text or "").split())
    if not headline or len(headline) > MAX_CHARS or "<" in headline or "](" in headline:
        return None
    if len(_SENTENCE_END.findall(headline)) > MAX_SENTENCES:
        return None
    allowed = set(_NUMBER.findall("\n".join(fact_lines)))
    if any(n not in allowed for n in _NUMBER.findall(headline)):
        return None
    return headline


async def write(deps: TurnDeps, forecasts: Sequence[Forecast]) -> str:
    fact_lines = facts(forecasts)
    fallback = render.template_headline(forecasts)
    answer = await ask_once(deps, ({"role": "system", "content": prompts.HEADLINE_SYSTEM},
                                   {"role": "user", "content": prompts.fence_data("\n".join(fact_lines))}))
    match = _JSON.search(answer or "")
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        return fallback
    if not isinstance(data, dict) or data.get("status") != "ok":
        return fallback
    return checked(str(data.get("headline") or ""), fact_lines) or fallback
