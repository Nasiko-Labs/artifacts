"""Pure: tone notes, confidence and the output envelope, all computed in code from the measured style and
the final checks (never from the model's own words)."""

from __future__ import annotations

import json

import textstats as ts
import tone_check
from draft import Reply
from grounding import placeholders
from register import register_for
from style import FORMALITY, NONE, Metrics

_LENGTH_CHECKS = ("length", "cap")


def final_violations(reply: Reply, target: tone_check.Target) -> list[tone_check.Violation]:
    return tone_check.violations(reply.draft, target)


def tone_notes(m: Metrics, target: tone_check.Target, reply: Reply) -> str:
    left = final_violations(reply, target)
    if not m.measured:
        return ("Neutral default tone (no past replies given): formality 3/5, opens with 'Hi {first},'. "
                "Paste 1-3 of your past replies and I'll match your style.")
    plural = "s" if m.examples != 1 else ""
    style = [f"{FORMALITY[m.formality]} ({m.formality}/5, approx.)", f"about {m.avg_words} words"]
    if m.greeting != NONE:
        style.append(f"opens with '{m.greeting}'")
    if m.sign_off != NONE:
        style.append(f"signs '{m.sign_off.replace(chr(10), ' ')}'")
    style.append("emoji" if m.emoji else "no emoji")
    style.append(_contractions(m))
    lead = f"Matched to your {m.examples} example{plural}: " + ", ".join(style) + "."
    if target.relaxed:
        return lead + " You asked for a different style this turn, so it was a starting point, not a rule."
    failed = {v.check for v in left}
    checked = ", ".join(f"{name} {'off' if name in failed else 'ok'}"
                        for name in ("length", "greeting", "sign-off", "contractions", "exclamations", "emoji"))
    notes = [lead, f"Checked: {checked}."]
    words = ts.word_count(reply.draft)
    if "length" in failed:
        side = "Longer" if words > m.avg_words else "Shorter"
        notes.append(f"{side} than your examples: {words} words vs ~{m.avg_words}.")
    notes += [v.message[:1].upper() + v.message[1:] + "." for v in left if v.check not in ("length",)]
    return " ".join(notes)


def _contractions(m: Metrics) -> str:
    if m.contraction_rate == 0:
        return "no contractions"
    return f"1 contraction per {max(1, round(100 / m.contraction_rate))} words"


def confidence(m: Metrics, reply: Reply, target: tone_check.Target, code_fixed: bool,
               ungrounded_after_repair: bool) -> str:
    left = {v.check for v in final_violations(reply, target)}
    if not m.measured or ungrounded_after_repair or left & set(_LENGTH_CHECKS):
        return "low"
    if m.examples >= 2 and not code_fixed and not left and not placeholders(_all_text(reply)):
        return "high"
    return "medium"


def _all_text(reply: Reply) -> str:
    return "\n".join((reply.subject, reply.draft, reply.alt_shorter or ""))


def summary(reply_to: str, channel: str, m: Metrics) -> str:
    who = reply_to or "the sender"
    label = register_for(channel).label.split()[0].lower()
    if not m.measured:
        return f"Reply to {who} ({label}), neutral default tone."
    plural = "s" if m.examples != 1 else ""
    return f"Reply to {who} ({label}), tone matched to {m.examples} example{plural}."


def build(reply: Reply, channel: str, sender: str, m: Metrics, notes: str, level: str) -> dict:
    items = [{"label": "draft", "text": reply.draft, "words": ts.word_count(reply.draft)}]
    if reply.alt_shorter:
        items.append({"label": "alt_shorter", "text": reply.alt_shorter, "words": ts.word_count(reply.alt_shorter)})
    return {"summary": summary(sender, channel, m), "items": items, "draft": reply.draft,
            "alt_shorter": reply.alt_shorter, "tone_notes": notes, "subject": reply.subject or None,
            "channel": channel, "reply_to": sender or None, "placeholders": placeholders(_all_text(reply)),
            "asks_unaddressed": [{"ask": a.ask, "quote": a.quote} for a in reply.asks],
            "confidence": level, "needs_human": True}


def fenced(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
