"""Pure: the last pasted text per competitor and source, kept in the conversation's checkpointer. It is the
baseline for "what's new since last time"; it does not survive a new conversation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from records import Block

MAX_SNAPSHOT_CHARS = 100_000


def baseline(snapshots: Mapping[str, str], block: Block) -> tuple[str | None, str]:
    """The text to compare with, and where it came from: `before`, `snapshot` or `none`."""
    if block.before:
        return block.before, "before"
    stored = snapshots.get(block.key)
    return (stored, "snapshot") if stored else (None, "none")


def replaced(snapshots: Mapping[str, str], blocks: Iterable[Block]) -> dict[str, str]:
    """A new mapping: each block's current text replaces the stored one."""
    return {**snapshots, **{b.key: b.body[:MAX_SNAPSHOT_CHARS] for b in blocks}}
