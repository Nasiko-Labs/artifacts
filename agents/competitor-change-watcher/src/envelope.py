"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `changes`, `sources`,
`no_baseline`, `roadmap`, `caveats`, `checks`). `confidence` and `needs_human` are computed here from the
run's checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import replies
from diff import BlockDiff
from records import Change
from steps_roadmap import Roadmap
from words import first_sentence

LOW_DROP_SHARE = 0.2
MAX_NO_BASELINE = 10


def confidence(c: Mapping[str, Any]) -> str:
    if not c["blocks"] or c["salvage"] or c["dropped"] / max(c["classified"], 1) > LOW_DROP_SHARE:
        return "low"
    if not c["roadmap_checked"] or c["capped"] or c["dropped"] or c["unclassified"] or c["paste_cut"] \
            or c["unbaselined"] or c["unnamed"]:
        return "medium"
    return "high"


def _source(d: BlockDiff) -> dict:
    return {"competitor": d.block.competitor, "source": d.block.source, "baseline": d.baseline, "read": d.total,
            "new": len(d.new) or len(d.price_facts), "older_not_listed": d.older, "no_baseline": len(d.unbaselined),
            "named": d.block.named, "pricing_failed": d.pricing_failed}


def caveats(c: Mapping[str, Any], diffs: Sequence[BlockDiff], since: str) -> list[str]:
    out = []
    if c["salvage"] or c["unclassified"]:
        out.append(replies.STEP_LIMIT_NOTE)
    if c["capped"]:
        out.append(f"Only the newest {c['new'] - c['capped']} of {c['new']} new entries were classified.")
    if c["paste_cut"]:
        out.append("The message was longer than 100,000 characters; only the first 100,000 were read.")
    if c["unnamed"]:
        out.append("A block had no `Competitor:` line; it is listed as \"Unnamed competitor\".")
    out.extend(f"{d.block.competitor} pricing: no plan with a price could be read; check the paste."
               for d in diffs if d.pricing_failed)
    out.extend(f"{d.block.competitor} pricing: {d.total} plans read, no earlier version to compare with."
               for d in diffs if d.block.is_pricing and d.baseline == "none" and d.total)
    older = sum(d.older for d in diffs)
    if older:
        out.append(f"{older} {'entry' if older == 1 else 'entries'} dated before {since} "
                   f"{'was' if older == 1 else 'were'} not listed (no earlier paste to compare with).")
    if any(d.baseline == "none" for d in diffs):
        out.append(replies.SNAPSHOT_NOTE)
    return out


def summary(changes: Sequence[Change], c: Mapping[str, Any], competitors: Sequence[str]) -> str:
    hits = sum(1 for ch in changes if ch.affects is not None)
    head = f"{len(competitors)} competitors, {c['entries']} changelog entries read, {len(changes)} changes found"
    return head + (f"; {hits} affect roadmap items." if c["roadmap_checked"] else "; roadmap not checked.")


def build(changes: Sequence[Change], diffs: Sequence[BlockDiff], road: Roadmap, checks: Mapping[str, Any],
          today: date, days: int) -> dict:
    level = confidence(checks)
    since = (today - timedelta(days=days)).isoformat()
    competitors = list(dict.fromkeys(d.block.competitor for d in diffs))
    items = [c.to_dict() for c in changes]
    unbased = [{"competitor": e.competitor, "source": e.source, "what": first_sentence(e.text)}
               for d in diffs for e in d.unbaselined][:MAX_NO_BASELINE]
    return {"summary": summary(changes, checks, competitors), "items": items, "confidence": level,
            "needs_human": level == "low" or checks["unnamed"] or checks["pricing_failed"], "changes": items,
            "competitors": competitors, "sources": [_source(d) for d in diffs], "no_baseline": unbased,
            "roadmap": {"checked": road.checked, "line": road.line, "issues_found": len(road.issues)},
            "window_since": since, "caveats": caveats(checks, diffs, since), "checks": dict(checks)}


def looks_like(data: Any) -> bool:
    """An envelope from an earlier run of this agent (accepted as input for follow-ups)."""
    required = ("summary", "confidence", "needs_human", "sources", "roadmap", "caveats")
    return isinstance(data, Mapping) and isinstance(data.get("items", data.get("changes")), list) \
        and all(k in data for k in required)
