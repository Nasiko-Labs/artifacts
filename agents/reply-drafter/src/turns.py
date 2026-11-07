"""Pure: deciding what one turn does, from the checkpointed state and this turn's request. A fixed reply
(tone saved, envelope re-rendered, off-topic sum), a pause (incoming, purpose, channel, in that order), or
a compose job (a new reply, a revision, or a request with nothing to reply to that the model classifies).

The user's own words ground facts and links; the incoming message grounds facts only; the examples ground
nothing (they are measured in code and never shown to the model)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import inputs as i
import render
from draft import Reply
from drafting import Job
from envelope import fenced
from incoming import Incoming, read_incoming
from register import detect_channel
from smalltalk import OFF_TOPIC_REPLY
from style import Metrics, measure

REDO_INSTRUCTION = "Rewrite the current reply in my measured style below. Keep the same meaning and facts."
MAX_WORDS_KEPT = 20
_RELAXED = re.compile(r"\b(?:more|less) (?:formal|casual|friendly|direct|polite)\b|\bwarmer\b|\bfriendlier\b"
                      r"|\bcooler\b|\bfirmer\b|\bsofter\b|\blonger\b|\bmore detail", re.IGNORECASE)
_SHORT = re.compile(r"\b(?:short(?:er)?|brief(?:er)?|concise|tighter|trim it)\b", re.IGNORECASE)
_REDO = re.compile(r"^\s*(?:redo|re-?do it|redo (?:it|the reply|this)|apply it)\s*[.!]?\s*$", re.IGNORECASE)
_JSON_ONLY = re.compile(r"^\s*(?:(?:show|give|send)(?: me)?(?: it| that| the reply)?\s+)?(?:as json|json only|in json)"
                        r"\s*[.!]?\s*$", re.IGNORECASE)
_SUM = re.compile(r"^\s*(?:what(?:'s| is)\s+|calculate\s+|compute\s+)?[\d\s.]+(?:[-+*/x×÷^][\d\s.()]+)+\s*\??\s*$",
                  re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    kind: str                                   # fixed | pause | compose
    reply: str = ""
    slot: str = ""
    job: Job | None = None
    updates: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def plan_turn(state: Mapping[str, Any], request: i.Request) -> Plan:
    text, slots = request.text, dict(request.slots)
    parsed = i.parse(text)
    metrics = measure(parsed.examples) if parsed.examples else Metrics.from_dict(state.get("metrics"))
    sign_off_as = parsed.sign_off_as or str(state.get("sign_off_as") or "")
    updates = {"metrics": metrics.to_dict(), "sign_off_as": sign_off_as}
    current = Reply.from_dict(state.get("reply"))
    incoming = read_incoming(slots["incoming"]) if slots.get("incoming") else parsed.incoming
    if incoming is None and not parsed.examples:
        if _SUM.match(text):
            return Plan("fixed", OFF_TOPIC_REPLY)
        if _JSON_ONLY.match(text) and state.get("envelope"):
            return Plan("fixed", fenced(state["envelope"]))
    if parsed.examples and incoming is None and not parsed.has_purpose and not _REDO.match(text):
        return Plan("fixed", render.tone_saved(metrics, current is not None), updates=updates)
    styled = metrics.with_sign_off(sign_off_as)
    if incoming is not None:
        return _new_reply(parsed, incoming, slots, request, styled, updates)
    if current is None:
        job = Job(None, parsed.instruction or text, (text,), request.org, "email", styled)
        return Plan("compose", job=job, updates=updates, notes=parsed.notes)
    return _revision(state, text, parsed, current, request, styled, updates)


def _new_reply(parsed: i.Parsed, incoming: Incoming, slots: dict, request: i.Request, metrics: Metrics,
               updates: dict) -> Plan:
    purpose = "\n".join(p for p in (parsed.instruction if parsed.has_purpose else "", slots.get("purpose", "")) if p)
    if not purpose:
        return Plan("pause", slot="purpose", updates=updates)
    channel = slots.get("channel") or parsed.channel or detect_channel(purpose)
    if channel is None:
        return Plan("pause", slot="channel", updates=updates)
    words = tuple(w for w in (parsed.instruction, slots.get("purpose", "")) if w)
    job = Job(incoming, purpose, words, request.org, channel, metrics, short=bool(_SHORT.search(purpose)),
              want_subject=parsed.want_subject)
    stored = {**updates, "incoming": _incoming_dict(incoming), "channel": channel, "words": list(words)}
    return Plan("compose", job=job, updates=stored, notes=parsed.notes)


def _revision(state: Mapping[str, Any], text: str, parsed: i.Parsed, current: Reply, request: i.Request,
              metrics: Metrics, updates: dict) -> Plan:
    incoming = incoming_from(state.get("incoming"))
    redo = bool(_REDO.match(text))
    instruction = REDO_INSTRUCTION if redo else text
    channel = (None if redo else detect_channel(text)) or str(state.get("channel") or "email")
    words = tuple([*(state.get("words") or ()), *(() if redo else (text,))][-MAX_WORDS_KEPT:])
    job = Job(incoming, instruction, words, request.org, channel, metrics, current=current,
              relaxed=bool(_RELAXED.search(text)), short=bool(_SHORT.search(text)))
    return Plan("compose", job=job, updates={**updates, "channel": channel, "words": list(words)},
                notes=parsed.notes)


def _incoming_dict(incoming: Incoming) -> dict:
    return {"text": incoming.text, "sender": incoming.sender, "subject": incoming.subject,
            "is_email": incoming.is_email}


def incoming_from(data: Mapping[str, Any] | None) -> Incoming:
    data = data or {}
    return Incoming(text=str(data.get("text") or ""), sender=str(data.get("sender") or ""),
                    subject=str(data.get("subject") or ""), is_email=bool(data.get("is_email")))
