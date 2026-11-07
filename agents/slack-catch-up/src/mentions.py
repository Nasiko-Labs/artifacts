"""Pure: mentions of the user, broadcasts and decision signals, decided in code before any model call.

A personal mention is the user's id token, their handle (with or without @) or their display name as a
whole word, in a message someone else wrote. `@here` / `@channel` / `@everyone` are broadcasts, counted
separately, never personal. A reply by someone else in a thread the user started or posted in is a
mention of kind `thread_reply`."""

from __future__ import annotations

import re
from collections.abc import Sequence

from models import Me, Message

QUOTE_CHARS = 200
_BROADCAST = re.compile(r"<!(?:here|channel|everyone)(?:\|[^>]*)?>|(?<![\w@])@(?:here|channel|everyone)\b", re.IGNORECASE)
DECISION_PHRASES = ("we decided", "decided to", "decision:", "decision is", "let's go with", "lets go with",
                    "agreed", "final answer", "approved", "going with", "ship it", "we'll go with", "we will go with")
_CHECK_REACTIONS = frozenset({"white_check_mark", "heavy_check_mark", "ballot_box_with_check"})


def is_mine(message: Message, me: Me) -> bool:
    if me.user_id and message.user_id == me.user_id:
        return True
    names = {n.casefold() for n in (me.handle, me.display_name) if n}
    return message.author.casefold() in names


def is_broadcast(message: Message) -> bool:
    return bool(_BROADCAST.search(message.text))


def mentions_me(message: Message, me: Me) -> bool:
    if not me.known or is_mine(message, me) or not message.is_human:
        return False
    text = _BROADCAST.sub(" ", message.text)
    if me.user_id and f"<@{me.user_id}" in text:
        return True
    return any(_word(name, text) for name in (me.handle, me.display_name) if name)


def _word(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text, re.IGNORECASE) is not None


def decision_signal(message: Message) -> bool:
    lowered = message.text.casefold()
    return any(p in lowered for p in DECISION_PHRASES)


def quote_of(message: Message) -> str:
    """A verbatim prefix of the message, cut at a word boundary."""
    text = message.text.strip()
    if len(text) <= QUOTE_CHARS:
        return text
    cut = text[:QUOTE_CHARS]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def threads_with_me(messages: Sequence[Message], me: Me) -> frozenset[str]:
    """Thread ids (the parent ts) the user started or posted in."""
    return frozenset((m.thread_ts or m.ts) for m in messages if is_mine(m, me) and (m.thread_ts or m.reply_count))


def personal_mentions(messages: Sequence[Message], me: Me) -> tuple[tuple[Message, str], ...]:
    """(message, kind) for every mention of the user, oldest first; kind is "mention" or "thread_reply"."""
    mine = threads_with_me(messages, me)
    out = []
    for m in messages:
        if mentions_me(m, me):
            out.append((m, "mention"))
        elif (m.thread_ts and m.thread_ts != m.ts and m.thread_ts in mine and m.is_human
              and not is_mine(m, me)):
            out.append((m, "thread_reply"))
    return tuple(out)


def threads_to_read(messages: Sequence[Message], me: Me, limit: int) -> tuple[str, ...]:
    """Parent ts of threads with replies worth reading: a mention, a decision signal or the user's own."""
    picked = [m.ts for m in messages
              if m.reply_count > 0 and (not m.thread_ts or m.thread_ts == m.ts)
              and (mentions_me(m, me) or decision_signal(m) or is_mine(m, me))]
    return tuple(dict.fromkeys(picked))[:limit]
