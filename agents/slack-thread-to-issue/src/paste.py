"""Pure: one chat thread pasted into the message ("Name: text" per line, an optional "[10:02]" time and an
optional "#channel" first line) read as a thread, so a user without a chat connection still gets a
proposal. The paste is the evidence: every quote is checked against it."""

from __future__ import annotations

import re

from models import ChatMessage, ThreadCandidate

MIN_MESSAGES = 3
MIN_AUTHORS = 2
_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\[?\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?\]?\s*)?"
                   r"([A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}):\s+(\S.*)$")
_CHANNEL_LINE = re.compile(r"^\s*(?:channel\s*:\s*)?#([A-Za-z0-9][A-Za-z0-9._-]{0,79})\s*:?\s*$", re.IGNORECASE)
_NOT_AUTHORS = frozenset({"channel", "team", "title", "subject", "date", "project", "label", "labels", "note",
                          "current message", "thread", "link"})


def read_paste(text: str) -> ThreadCandidate | None:
    """A thread from a pasted conversation, or None (needs 3+ messages from 2+ people)."""
    channel = ""
    messages: tuple[ChatMessage, ...] = ()
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        named = _CHANNEL_LINE.match(line)
        if named and not messages:
            channel = named.group(1).lower()
            continue
        match = _LINE.match(line)
        if match and match.group(1).casefold() not in _NOT_AUTHORS:
            author = match.group(1).strip()
            messages = (*messages, ChatMessage(author=author, text=match.group(2).strip(), bot=_is_bot(author)))
        elif messages:
            last = messages[-1]
            messages = (*messages[:-1], ChatMessage(last.author, f"{last.text}\n{line.strip()}", last.ts, last.bot))
    authors = {m.author for m in messages if not m.bot}
    if len(messages) < MIN_MESSAGES or len(authors) < MIN_AUTHORS:
        return None
    return ThreadCandidate(id="p1", channel=channel, messages=messages, pasted=True)


def _is_bot(author: str) -> bool:
    word = author.casefold()
    return word.endswith("bot") or word in {"app", "integration"}
