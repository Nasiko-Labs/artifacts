"""Pure: the markdown reply, rendered by code from the computed rows. Task text and model text are cleaned
first: no HTML, no images, no link targets, and table pipes escaped."""

from __future__ import annotations

import json
from collections.abc import Sequence

import scoring
from checks import plain
from envelope import close_call
from ranking import Row, quick_wins

FOOTER = '_Say "urgency first", "impact first", "move 3 up", "show the numbers"._'


def cell(text: str) -> str:
    return plain(text).replace("|", "\\|")


def _title(preset: str, goals: Sequence[str], shown: int, total: int) -> str:
    head = "Ranked" if shown == total else f"Top {shown} of {total}"
    goal_text = f" goals: {cell('; '.join(goals))}" if goals else ""
    return f"**{head} (weights: {scoring.label(preset)})**{goal_text}"


def _row(row: Row) -> str:
    marks = []
    if row.pinned:
        marks.append("(pinned by you)")
    if row.action == "drop":
        marks.append("Drop?")
    elif row.action == "delegate":
        marks.append(f"Delegate to {cell(row.delegate_to or 'someone else')}?")
    return f"| {row.rank} | {cell(row.text)} | {row.score} | {cell(' '.join([*marks, row.reason]))} |"


def _action_line(row: Row) -> str:
    target = f" to {row.delegate_to}" if row.action == "delegate" and row.delegate_to else ""
    return f"{cell(row.text)} ({row.action}{target}: {row.action_reason})"


def render(rows: Sequence[Row], env: dict, preset: str, goals: Sequence[str], notes: Sequence[str] = (),
           top: int | None = None) -> str:
    shown = list(rows[:top]) if top else list(rows)
    table = ["| # | Task | Score | Why |", "|---|---|---|---|", *(_row(r) for r in shown)]
    blocks = [_title(preset, goals, len(shown), len(rows)) + "\n" + "\n".join(table)]
    wins = quick_wins(shown)
    actions = [r for r in rows if r.action]
    extras = []
    if wins:
        extras.append("Quick wins: " + ", ".join(f"#{r.rank}" for r in wins))
    if actions:
        extras.append("**Drop or delegate:** " + "; ".join(_action_line(r) for r in actions))
    if extras:
        blocks.append("   ".join(extras))
    blocks += _flags(rows, env)
    blocks += [n for n in notes if n]
    blocks.append(FOOTER)
    return "\n\n".join(blocks)


def _flags(rows: Sequence[Row], env: dict) -> list[str]:
    flags = []
    pair = close_call(rows)
    if pair:
        flags.append(f"_It's a close call between #{pair[0].rank} and #{pair[1].rank} ({pair[0].score} vs "
                     f"{pair[1].score}): you choose the order._")
    if env.get("confidence") == "low":
        flags.append("_Confidence is low: most tasks have no deadline or estimate and no goals were given, or a "
                     "rating call failed. Add goals or dates and I'll re-rank._")
    return flags


def render_actions(rows: Sequence[Row]) -> str:
    actions = [r for r in rows if r.action]
    if not actions:
        return "Nothing in this list meets my drop or delegate rule.\n\n" + FOOTER
    return "**Drop or delegate**\n" + "\n".join(f"- #{r.rank} {_action_line(r)}" for r in actions)


def render_quick_wins(rows: Sequence[Row]) -> str:
    wins = quick_wins(rows)
    if not wins:
        return "No quick wins in this list (impact 3+ and effort 2 or less)."
    return "**Quick wins** (impact 3+, effort 2 or less)\n" + "\n".join(
        f"- #{r.rank} {cell(r.text)} (impact {r.impact}, effort {r.effort})" for r in wins)


def render_numbers(rows: Sequence[Row], preset: str) -> str:
    head = ["| # | Task | Impact | Urgency | Effort | Due | Score |", "|---|---|---|---|---|---|---|"]
    body = [f"| {r.rank} | {cell(r.text)} | {r.impact} | {r.urgency} | {r.effort}{' (est.)' if r.effort_estimated else ''}"
            f" | {r.due.isoformat() if r.due else (r.due_phrase or '-')} | {r.score} |" for r in rows]
    bands = ("Urgency: overdue or 0-1 days = 5, 2-3 = 4, 4-7 = 3, 8-30 = 2, later or no date = 1; ASAP or a "
             "blocking word raises it to 4.")
    return "\n".join(["**The numbers**", *head, *body, "", scoring.formula(preset), bands])


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"
