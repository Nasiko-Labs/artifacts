"""Pure: the operations behind a turn (split, a piece of a longer text, merge). Each returns this turn's
`reply` and `envelope`. Stateless: nothing is held between messages and nothing ever pauses; a missing
input or a too-long text gets a plain reply (see PLAN.md, "Stateless variant")."""

from __future__ import annotations

import accumulate
import render
import replies
from envelope import Pasted
from merge import merge
from split import SplitOptions, split_text


def split(text: str, opts: SplitOptions, notes: tuple[str, ...] = ()) -> dict:
    """Over the cap: the first part is split and the reply says how much was left out."""
    if len(text) > accumulate.CAP_CHARS:
        kept = accumulate.cap_cut(text)
        cut = replies.OVER_CAP_CUT.format(total=len(text), kept=len(kept), left=len(text) - len(kept))
        return split(kept, opts, (*notes, cut))
    result = split_text(text, opts)
    return {"reply": "\n\n".join([render.split_reply(result), *notes]), "envelope": result.envelope()}


def split_request(payload: str, opts: SplitOptions) -> dict:
    return split(payload, opts) if payload else {"reply": replies.NEED_TEXT}


def piece(payload: str, opts: SplitOptions, last: bool) -> dict:
    """One piece of a text sent over several messages: split as it arrives (nothing is held)."""
    if not payload:
        return {"reply": replies.NOTHING_HELD if last else replies.NEED_TEXT_WITH_PART}
    return split(payload, opts, (replies.PIECE_NOTE,))


def merge_values(pasted: Pasted, base: tuple = ()) -> dict:
    result = merge([*base, *pasted.values], pasted.problems)
    if result.status == "need_more":
        return {"reply": "\n\n".join([*result.notes, replies.NEED_ENVELOPES])}
    if result.status == "different":
        return {"reply": "\n\n".join([replies.DIFFERENT_AGENTS, *result.notes])}
    return {"reply": render.merge_reply(result), "envelope": result.envelope}
