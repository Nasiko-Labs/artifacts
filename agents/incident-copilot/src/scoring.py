"""Pure: every number on the card. Metric deviation, log and error spikes, suspect scores and past-incident
similarity are computed here from checked records; the model never ranks or does arithmetic."""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from records import Change, Issue, LogLine, PastIncident, Series
from windows import Windows, minutes_between, parse_time

MAD_K = 3.0
MAD_FLOOR_RATIO = 0.05
MAD_EPSILON = 1e-9
BASE_MINUTES = 45.0
HOT_MINUTES = 30.0
LOG_SPIKE_RATIO = 3.0
LOG_MIN_HOT = 5
TOP_LOG_GROUPS = 5
ERR_SPIKE_RATIO = 3.0
ERR_MIN_HOT = 10
NEW_BEFORE_MIN = 60.0
HALF_LIFE_MIN = 60.0
W_RECENCY, W_SERVICE, W_OVERLAP, W_ONSET = 0.45, 0.25, 0.20, 0.10
KIND_WEIGHT = {"deploy": 1.0, "merged_pr": 0.8}
ONSET_AFTER_MIN = 30.0
MIN_SUSPECT_SCORE = 0.15
TOP_SUSPECTS = 3
SIM_THRESHOLD = 0.5
MIN_SHA_PREFIX = 7
TOP_SIMILAR = 3

_NORM = re.compile(r"[^a-z0-9]")
_WORD = re.compile(r"[a-z][a-z0-9]{2,}")
_DIGITS = re.compile(r"\d")
_HEXISH = re.compile(r"\b[0-9a-f]{8,}\b|\b[0-9a-f-]{36}\b", re.IGNORECASE)
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
STOP_WORDS = frozenset({"the", "and", "for", "with", "from", "into", "are", "was", "has", "have", "not", "high",
                        "on", "of", "in", "at", "to", "is", "service", "alert", "incident", "triggered", "this"})


def norm(text: str) -> str:
    return _NORM.sub("", (text or "").casefold())


def _at(value: str) -> datetime | None:
    return parse_time(value) if value else None


@dataclass(frozen=True)
class MetricSignal:
    label: str
    base: float
    onset: str
    peak_ratio: float | None


def deviation(series: Series, w: Windows) -> MetricSignal | None:
    points = [(_at(t), v) for t, v in series.points]
    base_values = [v for t, v in points if w.base.contains(t)]
    if not base_values:
        return None
    base = statistics.median(base_values)
    mad = statistics.median([abs(v - base) for v in base_values])
    mad = max(mad, MAD_FLOOR_RATIO * abs(base), MAD_EPSILON)
    watch = sorted((t, v) for t, v in points if t is not None and t >= w.hot.start and t <= w.obs.end)
    onset = next((t for t, v in watch if abs(v - base) > MAD_K * mad), None)
    if onset is None:
        return None
    hot = [v for t, v in points if w.hot.contains(t)]
    peak = max(hot) if hot else None
    ratio = None if peak is None or base == 0 else round(peak / base, 2)
    return MetricSignal(series.label, base, onset.strftime("%Y-%m-%dT%H:%M:%SZ"), ratio)


def message_key(message: str) -> str:
    """Lowercased, with quoted strings, UUIDs, hex ids of 8+ and digits replaced by #."""
    text = _QUOTED.sub("#", message.casefold())
    text = _HEXISH.sub("#", text)
    return _DIGITS.sub("#", text).strip()


@dataclass(frozen=True)
class LogGroup:
    sample: str
    hot_count: int
    base_count: int
    r_hot: float
    onset: str


def log_spikes(lines: Sequence[LogLine], w: Windows) -> tuple[LogGroup, ...]:
    groups: dict[str, list[LogLine]] = {}
    for line in lines:
        groups.setdefault(message_key(line.quote), []).append(line)
    spikes = []
    for members in groups.values():
        hot = sorted((m for m in members if w.hot.contains(_at(m.at))), key=lambda m: m.at)
        base = [m for m in members if w.base.contains(_at(m.at))]
        r_hot, r_base = len(hot) / HOT_MINUTES, len(base) / BASE_MINUTES
        if len(hot) >= LOG_MIN_HOT and r_hot >= LOG_SPIKE_RATIO * max(r_base, 1 / BASE_MINUTES):
            spikes.append(LogGroup(hot[0].quote, len(hot), len(base), round(r_hot, 3), hot[0].at))
    return tuple(sorted(spikes, key=lambda g: -g.r_hot)[:TOP_LOG_GROUPS])


@dataclass(frozen=True)
class ErrorSignal:
    issue: Issue
    kind: str          # "new" | "rate" | "active" (no rate data: listed, never used for overlap)
    onset: str

    @property
    def spiking(self) -> bool:
        return self.kind in ("new", "rate")


def error_signal(issue: Issue, w: Windows) -> ErrorSignal | None:
    first = _at(issue.first_seen)
    if first is not None and w.t0.timestamp() - NEW_BEFORE_MIN * 60 <= first.timestamp() <= w.hot.end.timestamp():
        return ErrorSignal(issue, "new", issue.first_seen)
    if issue.buckets:
        hot = [(t, n) for t, n in issue.buckets if w.hot.contains(_at(t))]
        hot_events = sum(n for _, n in hot)
        base_events = sum(n for t, n in issue.buckets if w.err24.contains(_at(t)))
        rate_hot, rate_base = hot_events / HOT_MINUTES, base_events / w.err24.minutes
        if hot_events >= ERR_MIN_HOT and rate_hot >= ERR_SPIKE_RATIO * rate_base:
            onset = next((t for t, n in sorted(hot) if n > 0), w.hot.start.strftime("%Y-%m-%dT%H:%M:%SZ"))
            return ErrorSignal(issue, "rate", onset)
        return None
    if w.hot.contains(_at(issue.last_seen)) and (issue.count or 0) >= ERR_MIN_HOT:
        return ErrorSignal(issue, "active", "")
    return None


