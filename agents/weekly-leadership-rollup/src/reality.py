"""Pure: the Status Report Reality Check (copied method, 2026-11-05; keep in sync by copying).

Rules D1 to D11 over one evidence unit (a ticket, or an epic and its non-cancelled children), the
evidence strings (from tracker fields only), the unmatched list and the unreported risks. Every date
comparison uses the as-of date passed in; nothing here reads a clock. A ticket whose `updated` could not
be read is left out of every staleness rule (D5, D7, D10) and noted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import statuses as st
from clean import MAX_CELL_CHARS, clean
from dates import plural
from linking import NONE
from records import Issue

HIGH, MEDIUM, LOW = "high", "medium", "low"
RISK_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}
STALE_DAYS = 14
DUE_SOON_DAYS = 14
RECENT_DAYS = 3
OLD_TICKET_DAYS = 14
EPIC_DONE_PCT = 0.9
EPIC_BEHIND_PCT = 0.5
MAX_UNREPORTED = 10

RULES: dict[str, tuple[str, str]] = {
    "D1": ("reported_done_not_done", HIGH), "D2": ("on_track_but_blocked", HIGH),
    "D3": ("on_track_not_started", HIGH), "D4": ("on_track_overdue", HIGH), "D5": ("on_track_stale", MEDIUM),
    "D6": ("epic_behind", HIGH), "D7": ("blocked_but_moving", LOW), "D8": ("blocked_but_done", MEDIUM),
    "D9": ("not_started_but_started", LOW), "D10": ("at_risk_not_visible", LOW),
    "D11": ("ticket_reassigned_or_unowned", MEDIUM),
}


@dataclass(frozen=True)
class Ctx:
    as_of: date
    stale_days: int = STALE_DAYS
    soon_days: int = DUE_SOON_DAYS
    status_map: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Facts:
    category: str
    is_epic: bool
    n_children: int
    n_done: int
    n_blocked: int
    n_in_progress: int
    due: date | None
    updated: date | None
    created: date | None
    blocked_label: bool
    assignee: str | None

    @property
    def pct_done(self) -> float:
        return self.n_done / self.n_children if self.n_children else 0.0

    @property
    def started(self) -> bool:
        return self.n_done + self.n_in_progress > 0 if self.is_epic else self.category in (st.IN_PROGRESS, st.DONE)


def category(issue: Issue, ctx: Ctx) -> str:
    return st.ticket_category(issue.status, issue.labels, ctx.status_map, issue.status_type,
                              open_blocker=bool(issue.open_blockers))


def facts(issue: Issue, children: Sequence[Issue], ctx: Ctx) -> Facts:
    cats = [category(c, ctx) for c in children]
    stamps = [] if issue.updated_unparsed else [d for d in (i.updated for i in (issue, *children)) if d]
    return Facts(category=category(issue, ctx), is_epic=bool(children), n_children=len(children),
                 n_done=cats.count(st.DONE), n_blocked=cats.count(st.BLOCKED), n_in_progress=cats.count(st.IN_PROGRESS),
                 due=issue.due, updated=max(stamps) if stamps else None, created=issue.created_at,
                 blocked_label=any(st.normalize(x) in st.BLOCKED_LABELS for x in issue.labels),
                 assignee=issue.assignee)


def fired(reported: str, owner: str | None, f: Facts, ctx: Ctx) -> list[str]:
    checks = _epic_checks(reported, f, ctx) if f.is_epic else _ticket_checks(reported, f, ctx)
    checks["D11"] = reported in (st.ON_TRACK, st.DONE) and bool(owner) and not (f.assignee or "").strip()
    return [rule for rule in RULES if checks.get(rule)]


def lead(rules: Sequence[str]) -> str:
    return min(rules, key=lambda r: (RISK_ORDER[RULES[r][1]], int(r[1:])))


def _ticket_checks(reported: str, f: Facts, ctx: Ctx) -> dict[str, bool]:
    c, on_track = f.category, reported == st.ON_TRACK
    return {
        "D1": reported == st.DONE and c in (st.TODO, st.IN_PROGRESS, st.BLOCKED),
        "D2": on_track and c == st.BLOCKED,
        "D3": on_track and c == st.TODO and (_due_soon_or_past(f, ctx) or _older_than(f.created, OLD_TICKET_DAYS, ctx)),
        "D4": on_track and _overdue(f, ctx) and c not in (st.DONE, st.CANCELLED),
        "D5": on_track and c == st.IN_PROGRESS and _older_than(f.updated, ctx.stale_days, ctx),
        "D7": (reported == st.BLOCKED and c == st.IN_PROGRESS and not f.blocked_label
               and _within(f.updated, RECENT_DAYS, ctx)),
        "D8": reported == st.BLOCKED and c == st.DONE,
        "D9": reported == st.NOT_STARTED and f.started,
        "D10": (reported == st.AT_RISK and c == st.TODO and f.due is None
                and _older_than(f.updated, ctx.stale_days, ctx)),
    }


def _epic_checks(reported: str, f: Facts, ctx: Ctx) -> dict[str, bool]:
    on_track, untouched = reported == st.ON_TRACK, not f.started
    moving = f.n_in_progress > 0 or f.category == st.IN_PROGRESS
    return {
        "D1": reported == st.DONE and f.pct_done < EPIC_DONE_PCT,
        "D2": on_track and (f.n_blocked >= 1 or f.category == st.BLOCKED),
        "D3": on_track and untouched and (_due_soon_or_past(f, ctx) or _older_than(f.created, OLD_TICKET_DAYS, ctx)),
        "D4": on_track and _overdue(f, ctx) and f.pct_done < 1,
        "D5": on_track and moving and _older_than(f.updated, ctx.stale_days, ctx),
        "D6": (reported in (st.ON_TRACK, st.DONE) and f.pct_done < EPIC_BEHIND_PCT and f.due is not None
               and ctx.as_of <= f.due <= ctx.as_of + timedelta(days=ctx.soon_days)),
        "D7": (reported == st.BLOCKED and f.n_blocked == 0 and f.category != st.BLOCKED and moving
               and _within(f.updated, RECENT_DAYS, ctx)),
        "D8": reported == st.BLOCKED and f.pct_done == 1,
        "D9": reported == st.NOT_STARTED and f.started,
        "D10": reported == st.AT_RISK and untouched and f.due is None and _older_than(f.updated, ctx.stale_days, ctx),
    }


def _overdue(f: Facts, ctx: Ctx) -> bool:
    return f.due is not None and f.due < ctx.as_of


def _due_soon_or_past(f: Facts, ctx: Ctx) -> bool:
    return f.due is not None and f.due <= ctx.as_of + timedelta(days=ctx.soon_days)


def _older_than(day: date | None, days: int, ctx: Ctx) -> bool:
    return day is not None and (ctx.as_of - day).days > days


def _within(day: date | None, days: int, ctx: Ctx) -> bool:
    return day is not None and 0 <= (ctx.as_of - day).days <= days


# --- evidence strings: tracker fields and the as-of date only, never model text ---------------------------

def ticket_evidence(issue: Issue, f: Facts, as_of: date) -> str:
    head = f"{clean(issue.key, MAX_CELL_CHARS)} '{clean(issue.title)}' is **{clean(issue.status, MAX_CELL_CHARS) or 'no status'}**"
    details = [d for d in (_updated(issue, f, as_of), f"assignee {clean(issue.assignee, MAX_CELL_CHARS) or 'unassigned'}") if d]
    blockers = ", ".join(clean(b.key, MAX_CELL_CHARS) for b in issue.open_blockers)
    text = head + f" ({'; '.join(details)})" + (f"; blocked by {blockers}" if blockers else "")
    due = _due(f.due, as_of)
    return f"{text}; {due}" if due else text


def epic_evidence(issue: Issue, f: Facts, as_of: date) -> str:
    pct = round(100 * f.pct_done)
    head = f"Epic {clean(issue.key, MAX_CELL_CHARS)} '{clean(issue.title)}'"
    parts = [f"{head}: {f.n_done} of {f.n_children} children done ({pct}%)"]
    if f.n_blocked:
        parts.append(f"{f.n_blocked} blocked")
    if f.n_in_progress:
        parts.append(f"{f.n_in_progress} in progress")
    due = _due(f.due, as_of)
    if due:
        parts.append(due)
    if f.updated:
        parts.append(f"last update {f.updated.isoformat()} ({_ago((as_of - f.updated).days)})")
    return ", ".join(parts)


def _updated(issue: Issue, f: Facts, as_of: date) -> str:
    if issue.updated_unparsed:
        return "updated date unreadable"
    return f"updated {f.updated.isoformat()}, {_ago((as_of - f.updated).days)}" if f.updated else ""


def _due(due: date | None, as_of: date) -> str:
    if due is None:
        return ""
    days = (due - as_of).days
    if days < 0:
        return f"due {due.isoformat()} ({plural(-days)} overdue)"
    if days == 0:
        return f"due {due.isoformat()} (today)"
    return f"due {due.isoformat()} (in {plural(days)})"


def _ago(days: int) -> str:
    if days == 0:
        return "today"
    return f"{plural(days)} ago" if days > 0 else f"{plural(-days)} after the as-of date"


# --- the check over matched report items -----------------------------------------------------------------

def run(items: Sequence[dict], issues: Sequence[Issue], ctx: Ctx, unreported: bool = False) -> dict:
    by_key = {i.key: i for i in issues}
    children = _children(issues, ctx)
    found, covered = [], set()
    for item in items:
        units = [(by_key[k], children.get(k, [])) for k in item["match"]["ids"] if k in by_key]
        covered |= {x.key for t, kids in units for x in (t, *kids)}
        if item["reported"] == st.UNKNOWN or not units:
            continue
        disagreement = _check(item, units, ctx)
        if disagreement:
            found.append(disagreement)
    found.sort(key=lambda d: (RISK_ORDER[d["risk"]], not d["headline"], d["order"]))
    result = {"disagreements": found, "unmatched": _unmatched(items), "report_items": len(items),
              "tickets": len(issues), "caveats": caveats(issues)}
    if unreported:
        result["unreported_risks"] = _unreported(issues, covered, ctx)
    return result


def display_order(disagreements: Sequence[dict]) -> list[dict]:
    """Live display order: risk, then days since `updated` (most stale first), ties by team then key."""
    return sorted(disagreements, key=lambda d: (RISK_ORDER[d["risk"]], -(d.get("days_since_update") or -1),
                                                d.get("team") or "", (d["tickets"] or [""])[0]))


def caveats(issues: Sequence[Issue]) -> list[str]:
    notes = []
    unparsed = sum(1 for i in issues if i.updated_unparsed)
    if unparsed:
        notes.append(f"{plural(unparsed, 'ticket')} had an unreadable updated date and are left out of the "
                     "staleness rules.")
    if issues and not any(i.last_comment_at for i in issues):
        notes.append("Staleness uses the last update only: the tracker returned no comment dates.")
    if issues and not any(i.blocked_by for i in issues):
        notes.append("No blocking links were returned: blocked comes from status and labels only.")
    return notes


def _children(issues: Sequence[Issue], ctx: Ctx) -> dict[str, list[Issue]]:
    out: dict[str, list[Issue]] = {}
    for i in issues:
        if i.parent_key and i.parent_key != i.key and category(i, ctx) != st.CANCELLED:
            out[i.parent_key] = [*out.get(i.parent_key, []), i]
    return out


def _check(item: dict, units: list[tuple[Issue, list[Issue]]], ctx: Ctx) -> dict | None:
    fired_all: list[str] = []
    lines: list[str] = []
    hit: list[Issue] = []
    updated: list[date] = []
    for issue, kids in units:
        f = facts(issue, kids, ctx)
        rules = fired(item["reported"], item.get("owner"), f, ctx)
        if rules:
            fired_all += [r for r in rules if r not in fired_all]
            lines.append(epic_evidence(issue, f, ctx.as_of) if f.is_epic else ticket_evidence(issue, f, ctx.as_of))
            hit.append(issue)
            updated += [f.updated] if f.updated else []
    if not fired_all:
        return None
    first = lead(fired_all)
    return {"item": item["item"], "item_id": item["id"], "reported": item["reported_words"] or st.label(item["reported"]),
            "reported_category": item["reported"], "evidence": "; ".join(lines), "risk": RULES[first][1],
            "rule": first, "rule_name": RULES[first][0], "rules": sorted(fired_all, key=lambda r: int(r[1:])),
            "tickets": [i.key for i in hit], "quote": item["snippet"], "team": hit[0].team,
            "links": [i.url for i in hit if i.url], "permalink": item.get("permalink") or "",
            "author": item.get("author") or "", "date": item.get("date") or "", "source": item.get("source") or "",
            "headline": bool(item.get("headline")), "order": int(item["id"][1:]),
            "days_since_update": (ctx.as_of - max(updated)).days if updated else None}


def _unmatched(items: Sequence[dict]) -> list[dict]:
    out = []
    for i in items:
        if i["reported"] == st.UNKNOWN:
            reason = "status words not recognised"
        elif i["match"]["kind"] == NONE:
            reason = "no ticket found"
        else:
            continue
        out.append({"item": i["item"], "reported": i["reported_words"], "reason": reason, "quote": i["snippet"],
                    "permalink": i.get("permalink") or ""})
    return out


def _unreported(issues: Sequence[Issue], covered: set[str], ctx: Ctx) -> list[dict]:
    out = []
    for i in issues:
        if i.key in covered:
            continue
        f = facts(i, [], ctx)
        overdue = f.due is not None and f.due < ctx.as_of and f.category not in (st.DONE, st.CANCELLED)
        if f.category == st.BLOCKED or overdue:
            out.append({"ticket": i.key, "team": i.team, "url": i.url, "evidence": ticket_evidence(i, f, ctx.as_of),
                        "_key": (f.category != st.BLOCKED, f.due or ctx.as_of, i.team, i.key)})
    out.sort(key=lambda u: u["_key"])
    return [{k: v for k, v in u.items() if k != "_key"} for u in out[:MAX_UNREPORTED]]
