"""Pure: follow-ups on the last split or merge, with zero calls. Nothing is stored: `state` is rebuilt from
the platform's history slice (recall.py) and a split is recomputed from its source and options
(deterministic), so the parts are identical to the ones shown before."""

from __future__ import annotations

import accumulate
import actions
import recall
import render
import replies
from classify import Request
from envelope import extract_values, fenced
from merge import MergeResult, merge
from split import SplitOptions, SplitResult, split_text


def handle(request: Request, found: recall.Recalled, has_history: bool) -> dict:
    state = found.state()
    kind = request.kind
    if kind in ("merge_more", "merge_notes") or (kind == "as_json" and state.get("last") == "merge"):
        return _merge_followup(request, state, found.merge_lost or has_history)
    last = last_split(state)
    if last is None:
        return {"reply": replies.SPLIT_NOT_IN_HISTORY if found.split_lost or has_history else replies.NO_SPLIT_YET}
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


def _merge_followup(request: Request, state: dict, lost: bool) -> dict:
    base = tuple(state.get("merge_set") or ())
    if request.kind == "merge_more":
        pasted = extract_values(request.payload)
        if not pasted.values:
            return {"reply": replies.NEED_ENVELOPES}
        if not base and len(pasted.values) < 2 and lost:
            return {"reply": replies.MERGE_NOT_IN_HISTORY}
        return actions.merge_values(pasted, base=base)
    result = last_merge(state)
    if result is None:
        return {"reply": replies.MERGE_NOT_IN_HISTORY if lost else replies.NO_MERGE_YET}
    if request.kind == "merge_notes":
        return {"reply": render.merge_notes(result)}
    return {"reply": fenced(result.envelope), "envelope": result.envelope}
