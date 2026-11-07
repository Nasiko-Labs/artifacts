"""The code checks: the guarantee, not the prompt.

An item survives only when its quote is found in the message it cites (or, re-pointed, in exactly one
other message) and every number, date word, email, URL and name in its text is in the thread. Owners
are resolved in owners.py; a due phrase must be written in its message or the two after it. Then dedup
and caps. Every drop is counted."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding as g
import owners
from model import Action, Decision, Question
from thread import Thread

STATUSES = ("ok", "off_topic")
SECTIONS = ("decisions", "open_questions", "action_items", "reversals")
WORD_CAPS = {"decision": 30, "question": 25, "action": 25, "reversal": 25}
QUOTE_MAX_CHARS = 200
FIELD_MAX_CHARS = 2_000
DUE_FOLLOWING_MESSAGES = 2
NULLS = frozenset({"", "null", "none", "n/a", "not stated", "unknown", "tbd"})

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_HTML = re.compile(r"<[^>]{1,500}>")
_SPACES = re.compile(r"\s+")
_CLAUSE_ENDS = (". ", "; ", ", ", " - ", ": ")


@dataclass(frozen=True)
class Part:
    """One call's checked items. `reversals` are raw: they are verified once all decisions are known."""

    decisions: tuple[Decision, ...] = ()
    questions: tuple[Question, ...] = ()
    actions: tuple[Action, ...] = ()
    reversals: tuple[dict, ...] = ()
    tldr: str = ""
    kept: int = 0
    dropped: int = 0


def parse_extract(data: Any) -> dict | None:
    """The model's JSON, shape-checked; None when it does not match the contract (triggers a repair)."""
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    parsed: dict = {"status": data["status"], "tldr": str(data.get("tldr") or "")}
    for key in SECTIONS:
        entries = data.get(key) or []
        if not isinstance(entries, list) or not all(isinstance(e, Mapping) for e in entries):
            return None
        parsed[key] = [dict(e) for e in entries]
    return parsed


def clean_text(text: Any, source: str) -> str:
    """One line of model text with images and HTML removed; a link stays only if its URL is in the source."""
    value = _IMAGE.sub("", str(text or "")[:FIELD_MAX_CHARS])
    value = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), value)
    value = _HTML.sub("", value)
    return _SPACES.sub(" ", value.replace("|", "/").replace("~~", "")).strip()


def anchor(quote: str, message_id: str, thread: Thread) -> str | None:
    """The id of the message holding `quote`: the cited one, else the only other one that holds it."""
    cited = thread.get(message_id)
    if cited is not None and not cited.noise and g.locate(quote, g.normalise(cited.text)) is not None:
        return cited.id
    holders = [m.id for m in thread.content if g.locate(quote, g.normalise(m.text)) is not None]
    return holders[0] if len(holders) == 1 else None


def check_part(parsed: Mapping[str, Any], thread: Thread, prefix: str) -> Part:
    full = "\n".join(m.text for m in thread.messages)
    norm = g.normalise(full)
    decisions = [_decision(e, thread, full, norm, f"{prefix}{e.get('id') or i}")
                 for i, e in enumerate(parsed.get("decisions") or (), start=1)]
    questions = [_question(e, thread, full, norm) for e in parsed.get("open_questions") or ()]
    actions = [_action(e, thread, full, norm) for e in parsed.get("action_items") or ()]
    answered = sum(1 for q in questions if q == "answered")
    questions = [q for q in questions if q != "answered"]
    found = [*decisions, *questions, *actions]
    kept = [x for x in found if x is not None]
    reversals = tuple({**dict(r), "earlier_decision_id": f"{prefix}{r.get('earlier_decision_id')}"}
                      for r in parsed.get("reversals") or ())
    return Part(decisions=tuple(d for d in decisions if d), questions=tuple(q for q in questions if q),
                actions=tuple(a for a in actions if a), reversals=reversals, tldr=str(parsed.get("tldr") or ""),
                kept=len(kept) + answered, dropped=len(found) - len(kept))


