"""Pure: everything the user reads, rendered by code from the computed retro (markdown, no raw HTML, links
only from tool output), the envelope for workflows, and the post body (shortened by code when too long)."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import asdict

from dates import day
from metrics import Metrics, fmt_pct, fmt_points
from records import NO_REASON, Cycle, Issue, Spill
from retro import Retro

MAX_LISTED = 30
METHOD_NOTE = ("Planned = added to the cycle by its start + {hours} h; completed = done inside the cycle window; "
               "spilled = neither done nor canceled at its end. A reason is shown only with a verbatim quote.")
FALLBACK_NOTE = ("{n} issue(s) had no added-to-cycle time, so their creation time was used; scope added may be "
                 "overstated for issues created before but added later.")
_UNSAFE = re.compile(r"[<>\r\n]+")


def cycle_label(cycle: Cycle) -> str:
    if cycle.number:
        return f"cycle {cycle.number}" + (f" ({_clean(cycle.name)})" if cycle.name else "")
    return _clean(cycle.name) or "the cycle"


def headline(metrics: Metrics) -> str:
    spilled = "open so far" if metrics.in_progress else "spilled"
    return (f"Planned {metrics.planned_count} ({fmt_points(metrics.points_planned)} pts), completed "
            f"{metrics.completed_count} ({fmt_pct(metrics.completion_rate)}), added mid-cycle {metrics.added_count} "
            f"({fmt_pct(metrics.scope_added_pct)}), {spilled} {metrics.spilled_count} "
            f"({fmt_points(metrics.points_spilled)} pts).")


def title(retro: Retro) -> str:
    window = f"{day(retro.cycle.starts_at)} to {day(retro.cycle.ends_at)}"
    return f"# Retro: {_clean(retro.team.name)} {cycle_label(retro.cycle)} ({window})"


def retro_markdown(retro: Retro, drop: Collection[str] = (), notes: bool = True) -> str:
    m = retro.metrics
    parts = [title(retro)]
    if m.in_progress:
        parts.append("_In progress: numbers as of today._")
    extra = [f"Canceled {m.canceled_count}." if m.canceled_count else "",
             f"Unestimated {m.unestimated} (not in points)." if m.unestimated else ""]
    parts.append(" ".join([headline(m), *(e for e in extra if e)]))
    parts.append("## Shipped\n" + _lines([_issue(i) for i in retro.shipped], "Nothing was completed."))
    if "spilled" not in drop:
        heading = "## Open so far and why" if m.in_progress else "## Spilled and why"
        parts.append(heading + "\n" + _lines([spill_line(s) for s in retro.spills], "Nothing spilled."))
    parts.append("## Scope added\n" + _lines([_issue(i) for i in retro.added], "Nothing was added mid-cycle."))
    if "prompts" not in drop:
        parts.append("## Discussion prompts\n" + "\n".join(f"{n}. {p}" for n, p in enumerate(retro.prompts, 1)))
    if notes and retro.notes:
        parts.append("\n".join(f"_{n}_" for n in retro.notes))
    return "\n\n".join(parts)


def spill_line(s: Spill) -> str:
    head = f"- {_ref(s.issue_id, s.url)} {_clean(s.title)} ({fmt_points(s.estimate)} pts)".replace("  ", " ")
    if s.reason == NO_REASON or not s.quote:
        return f"{head}: no reason found"
    where = ", ".join(p for p in (s.source, day(s.posted_at)) if p)
    return f"{head}: {s.reason} - \"{_clean(s.quote)}\"" + (f" ({where})" if where else "")


def notes_for(metrics: Metrics, grace_hours: int, notes: Sequence[str]) -> tuple[str, ...]:
    fallback = (FALLBACK_NOTE.format(n=metrics.created_fallback),) if metrics.created_fallback else ()
    return tuple(dict.fromkeys([*notes, *fallback, METHOD_NOTE.format(hours=grace_hours)]))


def post_body(retro: Retro, drop: Collection[str], limit: int) -> tuple[str, bool]:
    """(the body to post, whether code shortened it). Notes are never posted."""
    full = retro_markdown(retro, drop, notes=False)
    if len(full) <= limit:
        return full, False
    lines = [title(retro), headline(retro.metrics), "## Spilled and why"]
    spills = [spill_line(s) for s in retro.spills] if "spilled" not in drop else []
    body = "\n".join(lines)
    for n, line in enumerate(spills):
        more = f"\n...and {len(spills) - n} more"
        if len(body) + len(line) + 1 + len(more) > limit:
            return body + more, True
        body += "\n" + line
    return body[:limit], True


def envelope(retro: Retro, markdown: str) -> dict:
    spilled = [{"issue": s.issue_id, "title": s.title, "url": s.url, "estimate": s.estimate, "reason": s.reason,
                "quote": s.quote or None, "source": s.source or None} for s in retro.spills]
    return {"summary": f"{title(retro)[2:]}. {headline(retro.metrics)}", "items": spilled,
            "confidence": retro.confidence, "needs_human": retro.needs_human, "retro_markdown": markdown,
            "metrics": asdict(retro.metrics), "spilled": spilled, "shipped": [_item(i) for i in retro.shipped],
            "scope_added": [_item(i) for i in retro.added], "prompts": list(retro.prompts),
            "team": retro.team.name, "cycle": asdict(retro.cycle), "notes": list(retro.notes)}


def copyable(body: str, reason: str) -> str:
    return f"{reason}\n\nHere is the retro to copy:\n\n{body}"


def _item(i: Issue) -> dict:
    return {"issue": i.id, "title": i.title, "url": i.url, "estimate": i.estimate, "completed_at": i.completed_at or None}


def _issue(i: Issue) -> str:
    done = f", done {day(i.completed_at)}" if i.completed_at else ""
    return f"- {_ref(i.id, i.url)} {_clean(i.title)} ({fmt_points(i.estimate)} pts{done})".replace("  ", " ")


def _lines(lines: list[str], empty: str) -> str:
    if not lines:
        return empty
    shown = lines[:MAX_LISTED]
    rest = len(lines) - len(shown)
    return "\n".join([*shown, *([f"- ...and {rest} more"] if rest else [])])


def _ref(issue_id: str, url: str) -> str:
    return f"[{issue_id}]({url})" if url.startswith(("https://", "http://")) else issue_id


def _clean(text: str) -> str:
    return _UNSAFE.sub(" ", text or "").strip()
