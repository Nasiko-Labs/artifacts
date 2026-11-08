"""Pure: the structured envelope (the DataPart, and the text in JSON mode). `confidence` and
`needs_human` are computed here from coverage, caps and empty packets, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence

import coverage as coverage_
from records import PersonPacket
from window import Window


def confidence(covered: frozenset[str], limited: bool) -> str:
    if not covered:
        return "low"
    return "high" if covered == frozenset(coverage_.SOURCES) and not limited else "medium"


def build(window: Window, packets: Sequence[PersonPacket], covered: coverage_.Coverage | None,
          limited: bool, notes: Sequence[str]) -> dict:
    sources = covered.covered if covered else frozenset()
    level = confidence(sources, limited)
    per_person = [_person(p) for p in packets]
    notable = sum(len(p.notable) for p in packets)
    praise = sum(len(p.praise) for p in packets)
    summary = (f"Kudos evidence for {len(packets)} {'person' if len(packets) == 1 else 'people'}, "
               f"{window.start.isoformat()} to {window.last_day.isoformat()}: {notable} notable pull requests, "
               f"{praise} praise messages. Evidence only, not a rating.")
    return {
        "summary": summary,
        "items": per_person,
        "confidence": level,
        "needs_human": level != "high" or any(p.empty for p in packets),
        "per_person": per_person,
        "window": window.to_dict(),
        "coverage": [{"source": s.source, "state": s.state, "reason": s.reason}
                     for s in (covered.statuses if covered else ())],
        "notes": list(notes),
    }


def _person(p: PersonPacket) -> dict:
    return {
        "person": p.person.label,
        "handles": [v for k, v in p.person.known if k == "handle"],
        "notable_prs": [{"number": n.pr.number, "url": n.pr.url, "title": n.pr.title,
                         "merged_at": n.pr.merged_at.isoformat() if n.pr.merged_at else None,
                         "why": list(n.why), "pasted": n.pr.pasted} for n in p.notable],
        "shipped": [{"number": s.pr.number, "url": s.pr.url, "title": s.pr.title, "release": s.release or None}
                    for s in p.shipped],
        "praise": [{"quote": q.quote, "from": q.author, "channel": q.channel, "permalink": q.permalink or None,
                    "ts": q.ts.isoformat() if q.ts else None, "pasted": q.pasted} for q in p.praise],
        "counts": {"prs_merged": p.prs_merged, "notable": len(p.notable), "praise_messages": len(p.praise),
                   "distinct_praisers": p.distinct_praisers},
        "gaps": list(p.gaps),
    }
