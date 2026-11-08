"""The reply draft. `none` is a fixed template (no link, no timing promise, no model call). For `exact`
and `partial` the model writes at most 60 words with the {{LINK}} token; code then strips any URL the
model wrote, fills the token from the index row, repairs a missing token once (then appends the link),
and falls back to a code template when the text states a number, promises a time or runs long. The
staleness note comes from the index's date column, formatted in code."""

from __future__ import annotations

import logging
import re

import prompts
from deps import TurnDeps
from index import staleness_note
from llm import ask_once
from models import Match, Row

log = logging.getLogger("deflector.reply")

MAX_WORDS = 60
_URL = re.compile(r"\(?\s*https?://[^\s)>\]]+\s*\)?")
_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
_TIMING = re.compile(
    r"\b(?:by (?:today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|eod|end of)|within \w+ "
    r"(?:hours?|days?|minutes?)|in (?:a|an|\d+|few|couple of) (?:hours?|days?|minutes?)|asap|end of (?:the )?day|"
    r"eod|later today|this afternoon|first thing)\b", re.IGNORECASE)


def greeting(requester: str | None) -> str:
    return f"Hi {requester}," if requester else "Hi,"


def none_reply(requester: str | None, match: Match) -> str:
    asked = ", ".join(match.missing) or "this"
    return (f"{greeting(requester)} I checked our dashboard index and no existing dashboard covers {asked}. "
            "I'll route it to the data team.")


def template(requester: str | None, match: Match) -> str:
    if match.confidence == "exact":
        return f"{greeting(requester)} the {match.dashboard} dashboard already has this: {prompts.LINK_TOKEN}"
    covers = ", ".join(match.covers) or "part of this"
    missing = ", ".join(match.missing) or "the rest"
    return (f"{greeting(requester)} the {match.dashboard} dashboard covers {covers}: {prompts.LINK_TOKEN} - "
            f"it doesn't have {missing}, so ping me for the rest.")


async def draft(deps: TurnDeps, request: str, requester: str | None, match: Match, row: Row | None) -> str:
    if match.confidence == "none" or row is None:
        return none_reply(requester, match)
    messages = [{"role": "system", "content": prompts.REPLY_SYSTEM},
                {"role": "user", "content": prompts.reply_request(requester, request, match.confidence,
                                                                  row.name, match.covers, match.missing)}]
    text = _strip_urls(await ask_once(deps, messages))
    if prompts.LINK_TOKEN not in text:
        log.info("reply had no link token; asking once for a rewrite")
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": prompts.REPLY_REPAIR}]
        text = _strip_urls(await ask_once(deps, messages))
    if not acceptable(text, request, row, requester):
        log.info("reply failed a code check; using the template")
        text = template(requester, match)
    return finish(text, row, deps)


def finish(text: str, row: Row, deps: TurnDeps) -> str:
    """Fill the first token with the row's url, drop any extra token, append it when missing, add the
    staleness note."""
    body = " ".join(text.split())
    if prompts.LINK_TOKEN in body:
        head, _, tail = body.partition(prompts.LINK_TOKEN)
        body = head + row.url + tail.replace(prompts.LINK_TOKEN, "")
    else:
        body = f"{body.rstrip()} {row.url}"
    body = " ".join(body.split())
    note = staleness_note(row, deps.now.date())
    return f"{body} {note}" if note else body


def acceptable(text: str, request: str, row: Row, requester: str | None) -> bool:
    if not text.strip() or len(text.split()) > MAX_WORDS or _TIMING.search(text):
        return False
    allowed = f"{request} {row.name} {requester or ''}"
    return all(n in allowed for n in _DIGITS.findall(text.replace(prompts.LINK_TOKEN, "")))


def _strip_urls(text: str) -> str:
    return " ".join(_URL.sub(" ", text or "").split())
