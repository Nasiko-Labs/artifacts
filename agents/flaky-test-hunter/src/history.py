"""Pure: the flake signals, the classification and the ranking, all in code.

Inputs are the kept runs of the window (latest attempt each), the attempts whose failing tests were read,
and, per test file, the dates of the commits that touched it (None when that could not be checked).

- `rerun_flip`: the SHAs where an attempt failed on T and a later attempt of the same run, or another run
  of the same workflow on the same SHA, was green.
- `main_flip`: runs that failed on T whose previous and next runs (same workflow and branch) were green,
  with no commit touching T's file between them; "unverified" when the commits could not be checked.
- `k`: runs where T was seen failing; `n`: runs of that workflow in the window (so k/n is a lower bound).
- flaky: `rerun_flip >= 1` or (`k >= 2` and `main_flip >= 1`); broken: failed in every inspected run
  since some commit, with no green run after it; else dropped.
- `score = 5*rerun_flip + 3*main_flip + k + (2 if the last failure is within 7 days)`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from records import Run, parse_time
from testids import Failure

GREEN = "success"
RED = ("failure", "timed_out")
RECENT = timedelta(days=7)
MAX_EVIDENCE = 3


@dataclass(frozen=True)
class Attempt:
    """One inspected attempt of one run: the failing tests read from its logs."""

    run_id: str
    attempt: int
    failures: tuple[Failure, ...]


@dataclass(frozen=True)
class Evidence:
    run: str
    sha: str
    date: str
    attempt: str
    url: str
    line: str

    def to_dict(self) -> dict:
        return {"run": self.run, "sha": self.sha, "date": self.date, "attempt": self.attempt, "url": self.url,
                "line": self.line}

    @staticmethod
    def from_dict(d: Mapping) -> Evidence:
        return Evidence(*(str(d.get(k) or "") for k in ("run", "sha", "date", "attempt", "url", "line")))


@dataclass(frozen=True)
class Flaky:
    test: str
    file: str
    name: str
    k: int
    n: int
    rerun_flip: int
    main_flip: int
    unverified: bool
    score: int
    last_failure: str
    evidence: tuple[Evidence, ...]
    rank: int = 0

    @property
    def rate_pct(self) -> int:
        return (100 * self.k) // self.n if self.n else 0

    def to_dict(self) -> dict:
        return {"rank": self.rank, "test": self.test, "file": self.file, "name": self.name, "k": self.k, "n": self.n,
                "rate_pct": self.rate_pct, "rerun_flip": self.rerun_flip, "main_flip": self.main_flip,
                "main_flip_unverified": self.unverified, "score": self.score, "last_failure": self.last_failure,
                "evidence": [e.to_dict() for e in self.evidence]}

    @staticmethod
    def from_dict(d: Mapping) -> Flaky:
        return Flaky(str(d.get("test") or ""), str(d.get("file") or ""), str(d.get("name") or ""), int(d.get("k") or 0),
                     int(d.get("n") or 0), int(d.get("rerun_flip") or 0), int(d.get("main_flip") or 0),
                     bool(d.get("main_flip_unverified")), int(d.get("score") or 0), str(d.get("last_failure") or ""),
                     tuple(Evidence.from_dict(e) for e in d.get("evidence") or () if isinstance(e, Mapping)),
                     int(d.get("rank") or 0))


@dataclass(frozen=True)
class Broken:
    test: str
    failed: int
    since_sha: str


@dataclass(frozen=True)
class Ranking:
    flaky: tuple[Flaky, ...] = ()
    broken: tuple[Broken, ...] = ()


@dataclass(frozen=True)
class _Seen:
    run: Run
    attempt: int
    failure: Failure


def analyse(runs: Sequence[Run], attempts: Sequence[Attempt], touches: Mapping[str, tuple[datetime, ...]] | None,
            now: datetime) -> Ranking:
    by_id = {r.run_id: r for r in runs}
    seen: dict[str, list[_Seen]] = {}
    for a in attempts:
        run = by_id.get(a.run_id)
        for f in a.failures if run else ():
            seen.setdefault(f.test_id, []).append(_Seen(run, a.attempt, f))
    inspected = {(a.run_id, a.attempt) for a in attempts}
    flaky, broken = [], []
    for test, sightings in seen.items():
        verdict = _classify(test, sightings, runs, inspected, touches, now)
        if isinstance(verdict, Flaky):
            flaky.append(verdict)
        elif isinstance(verdict, Broken):
            broken.append(verdict)
    ranked = sorted(flaky, key=lambda f: (-f.score, -f.rate_pct, f.test))
    numbered = tuple(_with_rank(f, i + 1) for i, f in enumerate(ranked))
    return Ranking(numbered, tuple(sorted(broken, key=lambda b: b.test)))


def _with_rank(f: Flaky, rank: int) -> Flaky:
    return Flaky(f.test, f.file, f.name, f.k, f.n, f.rerun_flip, f.main_flip, f.unverified, f.score, f.last_failure,
                 f.evidence, rank)


def _classify(test, sightings, runs, inspected, touches, now) -> Flaky | Broken | None:
    first = sightings[0].failure
    workflows = {s.run.workflow for s in sightings}
    pool = sorted((r for r in runs if r.workflow in workflows), key=lambda r: r.created_at)
    rerun = _rerun_flips(sightings, pool)
    main, unverified = _main_flips(first.file, sightings, pool, touches)
    k = len({s.run.run_id for s in sightings})
    if not rerun:
        since = _broken_since(test, sightings, pool, inspected)
        if since:
            return Broken(test, since[0], since[1])
    if not (rerun or (k >= 2 and main)):
        return None
    last = max(s.run.created_at for s in sightings)
    last_dt = parse_time(last)
    recent = 2 if last_dt is not None and now - last_dt <= RECENT else 0
    score = 5 * len(rerun) + 3 * main + k + recent
    return Flaky(test, first.file, first.name, k, len(pool), len(rerun), main, unverified, score,
                 last[:10], _evidence(sightings, pool))


def _rerun_flips(sightings: Sequence[_Seen], pool: Sequence[Run]) -> set[str]:
    shas = set()
    for s in sightings:
        later_green = s.run.conclusion == GREEN and s.run.attempt > s.attempt
        other_green = any(r.head_sha == s.run.head_sha and r.run_id != s.run.run_id and r.conclusion == GREEN
                          for r in pool)
        if later_green or other_green:
            shas.add(s.run.head_sha)
    return shas


def _main_flips(path: str, sightings: Sequence[_Seen], pool: Sequence[Run],
                touches: Mapping[str, tuple[datetime, ...]] | None) -> tuple[int, bool]:
    """(count, unverified): the commit check needs the test's file and the commit list for it."""
    dates = touches.get(path) if touches is not None and path else None
    count, unverified = 0, False
    order = [r.run_id for r in pool]
    for run_id in dict.fromkeys(s.run.run_id for s in sightings if s.run.conclusion in RED):
        i = order.index(run_id)
        if i == 0 or i == len(pool) - 1:
            continue
        before, after = pool[i - 1], pool[i + 1]
        if before.conclusion != GREEN or after.conclusion != GREEN or before.branch != after.branch:
            continue
        if dates is None:
            count, unverified = count + 1, True
        elif not _touched(dates, before.created_at, after.created_at):
            count += 1
    return count, unverified


