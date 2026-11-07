"""Pure: flaky or real, from the run history, decided in code.

Each history run of the failing job is green (the job succeeded), red (it failed and the failing test
shows in its reduced log), or unknown (it failed but was not inspected, or the test was not found).
n = green + red + unknown, k = red, u = unknown. Three patterns:

- rerun flake: one head commit has both a red and a green run (a rerun passed);
- intermittent: k ≥ 2 and, on the target branch, a green run between two red ones with no change to the
  failing files in between;
- boundary: on a run sequence, the last ≥ 2 runs before commit B are green and every run from B on is red.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal

State = Literal["green", "red", "unknown"]
VerdictKind = Literal["real", "flaky", "infra", "unclear"]

HISTORY_RUNS = 20
MAX_INSPECTED = 6
WINDOW_DAYS = 30
MIN_GREEN_BEFORE = 2


@dataclass(frozen=True)
class Row:
    run_id: str
    attempt: str
    sha: str
    branch: str
    created_at: datetime
    state: State
    url: str = ""


@dataclass(frozen=True)
class Boundary:
    sha: str              # B: the first red commit
    last_green_sha: str


@dataclass(frozen=True)
class Flakiness:
    n: int
    k: int
    u: int
    rerun_sha: str = ""            # a commit that went red and then green
    intermittent: bool = False
    boundary: Boundary | None = None
    since: datetime | None = None

    @property
    def rerun_flake(self) -> bool:
        return bool(self.rerun_sha)


def window(t_fail: datetime) -> tuple[datetime, datetime]:
    return t_fail - timedelta(days=WINDOW_DAYS), t_fail


def select(rows: Sequence[Row], target_branch: str, head_branch: str, t_fail: datetime,
           failed_sha: str = "") -> tuple[Row, ...]:
    """The last HISTORY_RUNS completed runs on the target branch plus every run on the head branch,
    inside [t_fail − 30 days, t_fail], oldest first, each (run, attempt) once. A later run of the failed
    commit itself (a rerun) is kept too: it is what tells a flake from a break."""
    start, end = window(t_fail)
    inside = [r for r in _unique(rows)
              if start <= r.created_at <= end or (failed_sha and r.sha == failed_sha and r.created_at > end)]
    target = sorted((r for r in inside if r.branch == target_branch), key=_when)[-HISTORY_RUNS:]
    head = [r for r in inside if head_branch and r.branch == head_branch and r.branch != target_branch]
    return tuple(sorted({*target, *head}, key=_when))


def to_inspect(rows: Sequence[Row], failed_run_id: str) -> tuple[str, ...]:
    """The run ids whose logs the history step fetches: the newest failed ones, at most MAX_INSPECTED."""
    failed = [r for r in sorted(rows, key=_when, reverse=True) if r.state != "green" and r.run_id != failed_run_id]
    return tuple(dict.fromkeys(r.run_id for r in failed))[:MAX_INSPECTED]


def assess(rows: Sequence[Row], target_branch: str, head_branch: str,
           change_times: Sequence[datetime] = ()) -> Flakiness:
    ordered = sorted(_unique(rows), key=_when)
    k = sum(r.state == "red" for r in ordered)
    u = sum(r.state == "unknown" for r in ordered)
    on_target = [r for r in ordered if r.branch == target_branch]
    head = [r for r in ordered if head_branch and r.branch == head_branch]
    boundary = (find_boundary(head) if head_branch and head_branch != target_branch else None) or find_boundary(on_target)
    return Flakiness(n=len(ordered), k=k, u=u, rerun_sha=_rerun_sha(ordered),
                     intermittent=k >= 2 and _intermittent(on_target, change_times), boundary=boundary,
                     since=ordered[0].created_at if ordered else None)


def _rerun_sha(rows: Sequence[Row]) -> str:
    for r in rows:
        if r.state == "red" and any(o.sha == r.sha and o.state == "green" for o in rows):
            return r.sha
    return ""


def _intermittent(rows: Sequence[Row], change_times: Sequence[datetime]) -> bool:
    reds = [i for i, r in enumerate(rows) if r.state == "red"]
    for a, b in pairwise(reds):
        if any(r.state == "green" for r in rows[a + 1:b]):
            start, end = rows[a].created_at, rows[b].created_at
            if not any(start < t <= end for t in change_times):
                return True
    return False


def find_boundary(rows: Sequence[Row]) -> Boundary | None:
    """B is the first red commit after the last green run; every later run is red, and the runs just
    before B are green (at least two, or the whole earlier sequence when it is shorter)."""
    seq = sorted(rows, key=_when)
    greens = [i for i, r in enumerate(seq) if r.state == "green"]
    if not greens:
        return None
    last = greens[-1]
    after = seq[last + 1:]
    if not after or any(r.state != "red" for r in after):
        return None
    before = seq[max(0, last + 1 - MIN_GREEN_BEFORE):last + 1]
    if len(before) < MIN_GREEN_BEFORE or any(r.state != "green" for r in before):
        return None
    return Boundary(sha=after[0].sha, last_green_sha=seq[last].sha)


def verdict_kind(failure_class: str, flaky: Flakiness) -> VerdictKind:
    if failure_class == "infra":
        return "infra"
    if flaky.boundary is not None and not flaky.rerun_flake:
        return "real"
    if flaky.rerun_flake or (flaky.intermittent and flaky.boundary is None):
        return "flaky"
    return "unclear"


def rate_sentence(flaky: Flakiness) -> str:
    """The k-of-n sentence, built in code only."""
    text = f"has failed {flaky.k} of the last {flaky.n} runs"
    if flaky.rerun_sha:
        text += f", and passed on a rerun of {flaky.rerun_sha[:7]}"
    if flaky.u:
        text += f" ({flaky.u} more failed runs were not inspected)"
    return text


def _when(row: Row) -> datetime:
    return row.created_at


def _unique(rows: Sequence[Row]) -> list[Row]:
    seen: dict[tuple[str, str], Row] = {}
    for r in rows:
        seen.setdefault((r.run_id, r.attempt), r)
    return list(seen.values())
