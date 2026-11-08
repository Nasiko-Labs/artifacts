"""Matching one request against the index (shortlist in code → judge → reply), the scan pre-filter, and
the envelope. `confidence` and `needs_human` are computed here from checks, never taken from the model."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, replace

import bm25
import judge as judge_
import reply as reply_
from deps import TurnDeps
from index import tokens
from models import Confidence, Envelope, Index, IndexInfo, Intent, Item, Request, Row

MAX_PER_TURN = 5
MIN_RELIABLE_ROWS = 5
_LEVELS: tuple[Confidence, ...] = ("low", "medium", "high")
_BY_VERDICT: dict[str, Confidence] = {"exact": "high", "partial": "medium", "none": "low"}
_CUES = re.compile(
    r"\b(?:can|could|would)\s+(?:you|someone|somebody|anyone|i)\s+(?:please\s+)?(?:pull|get|give|send|share|grab|find|"
    r"run|show)\b|\bneed\s+(?:the\s+)?(?:numbers|data|figures|stats|a\s+report)\b|\bhow\s+many\b|\bwhat(?:'s|\s+is|\s+are|"
    r"\s+was|\s+were)\s+(?:the|our)\b|\breport\s+on\b|\bis\s+there\s+a\s+(?:dashboard|report|chart)\b|"
    r"\bdo\s+we\s+have\s+(?:a\s+)?(?:dashboard|report|numbers|data)\b|\bwhere\s+can\s+i\s+(?:find|see)\b|"
    r"\banyone\s+have\s+(?:the\s+)?(?:numbers|data|a\s+dashboard)\b", re.IGNORECASE)


def looks_like_request(text: str) -> bool:
    """Code pre-filter for a scan: a request cue and a question mark or an ask verb."""
    return bool(_CUES.search(text or "")) and ("?" in text or bool(re.search(r"\b(?:pull|need|send|share)\b", text)))


def lower(level: Confidence) -> Confidence:
    return _LEVELS[max(0, _LEVELS.index(level) - 1)]


def item_confidence(verdict: str, index: Index, dropped: int) -> Confidence:
    level = _BY_VERDICT[verdict]
    if index.source == "paste" and len(index.rows) < MIN_RELIABLE_ROWS:
        level = "low"
    return lower(level) if dropped else level


async def match_request(deps: TurnDeps, request: Request, intent: Intent, index: Index, *, can_post: bool,
                        dropped: int = 0) -> Item:
    short = bm25.shortlist(index.rows, intent.terms)
    judged = await judge_.judge(deps, request.text, intent, short)
    text = await reply_.draft(deps, request.text, request.requester, judged.match, judged.row)
    return Item(request_text=request.text, request_link=request.link or None, requester=request.requester or None,
                match=judged.match, reply=text, state="draft" if can_post and request.thread_ts else "copyable",
                confidence=item_confidence(judged.match.confidence, index, dropped + judged.dropped),
                channel_id=request.channel_id, thread_ts=request.thread_ts)


def overall(items: Sequence[Item]) -> Confidence:
    if not items:
        return "low"
    return min((i.confidence for i in items), key=_LEVELS.index)


def summary(items: Sequence[Item]) -> str:
    if not items:
        return "No data requests matched."
    counts = {v: sum(1 for i in items if i.match.confidence == v) for v in ("exact", "partial", "none")}
    shown = ", ".join(f"{n} {v}" for v, n in counts.items() if n)
    return f"{len(items)} request{'s' if len(items) != 1 else ''} checked against the dashboard index: {shown}."


def envelope(items: Sequence[Item], index: Index | None, coverage: Sequence[str]) -> dict:
    first = items[0] if items else None
    env = Envelope(summary=summary(items), items=list(items), confidence=overall(items),
                   needs_human=not items or any(i.match.confidence != "exact" for i in items),
                   match=first.match if first else None, reply=first.reply if first else None,
                   requests=list(items), index=IndexInfo(title=index.title, rows=len(index.rows)) if index else None,
                   coverage=list(coverage))
    return env.model_dump()


def index_to_state(index: Index) -> dict:
    rows = [{k: v for k, v in asdict(r).items() if k != "tokens"} for r in index.rows]
    return {"title": index.title, "source": index.source, "dropped": index.dropped, "rows": rows}


def index_from_state(data: dict | None) -> Index | None:
    if not data or not data.get("rows"):
        return None
    rows = tuple(Row(**r) for r in data["rows"])
    rows = tuple(replace(r, tokens=frozenset(tokens(r.text))) for r in rows)
    return Index(str(data.get("title") or "index"), rows, data.get("source") or "sheet", int(data.get("dropped") or 0))
