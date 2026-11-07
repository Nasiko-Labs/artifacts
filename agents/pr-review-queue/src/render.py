"""Pure: the markdown reply and the JSON envelope, rendered in code from the computed view. No raw
HTML, no images; links only from tool output; titles are flattened untrusted text."""

from __future__ import annotations

import json
import re
from collections import Counter

from queue_rules import KINDS, NOBODY, TEAM_PREFIX, Assessed, Blocked
from view import View

MAX_TITLE = 80
_UNSAFE = re.compile(r"[\r\n\t`<>\[\]|*_]+")


def title(text: str) -> str:
    flat = _UNSAFE.sub(" ", text or "").strip()
    return (flat[:MAX_TITLE - 1] + "…") if len(flat) > MAX_TITLE else flat or "(untitled)"


def who(name: str) -> str:
    if not name or name in (NOBODY, "unknown") or name.startswith(TEAM_PREFIX):
        return name or "unknown"
    return f"@{name}"


def ref(a: Assessed) -> str:
    label = f"{a.facts.repo}#{a.facts.pr.number}"
    return f"[{label}]({a.facts.pr.url})" if a.facts.pr.url.startswith("https://") else label


def primary(a: Assessed) -> Blocked | None:
    awaiting = next((b for b in a.blocked if b.kind.startswith("awaiting")), None)
    return awaiting or (a.blocked[0] if a.blocked else None)


def waiting_text(a: Assessed) -> str:
    b = primary(a)
    if b is None:
        return f"waiting on {who(a.facts.pr.author)} (no blocker found)"
    phrases = {
        "awaiting_reviewer": f"waiting on {who(b.who)} (no review in {b.since_days} d)",
        "awaiting_first_review": "no reviewer assigned",
        "ready_to_merge": f"ready to merge, waiting on {who(b.who)}",
        "changes_requested": f"waiting on {who(b.who)} (changes requested {b.since_days} d ago, no push since)",
        "failing_checks": f"waiting on {who(b.who)} (checks failing)",
        "merge_conflict": f"waiting on {who(b.who)} (merge conflict)",
    }
    return phrases[b.kind]


def header(v: View) -> str:
    repos = ", ".join(v.shown_repos) or "pasted pull requests"
    drafts = " · drafts included" if v.settings.include_drafts else ""
    return f"**Review queue** · {repos} · stale = quiet >= {v.settings.stale_days} days{drafts}"


def stale_lines(v: View, reasons: dict[int, str]) -> list[str]:
    lines = []
    for rank, a in enumerate(v.stale, start=1):
        pr = a.facts.pr
        lines.append(f"{rank}. {ref(a)} \"{title(pr.title)}\" by {who(pr.author)} · quiet {a.quiet_days} d · "
                     f"priority {a.priority} · {waiting_text(a)}")
        if reasons.get(rank):
            lines.append(f"   {reasons[rank]}")
    return lines


def blocked_line(v: View) -> str:
    counts = Counter(b.kind for b in v.blocked)
    parts = [f"{k} ({counts[k]})" for k in KINDS if counts[k]]
    return "**Blocked by:** " + (" · ".join(parts) if parts else "nothing")


def load_lines(v: View) -> list[str]:
    head = (f"**Load** (last {v.settings.window_days} d; overloaded = open requests >= {v.threshold:g}; idle = 0 "
            "reviews and 2+ open requests)")
    if not v.load:
        return [head, "- no reviewers or authors found"]
    rows = []
    for row in v.load:
        flags = ", ".join(f for f, on in (("overloaded", row.overloaded), ("idle", row.idle_reviewer)) if on)
        suffix = f" ({flags})" if flags else ""
        rows.append(f"- {who(row.person)}: {row.open_requests} open requests, oldest {row.oldest_wait_days} d, "
                    f"{row.reviews_done} reviews in {v.settings.window_days} d, {row.authored_open} authored{suffix}")
    return [head, *rows]


def coverage(v: View) -> list[str]:
    notes = list(v.notes)
    if v.pasted:
        notes.append("Computed from pasted data; nudges need the code host, so they are not offered.")
    return ["**Coverage:** " + " ".join(notes)] if notes else []


def queue_markdown(v: View, headline: str, reasons: dict[int, str]) -> str:
    parts = [header(v), f"Headline: {headline}"]
    if v.stale:
        parts.append("\n".join(["**Stale (priority order)**", *stale_lines(v, reasons)]))
    parts += [blocked_line(v), "\n".join(load_lines(v)), *coverage(v)]
    if v.stale and not v.pasted:
        parts.append("Say \"nudge #1\" to draft a nudge for the first PR, \"why #<number>\" for a PR's blockers, or "
                     "\"stale after 5 days\" to change the threshold.")
    return "\n\n".join(parts)


def load_markdown(v: View) -> str:
    return "\n\n".join([header(v), "\n".join(load_lines(v)), *coverage(v)])


def why_markdown(a: Assessed) -> str:
    pr = a.facts.pr
    head = (f"{ref(a)} \"{title(pr.title)}\" by {who(pr.author)} · open {a.age_days} d · quiet {a.quiet_days} d · "
            f"priority {a.priority}")
    lines = [head]
    lines += [f"- {b.kind}: {who(b.who)}, {b.since_days} d ({b.detail})" for b in a.blocked] or ["- no blocker found"]
    return "\n".join(lines)


def stale_pr(a: Assessed) -> dict:
    pr = a.facts.pr
    return {"repo": a.facts.repo, "number": int(pr.number), "title": pr.title, "author": pr.author or "unknown",
            "url": pr.url, "age_days": a.age_days, "quiet_days": a.quiet_days, "priority": a.priority,
            "waiting_on": a.waiting_on}


def envelope(v: View, headline: str) -> dict:
    stale = [stale_pr(a) for a in v.stale]
    return {"summary": headline, "items": stale, "confidence": v.confidence, "needs_human": v.needs_human,
            "stale_prs": stale, "blocked_by": [b.to_dict() for b in v.blocked],
            "load": [row.to_dict() for row in v.load]}


def as_json_text(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=2) + "\n```"
