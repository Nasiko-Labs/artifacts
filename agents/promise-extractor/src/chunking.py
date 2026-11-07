"""Pure: lay the user's messages out for the model, and group them into chunks on message boundaries.

One pass up to SINGLE_PASS_MAX_CHARS; above it, chunks of about CHUNK_CHARS, never more than MAX_CHUNKS
(the chunk size grows instead). A message is never cut here (messages.py already cut over-long ones)."""

from __future__ import annotations

from collections.abc import Sequence

from messages import Message

SINGLE_PASS_MAX_CHARS = 24_000
CHUNK_CHARS = 12_000
MAX_CHUNKS = 10


def layout(message: Message) -> str:
    """One message as the model sees it: its id, then what code read from its header, then its text."""
    facts = [f"sent {message.sent_on}" if message.sent_on else "", f"to {message.to}" if message.to else ""]
    meta = "; ".join(f for f in facts if f)
    return f"[{message.id}]" + (f" ({meta})" if meta else "") + f"\n{message.text}"


def chunks(messages: Sequence[Message]) -> tuple[tuple[Message, ...], ...]:
    """Groups of messages; a single group when the whole layout fits one pass."""
    if not messages:
        return ()
    sizes = [len(layout(m)) + 2 for m in messages]
    if sum(sizes) <= SINGLE_PASS_MAX_CHARS:
        return (tuple(messages),)
    size = CHUNK_CHARS
    groups = _group(messages, sizes, size)
    while len(groups) > MAX_CHUNKS:
        size += size // 10
        groups = _group(messages, sizes, size)
    return groups


def _group(messages: Sequence[Message], sizes: Sequence[int], limit: int) -> tuple[tuple[Message, ...], ...]:
    groups: list[tuple[Message, ...]] = []
    current: list[Message] = []
    used = 0
    for message, size in zip(messages, sizes, strict=True):
        if current and used + size > limit:
            groups.append(tuple(current))
            current, used = [], 0
        current.append(message)
        used += size
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def text_of(group: Sequence[Message]) -> str:
    return "\n\n".join(layout(m) for m in group)
