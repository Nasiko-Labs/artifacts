"""Pure: from a commitment, its tracker facts and its chat signals to one audited row: the reported
status, the evidence status, whether they disagree, a confidence level and the specific risks.

Every rule and threshold is here, in code; the model never decides a status (it only maps a reported
phrase the table below cannot read, in classify.py)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date

from commitments import Commitment
from evidence import Signal, TrackerFacts
from quarter import Quarter, elapsed_share
from verbatim import parse_day

STALE_DAYS = 14
AT_RISK_GAP = 0.20
OFF_TRACK_GAP = 0.40
BLOCKER_WINDOW_DAYS = 14
HIGH_CONFIDENCE = 4
MEDIUM_CONFIDENCE = 2
MAX_RISKS = 2
_QUOTE_CHARS = 90

RANK = {"done": 0, "on_track": 1, "at_risk": 2, "off_track": 3}
LABELS = {"done": "done", "on_track": "on track", "at_risk": "at risk", "off_track": "off track",
          "unknown": "unknown", "not_reported": "not reported"}
NEGATIVE_KINDS = frozenset({"scope_cut", "delay", "blocked"})

# Reported phrases, first match wins (case-insensitive, whole words). A negation sends the text to the
# model instead ("not on track" must not read as on track).
_REPORTED_TABLE: tuple[tuple[str, re.Pattern], ...] = tuple(
    (status, re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)) for status, pattern in (
        ("done", r"done|completed?|shipped|launched"),
        ("off_track", r"off[ -]track|red|blocked|behind|slipped|delayed"),
        ("at_risk", r"at[ -]risk|amber|yellow|risk"),
        ("on_track", r"on[ -]track|green|going well|in progress"),
    ))
_NEGATION = re.compile(r"\b(?:not|no|never)\b|n't\b", re.IGNORECASE)


@dataclass(frozen=True)
class Row:
    """One audited commitment, as shown and as stored for follow-ups."""

    number: int
    cid: str
    name: str
    owner: str | None
    reported: str              # done / on_track / at_risk / off_track / unknown / not_reported
    reported_text: str | None
    evidence: str              # done / on_track / at_risk / off_track / unknown
    confidence: str            # High / Medium / Low
    points: int
    disagreement: str | None   # reported_better / reported_worse / unreported_risk
    risks: tuple[str, ...]
    links: tuple[str, ...]
    stale_days: int | None

    def to_dict(self) -> dict:
        return {**asdict(self), "risks": list(self.risks), "links": list(self.links)}

    @staticmethod
    def from_dict(data: Mapping) -> Row:
        return Row(**{**dict(data), "risks": tuple(data.get("risks") or ()), "links": tuple(data.get("links") or ())})


@dataclass(frozen=True)
class Signals:
    stale_days: int | None
    progress: float | None
    deadline: date
    elapsed: float
    gap: float | None
    open_blockers: tuple
    negative_chat: tuple[Signal, ...]


def map_reported(text: str | None) -> str | None:
    """The status a reported phrase states, or None when the table cannot tell (negated or unlisted)."""
    if not text or _NEGATION.search(text):
        return None
    return next((status for status, pattern in _REPORTED_TABLE if pattern.search(text)), None)


def signals(facts: TrackerFacts | None, chat: Sequence[Signal], quarter: Quarter, today: date) -> Signals:
    due = parse_day(facts.due_date) if facts else None
    deadline = due or quarter.end
    elapsed = elapsed_share(quarter.start, deadline, today)
    progress = _progress(facts)
    gap = None if progress is None else round(elapsed - progress, 4)
    blockers = tuple(b for b in (facts.blockers if facts else ()) if b.status_category != "done")
    return Signals(stale_days=_stale_days(facts, chat, today), progress=progress, deadline=deadline,
                   elapsed=elapsed, gap=gap, open_blockers=blockers,
                   negative_chat=tuple(s for s in chat if s.kind in NEGATIVE_KINDS))


def _progress(facts: TrackerFacts | None) -> float | None:
    if facts is None:
        return None
    if facts.children_total:
        return facts.children_done / facts.children_total
    return {"done": 1.0, "todo": 0.0}.get(facts.status_category)


def _stale_days(facts: TrackerFacts | None, chat: Sequence[Signal], today: date) -> int | None:
    days = [parse_day(v) for v in (facts.updated_at, facts.children_updated_max) if v] if facts else []
    days = [*days, *(s.day for s in chat)]
    known = [d for d in days if d is not None]
    return (today - max(known)).days if known else None


def _children_open(facts: TrackerFacts) -> bool:
    return bool(facts.children_total) and facts.children_done != facts.children_total


def evidence_status(facts: TrackerFacts | None, s: Signals, today: date) -> str:
    """The first rule that fires. Chat alone never sets a status: no tracker item is `unknown`."""
    if facts is None:
        return "unknown"
    if facts.status_category == "done" and not _children_open(facts):
        return "done"
    to_deadline = (s.deadline - today).days
    if to_deadline < 0 or (s.gap is not None and s.gap >= OFF_TRACK_GAP) or (
            s.open_blockers and to_deadline <= BLOCKER_WINDOW_DAYS):
        return "off_track"
    if ((s.stale_days is not None and s.stale_days >= STALE_DAYS) or (s.gap is not None and s.gap >= AT_RISK_GAP)
            or s.open_blockers or s.negative_chat):
        return "at_risk"
    return "on_track"


def disagreement(reported: str, evidence: str) -> str | None:
    if evidence == "unknown" or reported == "unknown":
        return None
    if reported == "not_reported":
        return "unreported_risk" if RANK[evidence] >= RANK["at_risk"] else None
    diff = RANK[reported] - RANK[evidence]
    if diff < 0:
        return "reported_better"
    return "reported_worse" if diff > 0 else None


def confidence_points(facts: TrackerFacts | None, chat: Sequence[Signal], chat_searched: bool) -> int:
    if facts is None:
        return int(chat_searched) + int(bool(chat))
    points = 1 + int(facts.children_total is not None) + int(facts.updated_at is not None) + int(chat_searched)
    points += int(bool(chat) or facts.last_update_comment is not None)
    return points - int(facts.dropped > 0)


def confidence_level(points: int) -> str:
    if points >= HIGH_CONFIDENCE:
        return "High"
    return "Medium" if points >= MEDIUM_CONFIDENCE else "Low"


def risks(commitment: Commitment, facts: TrackerFacts | None, s: Signals, reported: str,
          today: date) -> tuple[str, ...]:
    """The strongest one or two, each carrying its evidence link."""
    link = _tracker_link(commitment, facts)
    found: list[str] = []
    finished = facts is not None and facts.status_category == "done" and not _children_open(facts)
    if facts is not None and not finished and today > s.deadline:
        found.append(_with(f"Past due ({_days(today - s.deadline)})", link))
    if facts is not None and reported == "done" and _children_open(facts):
        still_open = facts.children_total - facts.children_done
        found.append(_with(f"Reported done but {still_open} of {facts.children_total} child issues open", link))
    found.extend(_with(f"Blocked by {b.key} ({LABELS.get(b.status_category, b.status_category)})", link)
                 for b in s.open_blockers)
    if s.gap is not None and s.gap >= AT_RISK_GAP:
        found.append(_with(f"Behind plan: {round(s.progress * 100)}% done at {round(s.elapsed * 100)}% of the time",
                           link))
    found.extend(_chat_risk(signal) for signal in s.negative_chat)
    if s.stale_days is not None and s.stale_days >= STALE_DAYS:
        found.append(_with(f"No update in {s.stale_days} days", link))
    return tuple(dict.fromkeys(found))[:MAX_RISKS]


def _chat_risk(signal: Signal) -> str:
    day = signal.day.isoformat() if signal.day else signal.at
    if signal.kind == "scope_cut":
        quote = signal.quote if len(signal.quote) <= _QUOTE_CHARS else signal.quote[:_QUOTE_CHARS].rstrip() + "..."
        return _with(f'Scope cut discussed in chat ({day}): "{quote}"', signal.link)
    what = "Delay" if signal.kind == "delay" else "Blocker"
    return _with(f"{what} discussed in chat ({day})", signal.link)


def _days(delta) -> str:
    return f"{delta.days} day{'s' if delta.days != 1 else ''}"


def _with(text: str, link: str | None) -> str:
    return f"{text} ({link})" if link else text


def _tracker_link(commitment: Commitment, facts: TrackerFacts | None) -> str | None:
    if facts is not None and facts.link:
        return facts.link
    return commitment.link or (facts.key if facts else None)


def row(commitment: Commitment, facts: TrackerFacts | None, chat: Sequence[Signal], *, reported: str,
        quarter: Quarter, today: date, chat_searched: bool) -> Row:
    """`reported` is already mapped (code table or classify), or `not_reported` / `unknown`."""
    s = signals(facts, chat, quarter, today)
    evidence = evidence_status(facts, s, today)
    points = confidence_points(facts, chat, chat_searched)
    links = tuple(dict.fromkeys(x for x in (_tracker_link(commitment, facts), commitment.reported_link,
                                             *(c.link for c in chat)) if x))
    return Row(number=0, cid=commitment.cid, name=commitment.name, owner=commitment.owner or (
        facts.assignee if facts else None), reported=reported, reported_text=commitment.reported_text,
        evidence=evidence, confidence=confidence_level(points), points=points,
        disagreement=disagreement(reported, evidence), risks=risks(commitment, facts, s, reported, today),
        links=links, stale_days=s.stale_days)


_GROUP = {"reported_better": 0, "unreported_risk": 1, "reported_worse": 2, None: 3}


def ranked(rows: Sequence[Row]) -> tuple[Row, ...]:
    """Reported-better disagreements first, then evidence rank, confidence, staleness; numbered 1..n."""
    def key(r: Row) -> tuple:
        return (_GROUP[r.disagreement], -RANK.get(r.evidence, -1), -r.points, -(r.stale_days or 0))

    ordered = sorted(rows, key=key)
    return tuple(Row(**{**asdict(r), "number": n, "risks": r.risks, "links": r.links})
                 for n, r in enumerate(ordered, start=1))
