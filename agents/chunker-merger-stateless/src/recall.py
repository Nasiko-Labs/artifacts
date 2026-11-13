"""Pure: what follow-ups work on, recovered from the platform's history slice (the agent stores nothing).

A split is deterministic, so "show part 3", "list the parts", "as json" and "split again with 10,000 chars"
recompute it from the newest user message that carried text to split, with that message's options and the
"split again" options after it replayed in order. A merge is recomputed from the user messages that pasted
its results ("merge these" and every "merge with this" after it). A source message the platform compressed
(it carries the "[⋯ " sigil) counts as lost; an older one is never used in its place.

`state()` returns the same keys the checkpointer used to hold, so followup.py works on it unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import accumulate
from classify import Request, classify
from envelope import extract_values
from history import USER, Turn
from split import SplitOptions

SOURCE_KINDS = frozenset({"split", "append", "end"})
MERGE_KINDS = frozenset({"merge", "merge_more"})
MIN_TEXT_CHARS = 40


@dataclass(frozen=True)
class Recalled:
    source: str = ""
    split_opts: SplitOptions = field(default_factory=SplitOptions)
    merge_set: tuple = ()
    last: str = ""                  # split | merge: which came last
    split_lost: bool = False        # the newest split's text is compressed in the history
    merge_lost: bool = False

    def state(self) -> dict:
        return {"source": accumulate.to_pieces(self.source), "split_opts": self.split_opts.to_dict(),
                "merge_set": list(self.merge_set), "last": self.last}


def _requests(turns: tuple[Turn, ...]) -> list[tuple[Turn, Request]]:
    out: list[tuple[Turn, Request]] = []
    for turn in (t for t in turns if t.role == USER):
        previous = out[-1] if out else None
        out.append((turn, resolve(classify(turn.text), turn.text, previous)))
    return out


def resolve(request: Request, text: str, previous: tuple[Turn, Request] | None) -> Request:
    """A plain paste (no cue) right after a split with no text, or a merge with fewer than two results, is
    that request's missing input: the stateless stand-in for answering a question."""
    if request.kind != "decline" or previous is None:
        return request
    turn, before = previous
    if before.kind == "split" and not before.payload and len(text.strip()) >= MIN_TEXT_CHARS:
        return Request("split", payload=text.strip(), opts=before.opts)
    merge_short = before.kind == "merge" and len(extract_values(before.payload).values) < 2
    if merge_short and not turn.compressed and extract_values(text).values:
        return Request("merge_more", payload=text)
    return request


def recall(turns: tuple[Turn, ...]) -> Recalled:
    asked = _requests(turns)
    split_at = next((n for n in range(len(asked) - 1, -1, -1)
                     if asked[n][1].kind in SOURCE_KINDS and asked[n][1].payload), None)
    merge_at = next((n for n in range(len(asked) - 1, -1, -1) if asked[n][1].kind in MERGE_KINDS), None)
    newest_merge = -1 if merge_at is None else merge_at
    newest_split = -1 if split_at is None else split_at
    found = Recalled(last="merge" if newest_merge > newest_split else "split" if split_at is not None else "")
    if split_at is not None:
        found = _with_split(found, asked, split_at)
    if merge_at is not None:
        found = _with_merge(found, asked, merge_at)
    return found


def _with_split(found: Recalled, asked: list, at: int) -> Recalled:
    turn, request = asked[at]
    if turn.compressed:
        return replace(found, split_lost=True)
    opts = request.opts
    for _, later in asked[at + 1:]:
        if later.kind == "resplit":
            opts = opts.overlay(later.opts)
    return replace(found, source=request.payload, split_opts=opts)


def _with_merge(found: Recalled, asked: list, at: int) -> Recalled:
    values: list = []
    for turn, request in reversed(asked[:at + 1]):
        if request.kind not in MERGE_KINDS:
            continue
        if turn.compressed:
            return replace(found, merge_lost=True)
        values = [*extract_values(request.payload).values, *values]
        if request.kind == "merge":
            return replace(found, merge_set=tuple(values))
    return replace(found, merge_lost=True)  # "merge with this" whose first merge is gone



def previous_request(turns: tuple[Turn, ...]) -> tuple[Turn, Request] | None:
    asked = _requests(turns)
    return asked[-1] if asked else None
