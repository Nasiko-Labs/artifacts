"""Pure: per-person figures and the fixed rules that raise signals. Every number is computed here.

Each person is compared only with their own baseline (the 4 weeks before the window). The one
cross-person figure is the team review share, used for the team view's load distribution.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from people import Team
from records import Activity, Issue, PullRequest, Signal
from windows import Window, days_between, hours_between, is_after_hours, local_date

REVIEW_SHARE_FLAG = 0.5
REVIEW_SHARE_MIN_REVIEWS = 8
TURNAROUND_FACTOR = 2.0
TURNAROUND_MIN_HOURS = 24.0
TURNAROUND_MIN_COUNT = 3
OPEN_REQUEST_HOURS = 48.0
STUCK_DAYS = 7
QUIET_MIN_BASE = 10.0
QUIET_RATIO = 0.3
AFTER_HOURS_MIN_COMMITS = 5
AFTER_HOURS_MIN_DAYS = 4
AFTER_HOURS_RISE = 0.15
AFTER_HOURS_NO_BASELINE_SHARE = 0.4
BASELINE_MIN_DAYS = 14
MAX_EVIDENCE = 5


@dataclass(frozen=True)
class ReviewPair:
    """One PR x reviewer: counted once, at the reviewer's first review."""

    pr: PullRequest
    reviewer: str
    first_at: datetime
    url: str
    requested_at: datetime


@dataclass(frozen=True)
class Card:
    label: str
    tz_name: str | None
    has_baseline: bool
    prs_w: int
    prs_b_week: float
    reviews_w: int
    reviews_b_week: float
    share_w: float | None
    share_b: float | None
    turnaround_w: float | None
    turnaround_b: float | None
    open_requests: tuple[tuple[str, float], ...]
    commits_w: int
    commits_b_week: float
    after_hours: tuple[float, float | None, int] | None  # share W, share B, distinct days W; None = no tz
    chat_w: int
    chat_b_week: float
    chat_per_day: tuple[float, float]
    chat_hours: tuple[tuple[str, int], ...]
    chat_sampled: bool
    chat_total: int | None
    tickets: tuple[tuple[Issue, int, int], ...]  # issue, days in status, days silent
    signals: tuple[Signal, ...]


@dataclass(frozen=True)
class TeamView:
    shares: tuple[tuple[str, float], ...]  # roster order
    reviews_w: int
    signals: tuple[Signal, ...]
    blocked: tuple[tuple[Issue, str, int, int], ...]  # issue, person, days in status, days silent


def review_pairs(team: Team, prs: Iterable[PullRequest]) -> tuple[ReviewPair, ...]:
    pairs = []
    for pr in prs:
        author = team.person_of(pr.author)
        if author is None:
            continue
        firsts: dict[str, tuple[datetime, str]] = {}
        for review in sorted(pr.reviews, key=lambda r: r.submitted_at):
            who = team.person_of(review.reviewer)
            if who is not None and who != author:
                firsts.setdefault(who, (review.submitted_at, review.url))
        for who, (at, url) in firsts.items():
            asked = [r.requested_at for r in pr.review_requests if team.person_of(r.reviewer) == who]
            pairs.append(ReviewPair(pr, who, at, url, min(asked) if asked else pr.created_at))
    return tuple(pairs)


def team_view(team: Team, activity: Activity, window: Window) -> TeamView:
    in_w = [p for p in review_pairs(team, activity.prs) if window.in_window(p.first_at)]
    total = len(in_w)
    shares = tuple((label, _share([p for p in in_w if p.reviewer == label], total)) for label in team.labels())
    signals = tuple(
        Signal("review_concentrated", label, {"share": share, "reviews": round(share * total), "total": total},
               tuple(p.url for p in in_w if p.reviewer == label)[:MAX_EVIDENCE])
        for label, share in shares if total >= REVIEW_SHARE_MIN_REVIEWS and share >= REVIEW_SHARE_FLAG
    )
    blocked = sorted(((i, label, d, s) for label in team.labels()
                      for i, d, s in _tickets(team, activity.issues, label, window.today) if _is_stuck(i, d)),
                     key=lambda row: -row[2])
    return TeamView(shares, total, signals, tuple(blocked))


def cards(team: Team, activity: Activity, window: Window) -> tuple[Card, ...]:
    pairs = review_pairs(team, activity.prs)
    zones = {c.person: c.tz for c in activity.chat if c.tz}
    return tuple(_card(label, zones.get(label) or tz, team, activity, pairs, window)
                 for label, tz in ((p.label, p.tz) for p in team.people))


