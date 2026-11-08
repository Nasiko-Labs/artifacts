"""Pure: the four operations behind a turn (split, append, end, merge). Each returns the state update
for the graph: this turn's `reply`/`envelope`/`pause` plus what to keep for follow-ups."""

from __future__ import annotations

import accumulate
import render
import replies
from envelope import Pasted
from hitl import NEED_ENVELOPES, NEED_TEXT, OVER_CAP, Pending
from merge import merge
from split import SplitOptions, split_text

CLEARED_HOLD = {"held": [], "held_opts": {}}


def split(text: str, opts: SplitOptions) -> dict:
    if len(text) > accumulate.CAP_CHARS:
        kept = accumulate.cap_cut(text)
        note = f"This text is {len(text):,} characters; I can hold the first {len(kept):,}."
        return {"held": accumulate.to_pieces(kept), "held_opts": opts.to_dict(),
                "pause": Pending(OVER_CAP, opts=opts.to_dict(), note=note).to_metadata()}
    result = split_text(text, opts)
    return {"reply": render.split_reply(result), "envelope": result.envelope(), "source": accumulate.to_pieces(text),
            "split_opts": opts.to_dict(), "last": "split", **CLEARED_HOLD}


def split_request(state: dict, payload: str, opts: SplitOptions) -> dict:
    if payload:
        return split(payload, opts)
    if state.get("held"):
        return end(state, "", opts)
    return {"pause": Pending(NEED_TEXT, opts=opts.to_dict()).to_metadata()}


def append(state: dict, payload: str, opts: SplitOptions) -> dict:
    held_opts = SplitOptions.from_dict(state.get("held_opts")).overlay(opts)
    if not payload:
        return {"reply": replies.NEED_TEXT_WITH_PART, "held_opts": held_opts.to_dict()}
    added = accumulate.append(state.get("held"), payload)
    if added.over_cap:
        return _over_cap(added, held_opts)
    total = len(accumulate.joined(added.pieces))
    return {"reply": replies.APPENDED.format(added=added.added, total=total), "held": list(added.pieces),
            "held_opts": held_opts.to_dict()}


def end(state: dict, payload: str, opts: SplitOptions) -> dict:
    held_opts = SplitOptions.from_dict(state.get("held_opts")).overlay(opts)
    pieces = state.get("held") or []
    if payload:
        added = accumulate.append(pieces, payload)
        if added.over_cap:
            return _over_cap(added, held_opts)
        pieces = list(added.pieces)
    text = accumulate.joined(pieces)
    if not text:
        return {"reply": replies.NOTHING_HELD}
    return split(text, held_opts)


def merge_values(pasted: Pasted, base: tuple = ()) -> dict:
    result = merge([*base, *pasted.values], pasted.problems)
    if result.status == "need_more":
        note = " ".join(result.notes)
        return {"pause": Pending(NEED_ENVELOPES, envelopes=result.objects, note=note).to_metadata()}
    if result.status == "different":
        return {"reply": "\n\n".join([replies.DIFFERENT_AGENTS, *result.notes])}
    return {"reply": render.merge_reply(result), "envelope": result.envelope, "merge_set": list(result.objects),
            "last": "merge"}


def _over_cap(added: accumulate.Appended, opts: SplitOptions) -> dict:
    kept = len(accumulate.joined(added.pieces))
    note = replies.OVER_CAP.format(limit=accumulate.CAP_CHARS, total=added.would_be, kept=kept)
    return {"held": list(added.pieces), "held_opts": opts.to_dict(),
            "pause": Pending(OVER_CAP, opts=opts.to_dict(), note=note).to_metadata()}
