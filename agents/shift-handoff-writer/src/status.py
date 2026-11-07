"""Pure: an item's status by fixed rules over the notes (first rule wins), never the model's opinion.

1. resolved: an entry about the item says resolved/fixed/closed/done/... and no LATER entry about it reopens
   it (reopened/again/still/back/regressed/not fixed); a user's "mark #4812 resolved" also resolves it.
2. waiting: its latest entry (or its status words) says waiting on/for, pending, blocked, on hold, awaiting,
   needs input/reply/approval.
3. open: everything else.
In an entry that names several tickets, only the sentences naming this item's ticket are read."""

from __future__ import annotations

import re
from dataclasses import replace

from entries import Entry, ref_key
from items import OPEN, RESOLVED, WAITING, Item

WAITING_ON_MAX_WORDS = 8

RESOLVED_CUE = re.compile(r"\b(?:resolved|fixed|closed|done|sorted|completed|confirmed working|merged)\b", re.IGNORECASE)
REOPEN_CUE = re.compile(r"\b(?:reopened|re-opened|again|still|regressed|(?:is|it's|came|broke|went|it) back|"
                        r"not (?:fixed|resolved|working|sorted))\b"
                        r"|\bnot\s+\w+\s+(?:fixed|resolved)\b", re.IGNORECASE)
WAITING_CUE = re.compile(r"\b(?:waiting (?:on|for)|pending|blocked|on hold|awaiting|needs? (?:input|reply|approval))\b",
                         re.IGNORECASE)
_WAITING_ON = re.compile(r"\b(?:waiting (?:on|for)|awaiting|blocked (?:on|by)|pending)\s+([^.;,\n()]+)", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.;!?])\s+|\s+-\s+")


def relevant_text(entry: Entry, item: Item) -> str:
    """The whole entry, or, when it names several tickets, only the sentences naming this item's."""
    if len(entry.refs) <= 1:
        return entry.text
    keys = {ref_key(r) for r in item.refs}
    sentences = [s for s in _SENTENCE.split(entry.text) if any(k in s.upper() for k in keys)]
    return " ".join(sentences) or entry.text


def item_texts(item: Item, by_id: dict[str, Entry]) -> list[str]:
    return [relevant_text(by_id[e], item) for e in item.entries if e in by_id]


def is_resolved(texts: list[str]) -> bool:
    last_resolve = None
    for index, text in enumerate(texts):
        if RESOLVED_CUE.search(text) and not REOPEN_CUE.search(text):
            last_resolve = index
    if last_resolve is None:
        return False
    return not any(REOPEN_CUE.search(t) for t in texts[last_resolve + 1:])


def waiting_on(text: str) -> str | None:
    match = _WAITING_ON.search(text or "")
    if not match:
        return None
    words = match.group(1).split()[:WAITING_ON_MAX_WORDS]
    return " ".join(words) or None


def classify(item: Item, by_id: dict[str, Entry]) -> Item:
    texts = item_texts(item, by_id)
    if item.marked == RESOLVED or is_resolved(texts):
        return replace(item, status=RESOLVED, waiting_on=None)
    latest = " ".join(t for t in (texts[-1] if texts else "", item.status_words or "") if t)
    if WAITING_CUE.search(latest):
        return replace(item, status=WAITING, waiting_on=waiting_on(latest))
    return replace(item, status=OPEN, waiting_on=None)
