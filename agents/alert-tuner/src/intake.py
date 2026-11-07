"""The one model decision about the user's words: is the request in scope at all. Everything else about
the request (window, threshold, service, repository, which proposal) is read in code (asks.py)."""

from __future__ import annotations

import json
import logging
import re

import prompts
from deps import TurnDeps
from reader import ask_once

log = logging.getLogger("alert_tuner.intake")

MAX_INTAKE_CHARS = 4000
# Words that make a message about this agent's job. When the model still says off_topic for such a message
# it is overruled: the code behind can only read incidents, so nothing off-topic can be answered anyway.
_DOMAIN = re.compile(r"\b(?:alert\w*|incident\w*|noisy|noise|pag(?:e|es|er|ing|ed)|on-?call|flapp\w*|threshold\w*|"
                     r"urgency|firing\w*|fired)\b", re.IGNORECASE)


def _status(text: str) -> str:
    body = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    return str(data.get("status") or "").casefold() if isinstance(data, dict) else ""


async def in_scope(deps: TurnDeps, text: str) -> bool:
    """One tool-less LLM call. Only an explicit `off_topic` answer declines; an unreadable answer lets
    the request through to code that only reads incidents."""
    messages = ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                {"role": "user", "content": prompts.fence_user(text[:MAX_INTAKE_CHARS])})
    status = _status(await ask_once(deps, messages))
    if not status:
        log.warning("intake answer was not readable JSON; treating the request as in scope")
    if status == "off_topic" and _DOMAIN.search(text):
        log.info("intake said off_topic for a message about alerts; treating it as in scope")
        return True
    return status != "off_topic"
