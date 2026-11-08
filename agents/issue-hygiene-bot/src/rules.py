"""Pure: the four hygiene rules, the estimate-adoption gate, the suggested actions and the team scores.
Every date, count, threshold and percentage is computed here; the model never decides a rule.

- H1 no project: open and no project.
- H2 no estimate: open and no estimate, only for a team where at least `estimate_adoption_pct` percent of
  its read open issues carry an estimate (else skipped for that team, and said so).
- H3 stale in progress: started and last updated at least `stale_days` days ago.
- H4 open 90+ days: open and created at least `old_days` days ago.
Ages are whole days between today and the date (UTC). Triage and backlog are exempt from H3 only."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from config import Config
from dates import parse_day
from records import Finding, Issue

ALL_RULES = ("H1", "H2", "H3", "H4")
RULE_ORDER = ("H3", "H4", "H1", "H2")   # most urgent first
CLOSED_TYPES = frozenset({"completed", "canceled"})
_CLOSED_WORDS = frozenset({"done", "completed", "canceled", "cancelled", "closed", "duplicate", "released", "merged"})
ACTIONS = {
    "H1": "Add it to a project, or close it if it is no longer planned",
    "H2": "Add an estimate",
    "H3": "Post an update, move it back to Todo, or mark it blocked",
    "H4": "Decide: schedule it, or close it",
}
RULE_NAMES = {"H1": "no project", "H2": "no estimate", "H3": "stale in progress", "H4": "open 90+ days"}


@dataclass(frozen=True)
class TeamScore:
    team: str
    open_total: int
    flagged: int
    uses_estimates: bool

    @property
    def percent(self) -> int:
        return round(100 * self.flagged / self.open_total) if self.open_total else 0


@dataclass(frozen=True)
class Evaluation:
    findings: tuple[Finding, ...]
    scores: tuple[TeamScore, ...]

    @property
    def open_total(self) -> int:
        return sum(s.open_total for s in self.scores)

    @property
    def no_estimate_teams(self) -> tuple[str, ...]:
        return tuple(s.team for s in self.scores if not s.uses_estimates)


def is_open(issue: Issue) -> bool:
    if issue.state_type:
        return issue.state_type not in CLOSED_TYPES
    return issue.state.casefold() not in _CLOSED_WORDS


def days_since(value: str, today: date) -> int | None:
    day = parse_day(value)
    return None if day is None else (today - day).days


def team_of(issue: Issue) -> str:
    return issue.team or "Unknown team"


def uses_estimates(open_issues: Sequence[Issue], config: Config) -> bool:
    if not open_issues:
        return False
    estimated = sum(1 for i in open_issues if i.estimate is not None)
    return estimated * 100 >= config.estimate_adoption_pct * len(open_issues)


def broken_rules(issue: Issue, today: date, config: Config, estimates_used: bool) -> tuple[str, ...]:
    if not is_open(issue):
        return ()
    age, idle = days_since(issue.created_at, today), days_since(issue.updated_at, today)
    broken = {
        "H1": issue.project is None,
        "H2": estimates_used and issue.estimate is None,
        "H3": issue.state_type == "started" and idle is not None and idle >= config.stale_days,
        "H4": age is not None and age >= config.old_days,
    }
    return tuple(rule for rule in RULE_ORDER if broken[rule])


def evaluate(issues: Iterable[Issue], today: date, config: Config,
             wanted: Sequence[str] = ALL_RULES) -> Evaluation:
    open_issues = [i for i in issues if is_open(i)]
    teams = list(dict.fromkeys(team_of(i) for i in open_issues))
    adoption = {t: uses_estimates([i for i in open_issues if team_of(i) == t], config) for t in teams}
    findings: list[Finding] = []
    for issue in open_issues:
        rules = tuple(r for r in broken_rules(issue, today, config, adoption[team_of(issue)]) if r in wanted)
        if rules:
            findings.append(Finding(issue, rules, days_since(issue.created_at, today),
                                    days_since(issue.updated_at, today), tuple(ACTIONS[r] for r in rules)))
    scores = tuple(TeamScore(t, sum(1 for i in open_issues if team_of(i) == t),
                             sum(1 for f in findings if team_of(f.issue) == t), adoption[t]) for t in teams)
    return Evaluation(tuple(sorted(findings, key=finding_order)), scores)


def finding_order(finding: Finding) -> tuple:
    """H3 first, then H4, H1, H2; oldest first within a rule; then by key."""
    rank = min(RULE_ORDER.index(r) for r in finding.rules)
    return rank, -(finding.age_days if finding.age_days is not None else -1), finding.issue.id


def why(finding: Finding) -> str:
    """'stale 19 days in progress; open 134 days' — the numbers are code's."""
    parts = []
    for rule in finding.rules:
        if rule == "H3":
            parts.append(f"stale {finding.days_since_update} days in progress")
        elif rule == "H4":
            parts.append(f"open {finding.age_days} days")
        else:
            parts.append(RULE_NAMES[rule])
    return "; ".join(parts)
