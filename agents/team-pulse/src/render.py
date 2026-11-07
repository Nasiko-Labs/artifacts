"""Pure: the team view and the per-person cards, from templates in code. The model writes no prose
about people. Cards are in name order (alphabetical, or the order the user named people), never
sorted by any figure; every signal carries at least one evidence link; every card carries the
framing line."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta

import coverage as coverage_
import prompts
from people import Team, clean_label
from records import Activity, Signal
from signals import Card, TeamView
from windows import Window

CARD_FRAMING = (
    "Signals to ask about, not conclusions. Leave, focus time, on-call or work outside these tools explain "
    "most of them."
)
NO_SIGNALS = "No signals in this window."
NO_BASELINE = "No baseline yet (under 2 weeks of earlier activity), so nothing is compared with their usual."
NOT_AVAILABLE = "not available ({source} not read)"
NO_TZ = "not computed (no time zone known for them)"
BAR_WIDTH = 20
_URL = re.compile(r"^https?://\S+$")

SIGNAL_TEXT = {
    "review_concentrated": "Review load concentrated: {person} gave {pct}% of the team's {total} reviews in the window.",
    "turnaround_up": ("Review turnaround is longer than their usual: median {w} h over {n} reviews in the window, "
                      "{b} h in their previous 4 weeks."),
    "stuck_ticket": "{key} has been in \"{status}\" for {days} days with no comment since it entered that status.",
    "quiet": ("Activity in these tools in the last 7 days is well below their usual ({last7} vs {base}/week). "
              "Leave or focus work can explain this."),
    "after_hours_commits": ("More commits outside their working hours than usual: {w}% of commits in the window, "
                            "on {days} days ({b})."),
}


def report(team: Team, activity: Activity, window: Window, view: TeamView, cards: Sequence[Card],
           covered: coverage_.Coverage, partial: Sequence[str] = ()) -> str:
    shown = [c for c in cards if _has_activity(c)]
    empty = [c.label for c in cards if not _has_activity(c)]
    parts = [_header(window), CARD_FRAMING, *_notes(team, activity, covered, partial, empty),
             _team_view(view, covered), *(_card(c, covered.covered) for c in shown)]
    return "\n\n".join(p for p in parts if p)


def _header(window: Window) -> str:
    last = (window.end - timedelta(days=1)).date().isoformat()
    b_last = (window.start - timedelta(days=1)).date().isoformat()
    return (f"**Team pulse: {window.start.date().isoformat()} to {last}** (each person compared only with their own "
            f"previous 4 weeks, {window.baseline_start.date().isoformat()} to {b_last}). Read-only: nothing was "
            "posted or changed.")


def _notes(team: Team, activity: Activity, covered, partial, empty) -> list[str]:
    notes = list(coverage_.notes(covered))
    notes += [prompts.STEP_LIMIT_NOTE.format(source=coverage_.LABEL[s].lower()) for s in partial]
    notes += [prompts.CAPS_NOTE.format(what=w) for w in activity.caps_hit]
    if team.left_out:
        notes.append(f"{team.left_out} more people were active; I show the first {len(team.people)} by name. "
                     "Name the people you want to include them.")
    if empty:
        notes.append(prompts.NOTHING_FOR.format(names=", ".join(empty)))
    if any(c.sampled for c in activity.chat):
        notes.append("Chat timing for some people is from their most recent 200 messages.")
    return [f"_{n}_" for n in notes]


def _team_view(view: TeamView, covered: coverage_.Coverage) -> str:
    lines = ["**Team view**"]
    if "code" in covered.covered:
        lines.append(f"Review load: share of the {view.reviews_w} reviews on the team's pull requests in the "
                     "window, in name order.")
        lines += [f"- {clean_label(label)}: {_bar(share)} {_pct(share)}" for label, share in view.shares]
        lines += [f"- {signal_text(s)} Evidence: {_links(s.evidence)}" for s in view.signals]
    else:
        lines.append(f"Review load: {NOT_AVAILABLE.format(source='code host')}")
    lines.append("Blocked work (tickets 7+ days in their status with no comment since), longest first:")
    if "tracker" not in covered.covered:
        lines.append(f"- {NOT_AVAILABLE.format(source='tracker')}")
    elif not view.blocked:
        lines.append("- None found.")
    lines += [f"- {clean_label(i.key)} (\"{clean_label(i.status)}\"), {days} days in status, assigned to "
              f"{clean_label(who)}: {_link(i.url)}" for i, who, days, _ in view.blocked]
    return "\n".join(lines)


def _card(card: Card, covered: frozenset[str]) -> str:
    lines = [f"**{clean_label(card.label)}**", f"_{CARD_FRAMING}_", *_code_lines(card, covered),
             *_ticket_lines(card, covered), _chat_line(card, covered)]
    if not card.has_baseline:
        lines.append(f"- {NO_BASELINE}")
    lines.append("Signals:")
    lines += [f"- {signal_text(s)} Evidence: {_links(s.evidence)}" for s in card.signals] or [f"- {NO_SIGNALS}"]
    return "\n".join(lines)


def _code_lines(card: Card, covered: frozenset[str]) -> list[str]:
    if "code" not in covered:
        return [f"- Pull requests, reviews and commits: {NOT_AVAILABLE.format(source='code host')}"]
    lines = [f"- Pull requests opened: {card.prs_w} in the window (usual {_num(card.prs_b_week)}/week)",
             (f"- Reviews given: {card.reviews_w} in the window (usual {_num(card.reviews_b_week)}/week); share of "
              f"team reviews {_pct(card.share_w)} (their usual {_pct(card.share_b)})"),
             f"- Review turnaround, median: {_hours(card.turnaround_w)} (their usual {_hours(card.turnaround_b)})"]
    if card.open_requests:
        lines.append("- Review requests open over 48 h: " +
                     ", ".join(f"{_link(u)} ({int(h)} h)" for u, h in card.open_requests))
    ah = NO_TZ if card.after_hours is None else (
        f"{_pct(card.after_hours[0])} outside working hours ({card.tz_name}), on {card.after_hours[2]} days")
    lines.append(f"- Commits: {card.commits_w} in the window (usual {_num(card.commits_b_week)}/week); {ah}")
    return lines


def _ticket_lines(card: Card, covered: frozenset[str]) -> list[str]:
    if "tracker" not in covered:
        return [f"- In-progress tickets: {NOT_AVAILABLE.format(source='tracker')}"]
    if not card.tickets:
        return ["- In-progress tickets: none found"]
    return ["- In-progress tickets: " + "; ".join(
        f"{clean_label(i.key)} (\"{clean_label(i.status)}\") {days} days in status, last activity {silent} days ago, "
        f"{_link(i.url)}" for i, days, silent in card.tickets)]


def _chat_line(card: Card, covered: frozenset[str]) -> str:
    if "chat" not in covered:
        return f"- Chat messages: {NOT_AVAILABLE.format(source='chat')}"
    zone = f"local, {card.tz_name}" if card.tz_name else "UTC, no time zone known"
    hours = ", ".join(f"{k}: {v}" for k, v in card.chat_hours) or "none"
    total = f"; {card.chat_total} reported by the tool for the 6 weeks" if card.chat_sampled and card.chat_total else ""
    return (f"- Chat messages (counted, never read): {card.chat_w} in the window (usual {_num(card.chat_b_week)}/week); "
            f"{_num(card.chat_per_day[0])} per working day (usual {_num(card.chat_per_day[1])}); by hour ({zone}): "
            f"{hours}{total}")


def signal_text(signal: Signal) -> str:
    v = signal.values
    values = {
        "review_concentrated": lambda: {"person": clean_label(signal.person or ""), "pct": _int_pct(v["share"]),
                                        "total": v["total"]},
        "turnaround_up": lambda: {"w": _num(v["w"]), "b": _num(v["b"]), "n": v["n"]},
        "stuck_ticket": lambda: {"key": clean_label(v["key"]), "status": clean_label(v["status"]), "days": v["days"]},
        "quiet": lambda: {"last7": v["last7"], "base": _num(v["base"])},
        "after_hours_commits": lambda: {"w": _int_pct(v["w"]), "days": v["days"],
                                        "b": f"usual {_int_pct(v['b'])}%" if v["b"] is not None
                                        else "no earlier commits to compare"},
    }[signal.kind]()
    return SIGNAL_TEXT[signal.kind].format(**values)


def _has_activity(card: Card) -> bool:
    return bool(card.prs_w or card.prs_b_week or card.reviews_w or card.reviews_b_week or card.commits_w
                or card.commits_b_week or card.tickets or card.chat_w or card.chat_b_week)


def _links(urls: Sequence[str]) -> str:
    return ", ".join(_link(u) for u in urls)


def _link(url: str) -> str:
    return url if _URL.match(url or "") else clean_label(url)


def _bar(share: float | None) -> str:
    filled = round((share or 0) * BAR_WIDTH)
    return "#" * filled + "." * (BAR_WIDTH - filled)


def _pct(share: float | None) -> str:
    return "n/a" if share is None else f"{_int_pct(share)}%"


def _int_pct(share: float) -> int:
    return round(share * 100)


def _num(value: float) -> str:
    return f"{value:.1f}"


def _hours(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} h"
