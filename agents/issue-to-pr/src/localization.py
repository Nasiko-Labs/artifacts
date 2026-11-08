"""Pure: the localization record (copied method: Agentless via the issue-localizer) built from the checkpointed
state: suspect files in score order, edit locations with line ranges computed in code, and a confidence. Also
the record built from an issue-localizer envelope (paths kept only when the listing has them)."""

from __future__ import annotations

from collections.abc import Sequence

import checks
import scoring
from candidates import Candidate

MAX_SUSPECTS = 6


def build(state: dict, searched: bool, repaired: bool, issue_words: int) -> dict:
    picks = state.get("picks") or []
    ranks = {p: i + 1 for i, (p, _) in enumerate(picks)}
    suspects = scoring.suspects([Candidate.from_dict(c) for c in state.get("candidates") or []], ranks)
    order = {s.path: i for i, s in enumerate(suspects)}
    locations = sorted(state.get("locations") or [], key=lambda x: order.get(x["path"], 99))
    top = suspects[0] if suspects else None
    top_loc = next((x for x in locations if top and x["path"] == top.path), None)
    pasted = len(state.get("listing") or []) if state.get("pasted_only") else None
    conf = checks.confidence(top.bucket if top else None, top_loc["precision"] if top_loc else None, searched,
                             repaired, issue_words, pasted)
    return {"confidence": conf,
            "suspect_files": [s.path for s in suspects[:MAX_SUSPECTS]],
            "scores": {s.path: s.score for s in suspects[:MAX_SUSPECTS]},
            "edit_locations": [{"path": x["path"], "symbol": x["symbol"], "start_line": x["start_line"],
                                "end_line": x["end_line"]} for x in locations]}


def from_envelope(suspects: Sequence[str], locations: Sequence[dict], listing: Sequence[str]) -> dict | None:
    known = set(listing)
    kept = [p for p in dict.fromkeys(suspects) if p in known][:MAX_SUSPECTS]
    if not kept:
        return None
    locs = [{"path": x.get("path"), "symbol": x.get("symbol"), "start_line": x.get("start_line"),
             "end_line": x.get("end_line")} for x in locations if x.get("path") in kept]
    return {"confidence": "medium", "suspect_files": kept, "scores": {}, "edit_locations": locs}


def forced(record: dict, path: str) -> dict:
    """The user picked or typed this file at Q3: it goes first."""
    rest = [p for p in record.get("suspect_files") or [] if p != path]
    return {**record, "confidence": "medium", "suspect_files": [path, *rest][:MAX_SUSPECTS], "user_picked": path}


def hints(record: dict) -> list[str]:
    """Locations as engine hints ("verify them")."""
    out = []
    for loc in record.get("edit_locations") or []:
        where = f"{loc['path']}"
        if loc.get("symbol"):
            where += f" :: {loc['symbol']}"
        if isinstance(loc.get("start_line"), int) and isinstance(loc.get("end_line"), int):
            where += f" (around lines {loc['start_line']}-{loc['end_line']})"
        out.append(where)
    return out