def error_signals(issues: Sequence[Issue], w: Windows) -> tuple[ErrorSignal, ...]:
    return tuple(s for s in (error_signal(i, w) for i in issues) if s is not None)


@dataclass(frozen=True)
class Suspect:
    change: Change
    score: float
    delta_min: float
    parts: dict
    evidence: tuple[str, ...]


def merge_prs(changes: Sequence[Change]) -> tuple[Change, ...]:
    """A merged PR whose SHA a deploy shipped is folded into that deploy."""
    deploys = [c for c in changes if c.kind == "deploy"]
    out = list(deploys)
    for change in changes:
        if change.kind != "merged_pr":
            continue
        index = next((i for i, d in enumerate(out) if same_sha(d.sha, change.sha)), None)
        if index is None:
            out.append(change)
            continue
        target = out[index]
        out[index] = replace(target, prs=(*target.prs, change.ref),
                             files=tuple(dict.fromkeys((*target.files, *change.files))))
    return tuple(out)


def same_sha(a: str, b: str) -> bool:
    a, b = (a or "").casefold(), (b or "").casefold()
    return min(len(a), len(b)) >= MIN_SHA_PREFIX and (a.startswith(b) or b.startswith(a))


def _path_key(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").casefold().split("/") if p and p != "."]
    return "/".join(parts[-2:])


def service_match(change: Change, service: str, repo: str) -> float:
    wanted = norm(service)
    if not wanted:
        return 0.5
    names = [norm(repo.split("/")[-1]) if repo else "", norm(change.environment)]
    if any(name and (wanted in name or name in wanted) for name in names):
        return 1.0
    return 0.0 if change.environment else 0.5


def score_change(change: Change, w: Windows, service: str, repo: str, errors: Sequence[ErrorSignal],
                 logs: Sequence[LogGroup]) -> Suspect | None:
    moment = _at(change.time)
    if moment is None or not (w.change.start <= moment <= w.t0):
        return None
    delta = minutes_between(moment, w.t0)
    recency = 0.5 ** (delta / HALF_LIFE_MIN)
    matched = service_match(change, service, repo)
    changed = {_path_key(f): f for f in change.files}
    overlap = next(((f, s.issue) for s in errors if s.spiking for f in s.issue.files if _path_key(f) in changed),
                   None)
    onsets = [(_at(s.onset), "errors began") for s in errors if s.spiking and s.onset] + \
             [(_at(g.onset), "error logs spiked") for g in logs]
    after = [(minutes_between(moment, t), what) for t, what in onsets
             if t is not None and 0 <= minutes_between(moment, t) <= ONSET_AFTER_MIN]
    weight = KIND_WEIGHT[change.kind]
    score = weight * (W_RECENCY * recency + W_SERVICE * matched + W_OVERLAP * (overlap is not None)
                      + W_ONSET * bool(after))
    parts = {"kind_weight": weight, "recency": round(recency, 3), "service_match": matched,
             "error_overlap": int(overlap is not None), "error_onset": int(bool(after))}
    return Suspect(change, round(score, 2), round(delta, 1), parts, _evidence(change, delta, matched, overlap, after))


def _evidence(change: Change, delta: float, matched: float, overlap, after) -> tuple[str, ...]:
    verb = "deployed" if change.kind == "deploy" else "merged (no deploy record)"
    lines = [f"{verb} {round(delta)} min before the start"]
    if matched == 1.0:
        lines.append("its repository or environment matches the service")
    if overlap is not None:
        path, issue = overlap
        lines.append(f"touches `{path}`, which is in the stack trace of error {issue.id}")
    if after:
        gap, what = min(after)
        lines.append(f"{what} {round(gap)} min after it")
    if change.prs:
        lines.append("ships " + ", ".join(f"PR {p}" for p in change.prs))
    return tuple(lines)


def rank_suspects(changes: Sequence[Change], w: Windows, service: str, repo: str,
                  errors: Sequence[ErrorSignal], logs: Sequence[LogGroup]) -> tuple[Suspect, ...]:
    scored = [s for s in (score_change(c, w, service, repo, errors, logs) for c in merge_prs(changes)) if s]
    ranked = sorted((s for s in scored if s.score >= MIN_SUSPECT_SCORE), key=lambda s: (-s.score, s.delta_min))
    return tuple(ranked[:TOP_SUSPECTS])


def tokens(title: str) -> frozenset[str]:
    return frozenset(t for t in _WORD.findall((title or "").casefold()) if t not in STOP_WORDS)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


@dataclass(frozen=True)
class Similar:
    past: PastIncident
    sim: float
    same_service: bool


def similar(title: str, service: str, past: Sequence[PastIncident], w: Windows,
            current_id: str = "") -> tuple[Similar, ...]:
    mine = tokens(title)
    found = []
    for item in past:
        if item.id == current_id or not w.past.contains(_at(item.created_at)):
            continue
        same = bool(norm(service)) and norm(item.service) == norm(service)
        sim = round(jaccard(mine, tokens(item.title)), 2)
        if same or sim >= SIM_THRESHOLD:
            found.append(Similar(item, sim, same))
    found.sort(key=lambda s: (not s.same_service, -s.sim, -(_at(s.past.created_at) or w.t0).timestamp()))
    return tuple(found[:TOP_SIMILAR])
