"""Pure: what one message asks for, decided in code (there is no model).

Order: greeting -> size -> JSON (render) -> broken JSON (pause) -> follow-up words on the stored input
-> a Markdown conversion request -> anything else is outside scope (fixed decline + intro)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import followup
import hitl
import parse
import replies
import smalltalk
from inputs import MAX_CHARS, Incoming
from model import Options

_FORMAT_INTENT = re.compile(
    r"\b(?:format|convert|render|turn|transform)\b|\b(?:as|to|into|for|in)\s+(?:a\s+)?"
    r"(?:slack|e-?mail|html|markdown|md|mrkdwn|block ?kit|blocks)\b", re.IGNORECASE)
HEAD_MAX_CHARS = 200


@dataclass(frozen=True)
class Plan:
    route: str                       # reply | pause | values | markdown
    reply: str = ""
    pending: hitl.Pending | None = None
    values: tuple[Any, ...] = ()
    md_source: str = ""
    opts: Options = field(default_factory=Options)
    as_json: bool = False
    plain_version: bool = False


def plan_turn(incoming: Incoming, stored: dict) -> Plan:
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
        pending = hitl.Pending(hitl.JSON_ERROR, instruction=extracted.instruction,
                               error=extracted.error.describe())
        return Plan("pause", pending=pending)
    if followup.is_followup_only(text):
        return _followup(text, stored, incoming.wants_json)
    request = markdown_request(text)
    if request is not None:
        head, body = request
        ask = followup.read(head)
        return Plan("markdown", md_source=body, opts=followup.apply(ask, Options()),
                    as_json=ask.as_json or incoming.wants_json)
    return Plan("reply", reply=replies.OFF_TOPIC_REPLY)


def plan_resume(pending: hitl.Pending, incoming: Incoming) -> Plan:
    """The answer to the broken-JSON question: the corrected JSON, or a cancel. One pause only."""
    if hitl.is_cancel(incoming.text):
        return Plan("reply", reply=replies.CANCELLED)
    extracted = parse.extract(incoming.text.strip())
    if extracted.error is not None:
        return Plan("reply", reply=replies.JSON_ERROR.format(error=extracted.error.describe()))
    if not extracted.values:
        return Plan("reply", reply=replies.CANCELLED)
    return _new_input(extracted, f"{pending.instruction} {extracted.instruction}", incoming.wants_json)


def _new_input(extracted: parse.Extracted, instruction: str, wants_json: bool) -> Plan:
    ask = followup.read(instruction)
    if not ask.target and extracted.target:
        ask = followup.Ask(target=followup.target_of(extracted.target), compact=ask.compact, footer=ask.footer,
                           title=ask.title, as_json=ask.as_json, plain_version=ask.plain_version)
    return Plan("values", values=extracted.values, opts=followup.apply(ask, Options()),
                as_json=ask.as_json or wants_json, plain_version=ask.plain_version)


def _followup(text: str, stored: dict, wants_json: bool) -> Plan:
    ask = followup.read(text)
    opts = followup.apply(ask, followup.options_from_state(stored.get("opts")))
    flags = {"opts": opts, "as_json": ask.as_json or wants_json, "plain_version": ask.plain_version}
    if stored.get("values"):
        return Plan("values", values=tuple(stored["values"]), **flags)
    if stored.get("md_source"):
        return Plan("markdown", md_source=stored["md_source"], **flags)
    return Plan("reply", reply=replies.NO_JSON)


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