def _touched(dates: tuple[datetime, ...], start: str, end: str) -> bool:
    lo, hi = parse_time(start), parse_time(end)
    return lo is None or hi is None or any(lo < d <= hi for d in dates)


def _broken_since(test: str, sightings, pool: Sequence[Run], inspected) -> tuple[int, str] | None:
    """(runs failed, first sha) when T failed in every run from its first failure on, with no green after."""
    failed_runs = {s.run.run_id for s in sightings if s.run.conclusion in RED}
    order = list(pool)
    start = next((i for i, r in enumerate(order) if r.run_id in failed_runs), None)
    if start is None:
        return None
    tail = order[start:]
    if len(tail) < 2 or any(r.conclusion not in RED for r in tail):
        return None
    if any((r.run_id, r.attempt) in inspected and r.run_id not in failed_runs for r in tail):
        return None
    return len(tail), tail[0].head_sha[:7]


def _evidence(sightings: Sequence[_Seen], pool: Sequence[Run]) -> tuple[Evidence, ...]:
    newest = sorted(sightings, key=lambda s: (s.run.created_at, s.attempt), reverse=True)
    out, used = [], set()
    for s in newest:
        if s.run.run_id in used:
            continue
        used.add(s.run.run_id)
        out.append(Evidence(s.run.run_id, s.run.head_sha[:7], s.run.created_at[:10], _attempt_text(s, pool),
                            s.run.url, s.failure.line))
        if len(out) == MAX_EVIDENCE:
            break
    return tuple(out)


def _attempt_text(s: _Seen, pool: Sequence[Run]) -> str:
    if s.run.conclusion == GREEN and s.run.attempt > s.attempt:
        return f"attempt {s.attempt} failed, attempt {s.run.attempt} passed"
    twin = next((r for r in pool if r.head_sha == s.run.head_sha and r.run_id != s.run.run_id
                 and r.conclusion == GREEN), None)
    suffix = f"; run {twin.run_id} on the same commit passed" if twin else ""
    return f"attempt {s.attempt} failed{suffix}"
