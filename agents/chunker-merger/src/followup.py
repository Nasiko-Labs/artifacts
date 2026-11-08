"""Pure: follow-ups on the stored split or merge, with zero calls. A split is never stored as prompts:
it is recomputed from the stored source and options (deterministic), which keeps stored values small."""

from __future__ import annotations

import accumulate
import actions
import render
import replies
from classify import Request
from envelope import extract_values, fenced
from merge import MergeResult, merge
from split import SplitOptions, SplitResult, split_text


def handle(request: Request, state: dict) -> dict:
    kind = request.kind
    if kind in ("merge_more", "merge_notes") or (kind == "as_json" and state.get("last") == "merge"):
        return _merge_followup(request, state)
    last = last_split(state)
    if last is None:
        return {"reply": replies.NO_SPLIT_YET}
    if kind == "show_part":
        if not 1 <= request.number <= len(last.parts):
            return {"reply": replies.NO_SUCH_PART.format(n=request.number, total=len(last.parts))}
        return {"reply": last.parts[request.number - 1]["prompt"]}
    if kind == "list_parts":
        return {"reply": render.part_list(last)}
    if kind == "as_json":
        return {"reply": fenced(last.envelope()), "envelope": last.envelope()}
    opts = SplitOptions.from_dict(state.get("split_opts")).overlay(request.opts)
    return actions.split(accumulate.joined(state.get("source")), opts)


def last_split(state: dict) -> SplitResult | None:
    source = accumulate.joined(state.get("source"))
    if not source:
        return None
    return split_text(source, SplitOptions.from_dict(state.get("split_opts")))


def last_merge(state: dict) -> MergeResult | None:
    stored = state.get("merge_set") or []
    if len(stored) < 2:
        return None
    result = merge(list(stored))
    return result if result.status == "ok" else None


def _merge_followup(request: Request, state: dict) -> dict:
    if request.kind == "merge_more":
        pasted = extract_values(request.payload)
        if not pasted.values:
            return {"reply": replies.NEED_ENVELOPES}
        return actions.merge_values(pasted, base=tuple(state.get("merge_set") or ()))
    result = last_merge(state)
    if result is None:
        return {"reply": replies.NO_MERGE_YET}
    if request.kind == "merge_notes":
        return {"reply": render.merge_notes(result)}
    return {"reply": fenced(result.envelope), "envelope": result.envelope}
