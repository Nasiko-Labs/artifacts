"""Pure: classify one person's checked records into Shipped, In progress, Blocked and Reviews given.

Every rule is code; the model never decides what is blocked. `now` is the window end, so a
re-render from stored records gives the same lines.

- Shipped: their pull requests merged in the window; their tracker issues in a done status completed
  in the window, unless the issue's key is in one of their listed pull requests' title or branch.
- In progress: their open pull requests (draft, reviewed, or waiting for review); their started issues
  not linked to a listed pull request; commits in the window on branches with no pull request.
- Blocked: an open pull request with changes requested and no push since, failing checks, a merge
  conflict, or no review for MIN_WAIT_DAYS+ days; an issue with a blocked status or label or a
  blocking link; an open item whose latest comment in the window says one of BLOCK_PHRASES.
- Reviews given: reviews they submitted in the window on someone else's pull request.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from identity import Person
from records import Comment, Issue, PullRequest, Records
from window import Window

MIN_WAIT_DAYS = 2
BLOCK_PHRASES = ("blocked on", "blocked by", "waiting for", "waiting on", "need access", "can't proceed",
                 "cannot proceed")


@dataclass(frozen=True)
class Line:
    kind: str  # pr | issue | commits | review
    ref: str
    url: str
    title: str
    note: str  # state suffix: "merged", "waiting for review", "done", "3 commits on `x`"...
    at: datetime | None
    reason: str = ""  # blocked lines: why, computed in code
    quote: str = ""  # blocked lines: the phrase found in the latest comment, verbatim
    blocked_on: str = ""  # blocked lines: the person or thing named by the tool record, else "unknown"
    links: tuple[str, ...] = ()  # issue keys of the tracker items this pull request covers

    def to_dict(self) -> dict:
        return {"text": self.title or self.ref, "kind": self.kind, "ref": self.ref, "url": self.url,
                "note": self.note, "reason": self.reason, "quote": self.quote, "blocked_on": self.blocked_on}


@dataclass(frozen=True)
class PersonStandup:
    name: str
    shipped: tuple[Line, ...] = ()
    in_progress: tuple[Line, ...] = ()
    blocked: tuple[Line, ...] = ()
    reviews: tuple[Line, ...] = ()

    @property
    def silent(self) -> bool:
        return not (self.shipped or self.in_progress or self.blocked)


def classify(person: Person, records: Records, window: Window) -> PersonStandup:
    prs = [p for p in records.prs if person.owns(p.author)]
    issues = [i for i in records.issues if person.owns(i.assignee)]
    shipped_prs = [p for p in prs if p.state == "merged" and window.contains(p.merged_at)]
    open_prs = [p for p in prs if p.state == "open"]
    listed = (*shipped_prs, *open_prs)
    blocked = [_pr_blocked(p, window) for p in open_prs]
    blocked_lines = [b for b in blocked if b is not None]
    blocked_refs = {b.ref for b in blocked_lines}
    shipped = [_pr_line(p, "merged", p.merged_at, issues) for p in shipped_prs]
    progress = [_pr_line(p, _pr_state(p), _last_activity(p), issues) for p in open_prs if p.ref not in blocked_refs]
    for issue in issues:
        if _linked(issue, listed):
            continue
        if issue.category == "done":
            if window.contains(issue.completed_at or issue.updated_at):
                shipped.append(_issue_line(issue, "done", issue.completed_at or issue.updated_at))
            continue
        stuck = _issue_blocked(issue, window)
        if stuck is not None:
            blocked_lines.append(stuck)
        elif issue.category == "started":
            progress.append(_issue_line(issue, _short(issue.status), issue.updated_at))
    progress += _commit_lines(person, records, window)
    return PersonStandup(name=person.label, shipped=_newest(shipped), in_progress=_newest(progress),
                         blocked=_newest(blocked_lines), reviews=_newest(_reviews_given(person, records, window)))


def _pr_state(pr: PullRequest) -> str:
    if pr.draft:
        return "draft"
    return "reviewed" if pr.reviews else "waiting for review"


def _pr_line(pr: PullRequest, note: str, at: datetime | None, issues: Sequence[Issue]) -> Line:
    links = tuple(i.key for i in issues if _mentions(i.key, pr))
    return Line("pr", pr.ref, pr.url, pr.title, note, at, links=links)


def _issue_line(issue: Issue, note: str, at: datetime | None) -> Line:
    return Line("issue", issue.key, issue.url, issue.title, note, at)


def _pr_blocked(pr: PullRequest, window: Window) -> Line | None:
    reason, blocked_on = _pr_reason(pr, window.end)
    quote = _phrase(pr.comment, window)
    if not reason and not quote:
        return None
    return Line("pr", pr.ref, pr.url, pr.title, "open", _last_activity(pr), reason=reason or "comment",
                quote=quote, blocked_on=blocked_on or "unknown")


def _pr_reason(pr: PullRequest, now: datetime) -> tuple[str, str]:
    requested = _changes_requested(pr)
    if requested:
        return "changes requested", ", ".join(requested)
    if pr.checks == "failing":
        return "checks failing", "failing checks"
    if pr.conflict:
        return "merge conflict", "a merge conflict"
    if not pr.draft and not pr.reviews:
        waited = (now - pr.created_at) // timedelta(days=1)
        if waited >= MIN_WAIT_DAYS:
            return f"no review for {waited} days", "a review"
    return "", ""


def _changes_requested(pr: PullRequest) -> list[str]:
    latest: dict[str, tuple[datetime, str, str]] = {}
    for review in pr.reviews:
        who = review.reviewer.handle or review.reviewer.name
        if who.casefold() not in latest or review.submitted_at >= latest[who.casefold()][0]:
            latest[who.casefold()] = (review.submitted_at, review.state, who)
    pushed = pr.last_push_at
    return [f"@{who}" if " " not in who else who for at, state, who in latest.values()
            if state == "CHANGES_REQUESTED" and (pushed is None or pushed <= at)]


def _issue_blocked(issue: Issue, window: Window) -> Line | None:
    quote = _phrase(issue.comment, window)
    flagged = issue.category == "blocked" or bool(issue.blocked_by)
    if not flagged and not quote:
        return None
    reason = f"status \"{_short(issue.status)}\"" if issue.category == "blocked" else (
        "blocking link" if issue.blocked_by else "comment")
    return Line("issue", issue.key, issue.url, issue.title, _short(issue.status), issue.updated_at, reason=reason,
                quote=quote, blocked_on=", ".join(issue.blocked_by) or "unknown")


def _phrase(comment: Comment | None, window: Window) -> str:
    if comment is None or not window.contains(comment.at):
        return ""
    lowered = comment.body.casefold()
    for phrase in BLOCK_PHRASES:
        at = lowered.find(phrase)
        if at != -1:
            return comment.body[at:at + len(phrase)]
    return ""


def _commit_lines(person: Person, records: Records, window: Window) -> list[Line]:
    pr_branches = {p.branch for p in records.prs if p.branch}
    pr_numbers = {p.number for p in records.prs if p.number is not None}
    by_branch: dict[str, list] = {}
    for commit in records.commits:
        if not person.owns(commit.author) or not window.contains(commit.authored_at) or not commit.branch:
            continue
        if commit.branch in pr_branches or any(f"#{n}" in commit.message for n in pr_numbers):
            continue
        by_branch.setdefault(commit.branch, []).append(commit)
    lines = []
    for branch, commits in by_branch.items():
        newest = max(commits, key=lambda c: c.authored_at)
        count = len(commits)
        note = f"{count} commit{'s' if count != 1 else ''} on `{_short(branch).replace('`', '')}`"
        lines.append(Line("commits", branch, newest.url, "", note, newest.authored_at))
    return lines


def _reviews_given(person: Person, records: Records, window: Window) -> list[Line]:
    lines = []
    for pr in records.prs:
        if person.owns(pr.author):
            continue
        mine = [r for r in pr.reviews if person.owns(r.reviewer) and window.contains(r.submitted_at)]
        if mine:
            last = max(mine, key=lambda r: r.submitted_at)
            lines.append(Line("review", pr.ref, pr.url, pr.title, last.state.replace("_", " ").lower(),
                              last.submitted_at))
    return lines


def _linked(issue: Issue, prs: Sequence[PullRequest]) -> bool:
    return any(_mentions(issue.key, pr) for pr in prs)


def _mentions(key: str, pr: PullRequest) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![0-9])", re.IGNORECASE)
    return bool(pattern.search(pr.title) or pattern.search(pr.branch))


def _last_activity(pr: PullRequest) -> datetime:
    times = [pr.created_at, pr.last_push_at, *(r.submitted_at for r in pr.reviews)]
    return max(t for t in times if t is not None)


def _newest(lines: Sequence[Line]) -> tuple[Line, ...]:
    floor = datetime.min.replace(tzinfo=None)
    return tuple(sorted(lines, key=lambda line: line.at.replace(tzinfo=None) if line.at else floor, reverse=True))


def _short(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:60]
