"""Pure: deciding what one turn does, from the state rebuilt out of the history block (`recall.py`) and this
turn's request. A fixed reply (tone saved, envelope re-rendered, off-topic sum, "what should it say?",
"paste it again") or a compose job (a new reply, a revision, or a request with nothing to reply to that the
model classifies). Nothing ever pauses: a missing purpose is asked for in a plain reply, a missing channel
defaults to chat (email when the message has email headers) and the reply says so.

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
from incoming import Incoming
from recall import incoming_dict
from register import detect_channel
from smalltalk import OFF_TOPIC_REPLY
from style import Metrics, measure

REDO_INSTRUCTION = "Rewrite the current reply in my measured style below. Keep the same meaning and facts."
MAX_WORDS_KEPT = 20
DEFAULT_CHANNEL = "chat"
ASK_PURPOSE = ("What should the reply say? For example \"say yes\", \"decline politely\", \"ask for more time\", "
               "\"ask a question\" or \"say thanks\" (or better, in your own words: \"say yes but not before "
               "Thursday\"). Tell me in your next message and I'll draft it.")
CHANNEL_NOTE = ("I wrote it as a chat message (Slack or Teams). Say \"as an email\" or \"as a text\" to "
                "change it.")
REPASTE = ("The reply and the message it answers are no longer in the part of our conversation I can see (long "
           "earlier inputs drop out), so paste the message again and tell me what to say back.")
TRIMMED_NOTE = ("The message you're replying to is no longer in the part of our conversation I can see, so I "
                "revised from the current reply alone.")
_REVISION_CUE = re.compile(r"^\s*(?:make it|say (?:no|yes) instead|more|less|again|redo|shorter|longer|warmer)\b",
                           re.IGNORECASE)
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
    kind: str                                   # fixed | compose
    reply: str = ""
    job: Job | None = None
    updates: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def plan_turn(state: Mapping[str, Any], request: i.Request) -> Plan:
    text = request.text
    parsed = i.parse(text)
    metrics = measure(parsed.examples) if parsed.examples else Metrics.from_dict(state.get("metrics"))
    sign_off_as = parsed.sign_off_as or str(state.get("sign_off_as") or "")
    updates = {"metrics": metrics.to_dict(), "sign_off_as": sign_off_as}
    current = Reply.from_dict(state.get("reply"))
    incoming = parsed.incoming
    if incoming is None and not parsed.examples:
        if _SUM.match(text):
            return Plan("fixed", OFF_TOPIC_REPLY)
        if _JSON_ONLY.match(text) and state.get("envelope"):
            return Plan("fixed", fenced(state["envelope"]))
    if parsed.examples and incoming is None and not parsed.has_purpose and not _REDO.match(text):
        return Plan("fixed", render.tone_saved(metrics, current is not None), updates=updates)
    styled = metrics.with_sign_off(sign_off_as)
    if incoming is not None:
        return _new_reply(parsed, incoming, "", request, styled, updates)
    if current is None and state.get("incoming"):
        # A message pasted earlier: this turn gives its purpose, or (the draft was trimmed from the history)
        # changes it, so the reply is drafted again from the purpose so far plus this turn.
        answer = "\n".join([*(state.get("words") or ()), text])
        return _new_reply(parsed, incoming_from(state["incoming"]), answer, request, styled, updates,
                          str(state.get("channel") or ""))
    if current is None:
        if _REVISION_CUE.match(text) or (_SHORT.search(text) and len(text.split()) <= 4):
            return Plan("fixed", REPASTE)
        job = Job(None, parsed.instruction or text, (text,), request.org, "email", styled)
        return Plan("compose", job=job, updates=updates, notes=parsed.notes)
    return _revision(state, text, parsed, current, request, styled, updates)


def _new_reply(parsed: i.Parsed, incoming: Incoming, answer: str, request: i.Request, metrics: Metrics,
               updates: dict, earlier_channel: str = "") -> Plan:
    """A new reply. `answer` is this turn's text when it gives the purpose for a message pasted earlier."""
    purpose = "\n".join(p for p in (parsed.instruction if parsed.has_purpose and not answer else "", answer) if p)
    if not purpose:
        return Plan("fixed", ASK_PURPOSE, updates=updates)
    channel = parsed.channel or detect_channel(purpose) or earlier_channel
    notes = parsed.notes
    if not channel:
        channel, notes = DEFAULT_CHANNEL, (*notes, CHANNEL_NOTE)
    words = tuple(w for w in (parsed.instruction, answer) if w)
    job = Job(incoming, purpose, words, request.org, channel, metrics, short=bool(_SHORT.search(purpose)),
              want_subject=parsed.want_subject)
    stored = {**updates, "incoming": incoming_dict(incoming), "channel": channel, "words": list(words)}
    return Plan("compose", job=job, updates=stored, notes=notes)


def _revision(state: Mapping[str, Any], text: str, parsed: i.Parsed, current: Reply, request: i.Request,
              metrics: Metrics, updates: dict) -> Plan:
    incoming = incoming_from(state.get("incoming"))
    redo = bool(_REDO.match(text))
    instruction = REDO_INSTRUCTION if redo else text
    channel = (None if redo else detect_channel(text)) or str(state.get("channel") or "email")
    words = tuple([*(state.get("words") or ()), *(() if redo else (text,))][-MAX_WORDS_KEPT:])
    notes = parsed.notes
    if not incoming.text:  # trimmed out of the history: the current reply (already checked) grounds its own facts
        words, notes = (*words, current.draft), (*notes, TRIMMED_NOTE)
    job = Job(incoming, instruction, words, request.org, channel, metrics, current=current,
              relaxed=bool(_RELAXED.search(text)), short=bool(_SHORT.search(text)))
    return Plan("compose", job=job, updates={**updates, "channel": channel, "words": list(words)}, notes=notes)


def incoming_from(data: Mapping[str, Any] | None) -> Incoming:
    data = data or {}
    return Incoming(text=str(data.get("text") or ""), sender=str(data.get("sender") or ""),
                    subject=str(data.get("subject") or ""), is_email=bool(data.get("is_email")))
