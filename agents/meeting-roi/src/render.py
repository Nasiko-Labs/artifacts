"""Pure: the report and the envelope, rendered in code from checked records. Links come only from tool
output (already checked); titles are escaped so calendar text cannot inject markdown or HTML."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import prompts
from records import MeetingRoi, Report

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>])")
_EXCLUDED = {"one_on_ones": ("one-on-one", "one-on-ones"),
             "external_guests": ("meeting with external guests", "meetings with external guests"),
             "not_organized_by_you": ("meeting you don't organize", "meetings you don't organize"),
             "below_size_filter": ("meeting under your size filter", "meetings under your size filter")}
_PROPOSAL_ORDER = {"cancel": 0, "ask the organizer": 1, "shrink": 2, "shorten": 3, "keep": 4, "insufficient data": 5}
FOOTER = ("Nothing was cancelled, edited or sent. Say \"draft a note to the {title} attendees\" for text you can "
          "copy and send yourself.")


def md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", " ".join((text or "").split()))


def num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{round(value * 100)} %"


def ordered(meetings: Sequence[MeetingRoi]) -> tuple[MeetingRoi, ...]:
    return tuple(sorted(meetings, key=lambda m: (_PROPOSAL_ORDER[m.proposal], -m.savings_hours_per_month,
                                                 -m.hours_per_month_person, m.series_title)))


def proposal_text(m: MeetingRoi) -> str:
    if m.proposal == "cancel":
        return "**Cancel** (you organize it)"
    if m.proposal == "ask the organizer":
        who = md(m.organizer) if m.organizer else "the organizer"
        return f"**Cancel?** You are a guest; suggest it to {who}"
    if m.proposal == "shrink":
        return f"**Shrink** to the decision makers (at most {m.proposed_size} people)"
    if m.proposal == "shorten":
        return f"**Shorten** {m.duration_min} to {m.proposed_minutes} min"
    if m.proposal == "insufficient data":
        return "**Not enough data** (fewer than 3 occurrences)"
    return "**Keep**"


def report(rep: Report, prior: Sequence[Mapping[str, Any]] = ()) -> str:
    meetings = ordered(rep.meetings)
    lines = [f"**Meeting ROI, last {rep.window_days} days** ({prompts.RSVP_NOTE})", _headline(rep, meetings)]
    lines += [f"Filters: {', '.join(rep.filters)}."] if rep.filters else []
    lines.append("")
    prior_by_title = {str(p.get("series_title")).casefold(): p for p in prior}
    for n, m in enumerate(meetings, start=1):
        lines += _meeting_lines(n, m, rep, prior_by_title.get(m.series_title.casefold()))
    tail = [excluded_text(rep.excluded)] if rep.excluded else []
    tail += ["Only the 25 meetings with the most person-hours (or the most recent 300 occurrences) are shown."] \
        if rep.capped else []
    tail += [f"- {line}" for line in rep.coverage]
    first = next((m for m in meetings if m.proposal != "keep"), meetings[0] if meetings else None)
    tail += [FOOTER.format(title=md(first.series_title))] if first else []
    return "\n".join([*lines, "", *tail]).strip()


def _headline(rep: Report, meetings: Sequence[MeetingRoi]) -> str:
    text = (f"You spend about {num(rep.total_hours)} person-hours a month in {len(meetings)} recurring "
            f"meeting{'s' if len(meetings) != 1 else ''}. Proposals could free {num(rep.total_savings)}.")
    if rep.rate:
        text += f" At {num(rep.rate)} an hour that is {num(round(rep.total_hours * rep.rate, 2))} a month, " \
                f"{num(round(rep.total_savings * rep.rate, 2))} of it in the proposals."
    return text


def _meeting_lines(n: int, m: MeetingRoi, rep: Report, prior: Mapping[str, Any] | None) -> list[str]:
    shape = ", ".join(x for x in (m.weekday, m.cadence, f"{m.duration_min} min", f"{m.invited} invited",
                                  f"{pct(m.accept_rate)} accepted", _notes_short(m)) if x)
    saves = f" Saves {num(m.savings_hours_per_month)} h/mo." if m.savings_hours_per_month else ""
    head = f"{n}. **{md(m.series_title)}** ({shape}) -> {proposal_text(m)}.{saves}"
    out = [head, f"   Evidence: {_evidence(m, rep)}"]
    if m.decision_quotes:
        out.append("   Decisions recorded: " + "; ".join(f"\"{md(q)}\"" for q in m.decision_quotes))
    links = " ".join(f"[{_label(link, m)}]({link})" for link in m.evidence_links)
    out += [f"   Links: {links}"] if links else []
    if prior is not None:
        out.append(f"   Earlier run: {md(str(prior.get('proposal') or 'n/a'))}, "
                   f"{md(str(prior.get('hours_per_month_person', 'n/a')))} h/mo.")
    return out


def _notes_short(m: MeetingRoi) -> str:
    return {None: "notes not checked", False: "no notes", True: "notes found"}[m.has_notes]


def _evidence(m: MeetingRoi, rep: Report) -> str:
    parts = [f"{m.occurrences} occurrence{'s' if m.occurrences != 1 else ''}",
             f"{num(m.accepted)} of {m.invited} accepted on average" if m.accept_rate is not None
             else "no response data", f"{num(m.hours_per_month_person)} person-hours a month"]
    if m.no_response_rate:
        parts.append(f"{pct(m.no_response_rate)} never answered")
    parts.append(_notes_evidence(m, rep))
    parts.append(_followup_evidence(m, rep))
    if m.cost_per_month is not None:
        parts.append(f"about {num(m.cost_per_month)} a month at your rate")
    if "CANCEL_NEEDS_NOTES_CHECK" in m.flags:
        parts.append("acceptance is under 50 %, but a cancel needs the notes check")
    parts.append(f"confidence {m.confidence}")
    return "; ".join(p for p in parts if p) + "."


def _notes_evidence(m: MeetingRoi, rep: Report) -> str:
    if m.has_notes is None:
        return "" if rep.pasted else "notes not available (documents could not be searched)"
    if not m.has_notes:
        return "no notes doc found (looked at event attachments and documents named like the meeting)"
    decided = {"true": "decisions recorded", "false": "no decisions recorded", "unclear": "decisions unclear",
               "unknown": "decisions not checked"}[m.has_decisions]
    stale = "" if m.notes_current is not False else ", not updated after most meetings"
    return f"notes doc found{stale}, {decided}"


def _followup_evidence(m: MeetingRoi, rep: Report) -> str:
    if m.followup_rate is not None:
        return f"chat follow-up after {pct(m.followup_rate)} of meetings in {md(m.followup_channel or '')}"
    if rep.pasted:
        return ""
    return "follow-up not available" if "chat" not in rep.sources_read else "no meeting channel found"


def _label(link: str, m: MeetingRoi) -> str:
    if link == m.notes_url:
        return "notes"
    return "chat" if m.followup_channel and "archives" in link or "/messages/" in link else "event"


def excluded_text(excluded: Mapping[str, int]) -> str:
    parts = [f"{count} {_EXCLUDED[key][count != 1]}" for key, count in excluded.items() if count and key in _EXCLUDED]
    return f"Not audited: {', '.join(parts)}." if parts else ""


def needs_human(rep: Report) -> bool:
    missing = rep.pasted or len(rep.sources_read) < 3
    return missing or rep.capped or any(m.proposal == "ask the organizer" for m in rep.meetings)


def confidence(rep: Report) -> str:
    if rep.pasted or rep.sources_read <= {"calendar"}:
        return "low"
    return "high" if len(rep.sources_read) == 3 else "medium"


def envelope(rep: Report, text: str) -> dict[str, Any]:
    meetings = [m.to_dict() for m in ordered(rep.meetings)]
    summary = " ".join(text.splitlines()[1:2]) or text[:200]
    return {"summary": summary, "items": meetings, "confidence": confidence(rep), "needs_human": needs_human(rep),
            "meetings": meetings,
            "window": {"start": rep.window_start.isoformat(), "end": rep.window_end.isoformat(),
                       "days": rep.window_days},
            "excluded": dict(rep.excluded), "coverage": list(rep.coverage), "attendance_basis": "rsvp",
            "nothing_changed": True}
