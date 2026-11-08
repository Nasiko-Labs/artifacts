"""Pure: the markdown reply, rendered in code from checked records. No model prose: every bullet is a
template over tracker fields, calendar fields or verbatim chat quotes, and every link came from a tool.

    **Weekly leadership rollup, Oct 29 to Nov 5** (tracker, chat and calendar all read)
    **Platform**: 6 shipped, 4 in flight, 1 blocked, 2 due next week
    - Shipped: PLT-88 Rotate signing keys; ...
    **Reality check** (4 items to ask about; tickets can lag work)
    1. High. Platform, "SSO fix shipped" (Dana, Nov 3, link) vs PLT-77 ... In Review ...
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import coverage as coverage_
import prompts
import reality
from clean import MAX_CELL_CHARS, MAX_QUOTE_CHARS, clean, link
from dates import parse_day, short
from envelope import Run
from records import Issue
from rollup import TeamRollup

MAX_BULLETS = 8
MAX_UNMATCHED_SHOWN = 10


def report(run: Run, env: dict) -> str:
    parts = [_title(run), *(_team(r) for r in run.rollups)]
    parts.append(_next_week(run))
    parts.append(_reality(run, env))
    extras = [_previous(env), _unreported(env), _notes(run, env)]
    parts.extend(p for p in extras if p)
    parts.append(f"_{prompts.COPY_NOTE}_")
    return "\n\n".join(p for p in parts if p)


def as_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2, ensure_ascii=False) + "\n```"


def unverified(items: Sequence[dict], failed_lines: Sequence[str]) -> str:
    """Tracker missing: the fixed reply, the coverage lines, and what chat reported, marked unverified."""
    parts = [prompts.NO_TRACKER, *failed_lines]
    if items:
        lines = [f"- {_quoted(i['item'])}: reported {_quoted(i['reported_words'])}{_who(i)}" for i in items]
        parts.append("**Reported statuses (unverified: no tickets to check them against)**\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _title(run: Run) -> str:
    w = run.window
    return f"**Weekly leadership rollup, {short(w.start)} to {short(w.today)}** ({coverage_.heading(run.coverage)})"


def _team(r: TeamRollup) -> str:
    lines = [f"**{clean(r.team, MAX_CELL_CHARS)}**: {r.headline()}"]
    if r.shipped:
        lines.append("- Shipped: " + _issues(r.shipped))
    if r.in_flight:
        carry = f" ({r.carry_over} carried over from before the window)" if r.carry_over else ""
        lines.append("- In flight: " + _issues(r.in_flight) + carry)
    if r.blocked:
        lines.append("- Blocked: " + _issues(r.blocked, blockers=True))
    if r.next_week:
        lines.append("- Due next week: " + "; ".join(f"{_issue(i)} (due {short(i.due)})"
                                                      for i in r.next_week[:MAX_BULLETS]) + _more(r.next_week))
    if r.capped:
        lines.append(f"- {prompts.CAP_NOTE}")
    return "\n".join(lines)


def _issues(issues: Sequence[Issue], blockers: bool = False) -> str:
    shown = []
    for i in issues[:MAX_BULLETS]:
        by = ", ".join(clean(b.key, MAX_CELL_CHARS) for b in i.open_blockers) if blockers else ""
        shown.append(_issue(i) + (f" (blocked by {by})" if by else ""))
    return "; ".join(shown) + _more(issues)


def _issue(i: Issue) -> str:
    return f"{link(clean(i.key, MAX_CELL_CHARS), i.url)} {clean(i.title)}"


def _more(seq: Sequence) -> str:
    extra = len(seq) - MAX_BULLETS
    return f"; and {extra} more" if extra > 0 else ""


def _next_week(run: Run) -> str:
    entries = [(e.start, f"{short(e.start)} {_quoted(e.title)} ({link('calendar', e.url)})") for e in run.events]
    entries += [(i.due, f"{_issue(i)} due {short(i.due)}") for r in run.rollups for i in r.next_week]
    if not entries:
        return "**Next week**: nothing due and no launch, review or release events found."
    ordered = [text for _, text in sorted(entries, key=lambda e: e[0])]
    return "**Next week**: " + "; ".join(ordered[:MAX_BULLETS * 2])


def _reality(run: Run, env: dict) -> str:
    if run.check is None:
        return f"**Reality check**: {prompts.NO_CHAT_NOTE}"
    found = reality.display_order(run.check["disagreements"])
    head = (f"**Reality check** ({len(found)} item{'s' if len(found) != 1 else ''} to ask about; "
            f"tickets can lag work)\n_{prompts.FRAMING_LINE}_")
    lines = [f"{n}. {d['risk'].capitalize()}. {clean(d['team'], MAX_CELL_CHARS)}, {_quoted(d['reported_quote'])}"
             f"{_who(d)} vs {d['evidence']}." for n, d in enumerate(_with_quotes(found), start=1)]
    if not found:
        lines = ["No disagreements between what was reported and the tickets."]
    unmatched = run.check["unmatched"]
    if unmatched:
        names = ", ".join(_quoted(u["item"]) for u in unmatched[:MAX_UNMATCHED_SHOWN])
        more = f" and {len(unmatched) - MAX_UNMATCHED_SHOWN} more" if len(unmatched) > MAX_UNMATCHED_SHOWN else ""
        lines.append(f"Reported with no ticket (or no recognised status): {names}{more}.")
    if run.conflicts:
        lines.append(f"Conflicting reports: {len(run.conflicts)} earlier or other posts disagree with the latest "
                     "post on the same item (see the data part).")
    return head + "\n" + "\n".join(lines)


def _with_quotes(found: Sequence[dict]) -> list[dict]:
    """The quoted part: the status words in their post, as the post said them."""
    return [{**d, "reported_quote": d["quote"] if len(d["quote"]) <= 120 else d["reported"]} for d in found]


def _who(d: dict) -> str:
    bits = []
    if d.get("author"):
        bits.append(clean(d["author"], MAX_CELL_CHARS))
    day = parse_day(d.get("date"))
    if day:
        bits.append(short(day))
    if d.get("permalink"):
        bits.append(link("link", d["permalink"]))
    if d.get("source") == "paste":
        bits.append("pasted update")
    return f" ({', '.join(bits)})" if bits else ""


def _quoted(text: str) -> str:
    return f"\"{clean(text, MAX_QUOTE_CHARS)}\""


def _previous(env: dict) -> str:
    prev = env.get("previous")
    if not prev:
        return ""
    still = ", ".join(_quoted(i) for i in prev["still_open"]) or "none"
    resolved = ", ".join(_quoted(i) for i in prev["resolved"]) or "none"
    return f"**Since the previous rollup**: still open: {still}. Resolved: {resolved}."


def _unreported(env: dict) -> str:
    risks = env.get("unreported_risks")
    if risks is None:
        return ""
    if not risks:
        return "**Unreported risks**: no blocked or overdue tickets outside the reported items."
    return "**Unreported risks** (blocked or overdue, in no report)\n" + "\n".join(
        f"- {clean(r['team'], MAX_CELL_CHARS)}: {r['evidence']}" for r in risks)


def _notes(run: Run, env: dict) -> str:
    lines = list(coverage_.notes(run.coverage))
    lines += [prompts.STEP_LIMIT_NOTE.format(source=s) for s in run.partial]
    if run.skipped:
        lines.append(prompts.DEADLINE_NOTE.format(steps=", ".join(run.skipped)))
    lines += list(run.notes) + list(env["caveats"])
    if not lines:
        return ""
    return "**Notes**\n" + "\n".join(f"- {line}" for line in dict.fromkeys(lines))

