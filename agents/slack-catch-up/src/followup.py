"""Follow-ups once a catch-up is stored in this conversation. Decided in code first ("only decisions",
"#payments", "last 3 days", "catch me up again"); only an unclear message goes to the model, which
answers with a sentinel or a short answer from the stored result."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

import parse
import prompts
from deps import TurnDeps
from steps import ask_once

Kind = Literal["render", "run", "reply", "off_topic"]
_URL = re.compile(r"https?://[^\s)\]>]+")
_AGAIN = re.compile(r"\b(?:again|refresh|catch(?:\s|-)?(?:me\s+)?up|what'?s new|anything new|since then)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: Kind
    request: parse.Request | None = None
    only: str = ""
    channel: str = ""
    text: str = ""


def covered(prior: Mapping[str, Any], name: str) -> bool:
    wanted = name.casefold().lstrip("#")
    return any(c["channel"].casefold().lstrip("#") == wanted for c in prior.get("by_channel", []))


def in_code(request: parse.Request, prior: Mapping[str, Any]) -> Route | None:
    if request.explicit_window:
        return Route("run", request)  # WIDEN / another window: re-fetch and replace the stored result
    if request.channels:
        if len(request.channels) == 1 and covered(prior, request.channels[0]) and not _AGAIN.search(request.text):
            return Route("render", channel=request.channels[0], only=_only(request))
        return Route("run", request)
    if (request.only_decisions or request.only_mentions) and not _AGAIN.search(request.text):
        return Route("render", only=_only(request))
    if request.looks_like_catch_up:
        return Route("run", request)
    return None


async def with_model(deps: TurnDeps, request: parse.Request, prior: Mapping[str, Any]) -> Route:
    prior_text = json.dumps(prior, ensure_ascii=False)
    messages = ({"role": "system", "content": prompts.followup_system(prior_text)},
                {"role": "user", "content": request.text})
    text = await ask_once(deps, messages)
    head = text.strip().split()[0].strip(".:") if text.strip() else ""
    if head == prompts.OFF_TOPIC:
        return Route("off_topic")
    if head == "REFRESH":
        return Route("run", request)
    if head == "ONLY_DECISIONS":
        return Route("render", only="decisions")
    if head == "ONLY_MENTIONS":
        return Route("render", only="mentions")
    if text.startswith("CHANNEL:"):
        name = text.split(":", 1)[1].strip().lstrip("#").split()[0].casefold() if text.split(":", 1)[1].strip() else ""
        if name and covered(prior, name):
            return Route("render", channel=name)
        return Route("run", replace(request, channels=(name,) if name else ()))
    if text.startswith("WIDEN:"):
        digits = re.match(r"\s*(\d+)", text.split(":", 1)[1])
        days = parse.clamp_days(int(digits.group(1))) if digits else parse.MAX_DAYS
        return Route("run", replace(request, days=days))
    return Route("reply", text=_only_known_links(text, prior_text) or prompts.UNREADABLE_ANSWER)


def _only(request: parse.Request) -> str:
    return "decisions" if request.only_decisions else "mentions" if request.only_mentions else ""


def _only_known_links(text: str, prior_text: str) -> str:
    """Links the stored result does not contain are removed: the model never adds a URL."""
    return _URL.sub(lambda m: m.group(0) if m.group(0) in prior_text else "[link removed]", text).strip()
