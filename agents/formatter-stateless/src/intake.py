"""Pure: what one message asks for, decided in code (there is no model).

Order: greeting -> size -> JSON (render) -> broken JSON (reply: fix and paste again) -> follow-up words on
the input recalled from the history -> a Markdown conversion request -> anything else is outside scope
(fixed decline + intro).

Stateless: nothing is stored. A follow-up ("as slack", "compact") re-renders the newest JSON or Markdown the
user pasted that is still in the platform's history slice, with the options of that paste and of every
follow-up after it replayed in order. Nothing ever pauses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import reduce
from typing import Any

import followup
import parse
import replies
import smalltalk
from history import USER, Turn
from inputs import MAX_CHARS, Incoming
from model import Options

_FORMAT_INTENT = re.compile(
    r"\b(?:format|convert|render|turn|transform)\b|\b(?:as|to|into|for|in)\s+(?:a\s+)?"
    r"(?:slack|e-?mail|html|markdown|md|mrkdwn|block ?kit|blocks)\b", re.IGNORECASE)
HEAD_MAX_CHARS = 200


@dataclass(frozen=True)
class Plan:
    route: str                       # reply | values | markdown
    reply: str = ""
    values: tuple[Any, ...] = ()
    md_source: str = ""
    opts: Options = field(default_factory=Options)
    as_json: bool = False
    plain_version: bool = False


def plan_turn(incoming: Incoming) -> Plan:
    text = incoming.text.strip()
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Plan("reply", reply=canned)
    if len(text) > MAX_CHARS:
        return Plan("reply", reply=replies.TOO_BIG.format(chars=len(text), limit=MAX_CHARS))
    extracted = parse.extract(text)
    if extracted.values:
        return _new_input(extracted, extracted.instruction, incoming.wants_json)
    if extracted.error is not None:
        return Plan("reply", reply=replies.JSON_ERROR.format(error=extracted.error.describe()))
    if followup.is_followup_only(text):
        return _followup(text, incoming.history, incoming.wants_json)
    request = _markdown(text, incoming.wants_json)
    return request or Plan("reply", reply=replies.OFF_TOPIC_REPLY)


def _markdown(text: str, wants_json: bool) -> Plan | None:
    request = markdown_request(text)
    if request is None:
        return None
    head, body = request
    ask = followup.read(head)
    return Plan("markdown", md_source=body, opts=followup.apply(ask, Options()), as_json=ask.as_json or wants_json)


def recall(turns: tuple[Turn, ...]) -> Plan | None:
    """The newest user paste still in the history, rendered with its own options and those of the follow-ups
    after it. None when there is none, or when the newest one is unusable (compressed, or JSON that no
    longer parses because it was cut): an older paste is never used in its place."""
    users = [t for t in turns if t.role == USER]
    for index in range(len(users) - 1, -1, -1):
        text = users[index].text.strip()
        if followup.is_followup_only(text) or smalltalk.reply_to(text) is not None:
            continue
        if users[index].compressed:
            return None
        extracted = parse.extract(text)
        if extracted.error is not None:
            return None
        found = _new_input(extracted, extracted.instruction, False) if extracted.values else _markdown(text, False)
        if found is None:
            continue
        later = (followup.read(t.text) for t in users[index + 1:] if followup.is_followup_only(t.text.strip()))
        return replace(found, opts=reduce(lambda opts, ask: followup.apply(ask, opts), later, found.opts))
    return None


def _new_input(extracted: parse.Extracted, instruction: str, wants_json: bool) -> Plan:
    ask = followup.read(instruction)
    if not ask.target and extracted.target:
        ask = followup.Ask(target=followup.target_of(extracted.target), compact=ask.compact, footer=ask.footer,
                           title=ask.title, as_json=ask.as_json, plain_version=ask.plain_version)
    return Plan("values", values=extracted.values, opts=followup.apply(ask, Options()),
                as_json=ask.as_json or wants_json, plain_version=ask.plain_version)


def _followup(text: str, turns: tuple[Turn, ...], wants_json: bool) -> Plan:
    found = recall(turns)
    if found is None:
        return Plan("reply", reply=replies.NOT_IN_HISTORY if turns else replies.NO_JSON)
    ask = followup.read(text)
    return replace(found, opts=followup.apply(ask, found.opts), as_json=ask.as_json or wants_json,
                   plain_version=ask.plain_version)


def markdown_request(text: str) -> tuple[str, str] | None:
    """(instruction, markdown body) when the message asks to format pasted Markdown, else None.
    The instruction is the first line up to a colon ("format as slack: **bold** ...") or the whole
    first line when the body follows on the next lines."""
    first, _, rest = text.partition("\n")
    head, colon, after = first.partition(":")
    if colon and len(head) <= HEAD_MAX_CHARS and _FORMAT_INTENT.search(head):
        body = f"{after}\n{rest}".strip()
        return (head, body) if body else None
    if len(first) <= HEAD_MAX_CHARS and _FORMAT_INTENT.search(first) and rest.strip():
        return first, rest.strip()
    return None
