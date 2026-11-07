"""Pure: the thread as numbered lines for the model, and chunks of them on message boundaries.

A message is never split: a message longer than a chunk is its own chunk. Ids stay global ([M17] in any
chunk). Noise messages (reactions, joins) are never sent."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from thread import Message, Thread

SINGLE_PASS_MAX_CHARS = 24_000
CHUNK_CHARS = 12_000
MAX_CHUNKS = 10


@dataclass(frozen=True)
class Chunk:
    messages: tuple[Message, ...]

    @property
    def block(self) -> str:
        return "\n".join(line(m) for m in self.messages)

    @property
    def size(self) -> int:
        return len(self.block)

    @property
    def first(self) -> str:
        return self.messages[0].id

    @property
    def last(self) -> str:
        return self.messages[-1].id


def line(message: Message) -> str:
    when = f" ({message.time_label})" if message.time_label else ""
    return f"[{message.id}] {message.speaker}{when}: {message.text}"


def block(messages: Sequence[Message]) -> str:
    return "\n".join(line(m) for m in messages)


def chunks(thread: Thread) -> tuple[Chunk, ...]:
    content = thread.content
    total = sum(len(line(m)) + 1 for m in content)
    if total <= SINGLE_PASS_MAX_CHARS:
        return (Chunk(content),) if content else ()
    size = max(CHUNK_CHARS, math.ceil(total / MAX_CHUNKS))
    packed = _pack(content, size)
    while len(packed) > MAX_CHUNKS:  # boundary packing ran short: widen until the call budget holds
        size += size // 10
        packed = _pack(content, size)
    return packed


def _pack(messages: Sequence[Message], size: int) -> tuple[Chunk, ...]:
    groups: list[tuple[Message, ...]] = []
    current: tuple[Message, ...] = ()
    used = 0
    for message in messages:
        length = len(line(message)) + 1
        if current and used + length > size:
            groups.append(current)
            current, used = (), 0
        current, used = (*current, message), used + length
    if current:
        groups.append(current)
    return tuple(Chunk(g) for g in groups)