def _card(label: str, tz, team: Team, activity: Activity, pairs: Sequence[ReviewPair], window: Window) -> Card:
    prs = [p for p in activity.prs if team.person_of(p.author) == label]
    opened = [p.created_at for p in prs]
    mine = [p for p in pairs if p.reviewer == label]
    commits = [c for c in activity.commits if team.person_of(c.author) == label]
    chat = next((c for c in activity.chat if c.person == label), None)
    stamps = list(chat.timestamps) if chat else []
    events = [*opened, *(p.first_at for p in mine), *(c.authored_at for c in commits), *stamps]
    baseline = has_baseline(events, window)
    tickets = _tickets(team, activity.issues, label, window.today)
    share_w = _share([p for p in mine if window.in_window(p.first_at)],
                     sum(window.in_window(p.first_at) for p in pairs))
    share_b = _share([p for p in mine if window.in_baseline(p.first_at)],
                     sum(window.in_baseline(p.first_at) for p in pairs))
    turn_w, turn_b, n_w = _turnarounds(mine, window)
    ah = _after_hours(commits, tz, window)
    signals = (*_turnaround_signal(label, mine, turn_w, turn_b, n_w, baseline, window),
               *_quiet_signal(label, events, prs, mine, commits, baseline, window),
               *_after_hours_signal(label, ah, commits, tz, window),
               *(_stuck_signal(label, i, d, s) for i, d, s in tickets if _is_stuck(i, d)))
    return Card(
        label=label, tz_name=str(tz) if tz else None, has_baseline=baseline,
        prs_w=_count(opened, window.in_window), prs_b_week=_weekly(opened, window),
        reviews_w=_count([p.first_at for p in mine], window.in_window),
        reviews_b_week=_weekly([p.first_at for p in mine], window), share_w=share_w, share_b=share_b,
        turnaround_w=turn_w, turnaround_b=turn_b, open_requests=_open_requests(team, label, activity.prs, window),
        commits_w=_count([c.authored_at for c in commits], window.in_window),
        commits_b_week=_weekly([c.authored_at for c in commits], window), after_hours=ah,
        chat_w=_count(stamps, window.in_window), chat_b_week=_weekly(stamps, window),
        chat_per_day=_per_workday(stamps, window),
        chat_hours=_hours([t for t in stamps if window.in_window(t)], tz),
        chat_sampled=bool(chat and chat.sampled), chat_total=chat.total_reported if chat else None,
        tickets=tuple(tickets), signals=tuple(s for s in signals if s.evidence),
    )


def has_baseline(events: Iterable[datetime], window: Window) -> bool:
    """At least two weeks of baseline: some activity at least BASELINE_MIN_DAYS before the window."""
    cutoff = window.start - timedelta(days=BASELINE_MIN_DAYS)
    return any(window.in_baseline(e) and e <= cutoff for e in events)


def _turnarounds(mine: Sequence[ReviewPair], window: Window) -> tuple[float | None, float | None, int]:
    def hours(inside) -> list[float]:
        return [hours_between(p.requested_at, p.first_at) for p in mine
                if inside(p.requested_at) and p.first_at >= p.requested_at]
    w, b = hours(window.in_window), hours(window.in_baseline)
    return (median(w) if w else None), (median(b) if b else None), len(w)


def _turnaround_signal(label, mine, turn_w, turn_b, n_w, baseline, window) -> tuple[Signal, ...]:
    if not baseline or turn_w is None or turn_b is None or n_w < TURNAROUND_MIN_COUNT:
        return ()
    if turn_w < TURNAROUND_FACTOR * turn_b or turn_w < TURNAROUND_MIN_HOURS:
        return ()
    urls = tuple(p.url for p in mine if window.in_window(p.requested_at))[:MAX_EVIDENCE]
    return (Signal("turnaround_up", label, {"w": turn_w, "b": turn_b, "n": n_w}, urls),)


def _open_requests(team: Team, label: str, prs: Iterable[PullRequest], window: Window) -> tuple[tuple[str, float], ...]:
    out = []
    for pr in prs:
        for req in pr.review_requests:
            if team.person_of(req.reviewer) != label or not window.in_window(req.requested_at):
                continue
            answered = any(team.person_of(r.reviewer) == label and r.submitted_at >= req.requested_at
                           for r in pr.reviews)
            age = hours_between(req.requested_at, window.end)
            if not answered and age > OPEN_REQUEST_HOURS:
                out.append((pr.url, age))
    return tuple(dict(out).items())[:MAX_EVIDENCE]


