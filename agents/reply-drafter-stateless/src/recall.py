"""Pure: the drafting state rebuilt from the platform's history block (this agent stores nothing).

The earlier turns are replayed in order, the way the stateful agent's checkpoint would have been updated:

- a user turn with past replies sets the measured style (the examples are measured, never kept);
- a user turn with an incoming message starts a new reply (incoming, channel, the user's words);
- any other user turn after that adds its words (they ground facts) and may switch the channel;
- an assistant turn rendered by `render.reply_text` (or a fenced JSON envelope) is the current reply.

The history is a budgeted slice, so any of these may be missing (trimmed or compressed): the caller then
works with what is left, says so, or asks for a re-paste. Nothing is invented: the current reply is the
earlier reply's own text, cut out by its markers."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import envelope as env
import inputs as i
import smalltalk
from draft import Reply
from history import USER, HistoryTurn
from register import CHANNELS, detect_channel, register_for
from style import Metrics, measure
from tone_check import Target

MAX_WORDS_KEPT = 20
_HEADER = re.compile(r"^\*\*Reply to (?P<who>.+?) \((?P<channel>[a-z]+)\)\*\*")
_SUBJECT = "**Subject:** "
_SHORTER = "**Shorter:** "
_TONE = "**Tone notes:**"
_FENCED_JSON = re.compile(r"\A\s*```(?:json)?\s*(\{.*\})\s*```\s*\Z", re.DOTALL)


def state_from(turns: Sequence[HistoryTurn]) -> dict:
    state: dict = {"metrics": None, "sign_off_as": "", "incoming": None, "channel": "", "words": [],
                   "reply": None, "envelope": None}
    for turn in turns:
        state = _user(state, turn.text) if turn.role == USER else _assistant(state, turn.text)
    return {**state, "words": state["words"][-MAX_WORDS_KEPT:], "envelope": rebuilt_envelope(state)}


def _user(state: dict, text: str) -> dict:
    if smalltalk.reply_to(text) is not None:
        return state
    parsed = i.parse(text)
    updates: dict = {}
    if parsed.examples:
        updates["metrics"] = measure(parsed.examples).to_dict()
    if parsed.sign_off_as:
        updates["sign_off_as"] = parsed.sign_off_as
    if parsed.incoming is not None:
        words = [parsed.instruction] if parsed.has_purpose else []
        channel = parsed.channel or detect_channel(parsed.instruction) or ""
        return {**state, **updates, "incoming": incoming_dict(parsed.incoming), "channel": channel,
                "words": words, "reply": None}
    if parsed.examples or not (state["incoming"] or state["reply"]):
        return {**state, **updates}
    channel = detect_channel(text) or state["channel"]
    return {**state, **updates, "channel": channel, "words": [*state["words"], text]}


def _assistant(state: dict, text: str) -> dict:
    found = rendered_reply(text)
    if found is None:
        return state
    reply, channel = found
    return {**state, "reply": reply.to_dict(), "channel": channel or state["channel"]}


def rendered_reply(text: str) -> tuple[Reply, str] | None:
    """(the reply, its channel) from an earlier rendered reply or JSON envelope, else None."""
    fenced = _FENCED_JSON.match(text or "")
    if fenced:
        return _from_envelope(fenced.group(1))
    lines = (text or "").strip().splitlines()
    header = _HEADER.match(lines[0]) if lines else None
    if header is None:
        return None
    rest, subject = lines[1:], ""
    if rest and rest[0].startswith(_SUBJECT):
        subject, rest = rest[0][len(_SUBJECT):].strip(), rest[1:]
    end = next((n for n, line in enumerate(rest) if line.startswith((_SHORTER, _TONE))), len(rest))
    draft = "\n".join(rest[:end]).strip()
    shorter = next((line[len(_SHORTER):].strip() for line in rest[end:] if line.startswith(_SHORTER)), "")
    if not draft:
        return None
    channel = header.group("channel") if header.group("channel") in CHANNELS else ""
    return Reply(subject=subject, draft=draft, alt_shorter=shorter or None), channel


def _from_envelope(blob: str) -> tuple[Reply, str] | None:
    try:
        data = json.loads(blob)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("draft"), str) or not data["draft"].strip():
        return None
    channel = data.get("channel") if data.get("channel") in CHANNELS else ""
    return Reply(subject=str(data.get("subject") or ""), draft=data["draft"].strip(),
                 alt_shorter=data.get("alt_shorter") or None), channel


def rebuilt_envelope(state: dict) -> dict | None:
    """The envelope of the current reply, computed in code (for a later "as json"), else None."""
    reply = Reply.from_dict(state.get("reply"))
    if reply is None:
        return None
    metrics = Metrics.from_dict(state.get("metrics")).with_sign_off(state.get("sign_off_as") or "")
    channel = state.get("channel") or "email"
    sender = str((state.get("incoming") or {}).get("sender") or "")
    target = Target(metrics, register_for(channel), sender)
    level = env.confidence(metrics, reply, target, False, False)
    return env.build(reply, channel, sender, metrics, env.tone_notes(metrics, target, reply), level)


def incoming_dict(incoming) -> dict:
    return {"text": incoming.text, "sender": incoming.sender, "subject": incoming.subject,
            "is_email": incoming.is_email}
