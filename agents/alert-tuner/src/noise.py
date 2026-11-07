"""Pure: which alerts are noisy, from incidents and their logs. Every count, rate, duration and
percentage the reply shows is computed here; the model never computes one.

Zero-action incident (all must hold): resolved; no acknowledgement; no note; no escalation or
reassignment; not resolved by a human. A missing log is `unknown`, never zero-action. An incident the
pager itself marks acknowledged or human-resolved is actioned without its log.

Noisy alert: fired >= N, floor(100 * zero / assessed) >= 80, unknown <= 20% of the incidents sampled.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from alertkey import group_key
from records import Incident, LogEntry, parse_time

NOISY_PCT = 80
MAX_UNKNOWN_PCT = 20
PER_ALERT_LOG_CAP = 25
TOTAL_LOG_CAP = 60
MAX_EVIDENCE = 3

ZERO, ACTIONED, UNKNOWN, UNCHECKED = "zero", "actioned", "unknown", "unchecked"


@dataclass(frozen=True)
class LogFacts:
    """What the logs said about one incident; `read` False when no log came back."""

    read: bool = False
    entries: tuple[LogEntry, ...] = ()
    notes: int = 0


@dataclass(frozen=True)
class Params:
    window_days: int = 30
    min_firings: int = 5
    service: str = ""
    include_ack_only: bool = False


@dataclass(frozen=True)
class Verdict:
    kind: str           # zero | actioned | unknown | unchecked
    acked: bool = False


@dataclass(frozen=True)
class AlertGroup:
    name: str
    service: str
    incidents: tuple[Incident, ...]


@dataclass(frozen=True)
class AlertStats:
    alert: str
    service: str
    fired: int
    zero_action: int
    zero_action_pct: int
    unknown: int
    assessed: int
    ack_count: int
    median_duration_min: float | None
    p90_duration_min: float | None
    fired_per_week: float
    last_fired: str
    evidence: tuple[dict, ...]
    zero_durations: tuple[float, ...] = field(default=(), repr=False)
    verdicts: Mapping[str, str] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {"alert": self.alert, "service": self.service, "fired": self.fired, "zero_action": self.zero_action,
                "zero_action_pct": self.zero_action_pct, "unknown": self.unknown, "assessed": self.assessed,
                "ack_count": self.ack_count, "median_duration_min": self.median_duration_min,
                "p90_duration_min": self.p90_duration_min, "fired_per_week": self.fired_per_week,
                "last_fired": self.last_fired, "evidence": [dict(e) for e in self.evidence]}


@dataclass(frozen=True)
class NoiseResult:
    noisy: tuple[AlertStats, ...]
    chatty: tuple[AlertStats, ...]
    too_unknown: tuple[AlertStats, ...]
    total_alerts: int


def group(incidents: Iterable[Incident]) -> list[AlertGroup]:
    """Incidents by (alert key, service), oldest first inside a group; groups by size, largest first."""
    by_key: dict[tuple[str, str], list[Incident]] = {}
    for incident in sorted(incidents, key=lambda i: (parse_time(i.created) is None, i.created)):
        by_key.setdefault(group_key(incident.title, incident.service), []).append(incident)
    groups = [AlertGroup(items[0].title, items[0].service, tuple(items)) for items in by_key.values()]
    return sorted(groups, key=lambda g: (-len(g.incidents), g.name))


def log_candidates(groups: Sequence[AlertGroup], min_firings: int) -> list[Incident]:
    """Resolved incidents of alerts that fired >= N, newest first, capped per alert and overall."""
    picked: list[Incident] = []
    for g in groups:
        if len(g.incidents) < min_firings:
            continue
        resolved = [i for i in reversed(g.incidents) if i.is_resolved and not i.acked and i.resolved_by != "human"]
        picked.extend(resolved[:PER_ALERT_LOG_CAP])
    return picked[:TOTAL_LOG_CAP]


def classify(incident: Incident, facts: LogFacts | None, include_ack_only: bool = False) -> Verdict:
    if not incident.is_resolved or incident.resolved_by == "human":
        return Verdict(ACTIONED, incident.acked)
    if incident.acked and not include_ack_only:
        return Verdict(ACTIONED, True)
    if facts is None:
        return Verdict(UNCHECKED, incident.acked)
    if not facts.read:
        return Verdict(UNKNOWN, incident.acked)
    kinds = {e.kind for e in facts.entries}
    acked = incident.acked or "acknowledge" in kinds
    human_resolve = any(e.kind == "resolve" and e.actor == "human" for e in facts.entries)
    if facts.notes or "annotate" in kinds or kinds & {"escalate", "reassign"} or human_resolve:
        return Verdict(ACTIONED, acked)
    if acked and not include_ack_only:
        return Verdict(ACTIONED, acked)
    return Verdict(ZERO, acked)


def duration_min(incident: Incident) -> float | None:
    start, end = parse_time(incident.created), parse_time(incident.resolved)
    if start is None or end is None or end < start:
        return None
    return round((end - start).total_seconds() / 60, 1)


def nearest_rank(values: Sequence[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(pct * len(ordered) / 100)) - 1]


def stats(g: AlertGroup, facts: Mapping[str, LogFacts], params: Params) -> AlertStats:
    verdicts = {i.id: classify(i, facts.get(i.id), params.include_ack_only) for i in g.incidents}
    zero = [i for i in g.incidents if verdicts[i.id].kind == ZERO]
    assessed = sum(1 for v in verdicts.values() if v.kind in (ZERO, ACTIONED))
    unknown = sum(1 for v in verdicts.values() if v.kind == UNKNOWN)
    durations = tuple(d for d in (duration_min(i) for i in zero) if d is not None)
    evidence = tuple({"number": i.label, "url": i.url} for i in list(reversed(zero))[:MAX_EVIDENCE])
    return AlertStats(
        alert=g.name, service=g.service, fired=len(g.incidents), zero_action=len(zero),
        zero_action_pct=math.floor(100 * len(zero) / assessed) if assessed else 0, unknown=unknown, assessed=assessed,
        ack_count=sum(1 for v in verdicts.values() if v.acked),
        median_duration_min=round(statistics.median(durations), 1) if durations else None,
        p90_duration_min=nearest_rank(durations, 90),
        fired_per_week=round(len(g.incidents) * 7 / params.window_days, 1),
        last_fired=g.incidents[-1].created, evidence=evidence, zero_durations=durations,
        verdicts={k: v.kind for k, v in verdicts.items()})


def is_noisy(s: AlertStats, min_firings: int) -> bool:
    return s.fired >= min_firings and s.zero_action_pct >= NOISY_PCT and not too_unknown(s)


def too_unknown(s: AlertStats) -> bool:
    sampled = s.assessed + s.unknown
    return sampled == 0 or s.unknown * 100 > MAX_UNKNOWN_PCT * sampled


def analyse(groups: Sequence[AlertGroup], facts: Mapping[str, LogFacts], params: Params) -> NoiseResult:
    noisy, chatty, unknown = [], [], []
    for g in groups:
        if len(g.incidents) < params.min_firings:
            continue
        s = stats(g, facts, params)
        if too_unknown(s):
            unknown.append(s)
        elif s.zero_action_pct >= NOISY_PCT:
            noisy.append(s)
        else:
            chatty.append(s)
    noisy.sort(key=lambda s: (-s.zero_action, -s.fired, s.alert))
    return NoiseResult(tuple(noisy), tuple(chatty), tuple(unknown), len(groups))