def _quiet_signal(label, events, prs, mine, commits, baseline, window) -> tuple[Signal, ...]:
    last7 = _count(events, window.in_recent)
    base = _weekly(events, window)
    if not baseline or base < QUIET_MIN_BASE or last7 > QUIET_RATIO * base:
        return ()
    dated = sorted([*((p.created_at, p.url) for p in prs), *((p.first_at, p.url) for p in mine),
                    *((c.authored_at, c.url) for c in commits)], reverse=True)
    urls = tuple(dict.fromkeys(u for _, u in dated))[:3]
    return (Signal("quiet", label, {"last7": last7, "base": base}, urls),)


def _after_hours(commits, tz, window: Window) -> tuple[float, float | None, int] | None:
    if tz is None:
        return None
    w = [c.authored_at for c in commits if window.in_window(c.authored_at)]
    b = [c.authored_at for c in commits if window.in_baseline(c.authored_at)]
    late_w = [t for t in w if is_after_hours(t, tz)]
    days = len({local_date(t, tz) for t in late_w})
    share_b = (sum(is_after_hours(t, tz) for t in b) / len(b)) if b else None
    return (len(late_w) / len(w) if w else 0.0), share_b, days


def _after_hours_signal(label, ah, commits, tz, window: Window) -> tuple[Signal, ...]:
    if ah is None:
        return ()
    share_w, share_b, days = ah
    count_w = _count([c.authored_at for c in commits], window.in_window)
    if count_w < AFTER_HOURS_MIN_COMMITS or days < AFTER_HOURS_MIN_DAYS:
        return ()
    rose = share_w >= share_b + AFTER_HOURS_RISE if share_b is not None else share_w >= AFTER_HOURS_NO_BASELINE_SHARE
    if not rose:
        return ()
    urls = tuple(c.url for c in commits if window.in_window(c.authored_at) and is_after_hours(c.authored_at, tz))
    return (Signal("after_hours_commits", label, {"w": share_w, "b": share_b, "days": days}, urls[:MAX_EVIDENCE]),)


def _tickets(team: Team, issues: Iterable[Issue], label: str, today: date) -> list[tuple[Issue, int, int]]:
    out = []
    for issue in issues:
        if team.person_of(issue.assignee) != label:
            continue
        last = max(issue.status_since, issue.last_comment_at) if issue.last_comment_at else issue.status_since
        out.append((issue, days_between(issue.status_since, today), days_between(last, today)))
    return out


def _is_stuck(issue: Issue, days_in_status: int) -> bool:
    commented = issue.last_comment_at is not None and issue.last_comment_at >= issue.status_since
    return days_in_status >= STUCK_DAYS and not commented


def _stuck_signal(label: str, issue: Issue, days: int, silent: int) -> Signal:
    return Signal("stuck_ticket", label, {"key": issue.key, "status": issue.status, "days": days, "silent": silent},
                  (issue.url,))


def _share(mine: Sequence, total: int) -> float | None:
    return len(mine) / total if total else None


def _count(times: Iterable[datetime], inside) -> int:
    return sum(1 for t in times if inside(t))


def _weekly(times: Iterable[datetime], window: Window) -> float:
    return _count(times, window.in_baseline) / window.baseline_weeks


def _per_workday(stamps: Sequence[datetime], window: Window) -> tuple[float, float]:
    def workdays(start: datetime, days: int) -> int:
        return sum((start + timedelta(days=d)).weekday() < 5 for d in range(days)) or 1
    w = _count(stamps, window.in_window) / workdays(window.start, (window.end - window.start).days)
    b = _count(stamps, window.in_baseline) / workdays(window.baseline_start, (window.start - window.baseline_start).days)
    return w, b


def _hours(stamps: Sequence[datetime], tz) -> tuple[tuple[str, int], ...]:
    """Three-hour buckets of message times (local when the zone is known, else UTC)."""
    buckets = {f"{h:02d}-{h + 3:02d}": 0 for h in range(0, 24, 3)}
    for t in stamps:
        hour = (t.astimezone(tz) if tz else t).hour // 3 * 3
        key = f"{hour:02d}-{hour + 3:02d}"
        buckets = {**buckets, key: buckets[key] + 1}
    return tuple((k, v) for k, v in buckets.items() if v)