def _quoted(entry: Mapping[str, Any], id_field: str, thread: Thread) -> tuple[str, str] | None:
    quote = _SPACES.sub(" ", str(entry.get("quote") or "")).strip()
    at = anchor(quote, str(entry.get(id_field) or ""), thread) if quote else None
    return None if at is None else (at, quote)


def _decision(entry, thread: Thread, full: str, norm: str, key: str) -> Decision | None:
    text, anchored = clean_text(entry.get("text"), full), _quoted(entry, "message_id", thread)
    if not text or anchored is None or g.ungrounded_tokens(text, norm):
        return None
    message = thread.get(anchored[0])
    return Decision(key=key, text=cut_words(text, WORD_CAPS["decision"]), message_id=message.id,
                    speaker=message.speaker, quote=cap_quote(anchored[1]))


def _question(entry, thread: Thread, full: str, norm: str) -> Question | str | None:
    text, anchored = clean_text(entry.get("text"), full), _quoted(entry, "asked_by_message", thread)
    if not text or anchored is None or g.ungrounded_tokens(text, norm):
        return None
    message = thread.get(anchored[0])
    answer_at, asked_at = thread.index(str(entry.get("answered_in") or "")), thread.index(message.id)
    if answer_at is not None and answer_at > asked_at:
        return "answered"
    return Question(text=cut_words(text, WORD_CAPS["question"]), message_id=message.id, asked_by=message.speaker,
                    quote=cap_quote(anchored[1]))


def _action(entry, thread: Thread, full: str, norm: str) -> Action | None:
    task, anchored = clean_text(entry.get("task"), full), _quoted(entry, "message_id", thread)
    if not task or anchored is None or g.ungrounded_tokens(task, norm):
        return None
    message = thread.get(anchored[0])
    owner = owners.resolve(entry.get("owner_ref"), entry.get("owner_name"), message, anchored[1], thread.speakers)
    return Action(task=cut_words(task, WORD_CAPS["action"]), owner=owner,
                  due=grounded_due(clean_text(entry.get("due"), full), thread, message.id),
                  message_id=message.id, quote=cap_quote(anchored[1]))


def grounded_due(due: str, thread: Thread, message_id: str) -> str:
    """The due phrase as written, only when it appears in the message or the two after it."""
    if due.casefold().strip(" .") in NULLS:
        return ""
    at = thread.index(message_id)
    window = thread.messages[at:at + 1 + DUE_FOLLOWING_MESSAGES] if at is not None else ()
    wanted = g.normalise(due).strip(" .,;:")
    return due if wanted and any(wanted in g.normalise(m.text) for m in window) else ""


def cut_words(text: str, cap: int) -> str:
    """At most `cap` words, cut back to the last clause boundary in the second half, then "…"."""
    words = text.split()
    if len(words) <= cap:
        return text
    head = " ".join(words[:cap])
    cut = max(head.rfind(mark) for mark in _CLAUSE_ENDS)
    if cut >= len(head) // 2:
        head = head[:cut]
    return head.rstrip(" ,;:-.") + "…"


def cap_quote(quote: str) -> str:
    return quote if len(quote) <= QUOTE_MAX_CHARS else quote[:QUOTE_MAX_CHARS].rstrip() + "…"


def dedup(items: Iterable, text_of, owner_of=lambda _item: "") -> tuple:
    seen: set[tuple[str, str]] = set()
    kept = []
    for item in items:
        key = (g.normalise(text_of(item)).strip(" .…"), g.normalise(owner_of(item)))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return tuple(kept)


def by_message(items: Sequence, thread: Thread) -> tuple:
    """Stable order by the message each item came from (code orders; the model never does)."""
    return tuple(sorted(items, key=lambda item: thread.index(item.message_id) or 0))
