"""Pure: the markdown a person reads and the fenced JSON a workflow step reads. Everything shown is
code-built; the only model text (reviewer questions) is cleaned of markdown before it is shown."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

FIX_FIRST = 5
MAX_PER_GROUP = 12
MAX_TEXT = 220
GROUPS = (("vague_requirement", "Vague requirements"), ("solution_in_requirement", "Solution detail in requirements"),
          ("unmeasurable_metric", "Metrics"), ("placeholder", "Placeholders"),
          ("unanswered_question", "Unowned open questions"))
VIEWS = {
    "blockers": lambda g: g["severity"] == "blocker",
    "metrics": lambda g: g["category"] == "unmeasurable_metric" or g["location"]["section"] == "Metrics",
    "requirements": lambda g: g["location"]["section"] == "Requirements",
    "sections": lambda g: g["category"] == "missing_section",
}
_UNSAFE = re.compile(r"[*_`#<>\[\]|]")


def clean(text: str) -> str:
    flat = " ".join(_UNSAFE.sub("", str(text or "")).split())
    return flat if len(flat) <= MAX_TEXT else flat[:MAX_TEXT - 3].rstrip() + "..."


def view(env: dict, kind: str) -> dict:
    """The envelope narrowed to one view (blockers, metrics, requirements, sections, questions)."""
    if kind == "questions":
        return {**env, "items": [], "gaps": []}
    keep = VIEWS.get(kind)
    gaps = [g for g in env["gaps"] if keep(g)] if keep else env["gaps"]
    return {**env, "items": gaps, "gaps": gaps}


def _where(g: dict) -> str:
    quote = g["location"]["quote"]
    return f'{g["location"]["section"]}: "{clean(quote)}"' if quote else g["location"]["section"]


def _gap_line(g: dict) -> str:
    return f'[{g["severity"]}] {_where(g)}: {clean(g["issue"])} Fix: {g["fix"]}'


def _groups(gaps: Sequence[dict], shown: Sequence[dict]) -> list[str]:
    """Every gap by kind; the ones already under Fix first are counted, not repeated."""
    out: list[str] = []
    missing = [g["location"]["section"] for g in gaps if g["category"] == "missing_section"]
    if missing:
        out += ["", f"**Missing or empty sections:** {', '.join(missing)}"]
    for category, title in GROUPS:
        every = [g for g in gaps if g["category"] == category]
        rows = [g for g in every if not any(g is s for s in shown)]
        if not rows:
            continue
        earlier = f", {len(every) - len(rows)} under Fix first" if len(every) > len(rows) else ""
        out += ["", f"**{title}** ({len(every)}{earlier})"]
        out += [f"- {_gap_line(g)}" for g in rows[:MAX_PER_GROUP]]
        if len(rows) > MAX_PER_GROUP:
            out.append(f"- _(and {len(rows) - MAX_PER_GROUP} more; ask \"as json\" for all)_")
    return out


def _questions(env: dict) -> list[str]:
    out: list[str] = []
    if env["questions"]:
        out += ["", "**Questions a reviewer will ask**"]
        out += [f'{n}. "{clean(q["line"])}": {clean(q["question"])}' + (f" ({clean(q['why'])})" if q["why"] else "")
                for n, q in enumerate(env["questions"], start=1)]
    if env["prd_questions"]:
        out += ["", "**Questions the PRD already raises (unanswered)**"]
        out += [f"- {clean(q)}" for q in env["prd_questions"]]
    return out


def render(env: dict, kind: str = "", notes: Sequence[str] = ()) -> str:
    shown = view(env, kind) if kind else env
    gaps = shown["gaps"]
    head = env["summary"].split(": ", 1)[1] if ": " in env["summary"] else ""
    lines = [f"**PRD review: {env['score']}/100 ({env['band']})**  {head}"]
    if kind and kind != "questions":
        lines.append(f"_Showing {kind} only: {len(gaps)} of {len(env['gaps'])} gaps._")
    if kind != "questions":
        urgent = [g for g in gaps if g["severity"] in ("blocker", "major")][:FIX_FIRST]
        if urgent:
            lines += ["", "**Fix first**", *(f"{n}. {_gap_line(g)}" for n, g in enumerate(urgent, start=1))]
        lines += _groups(gaps, urgent)
        if not gaps:
            lines += ["", "No gaps of this kind." if kind else "No gaps found by the rubric."]
    if kind in ("", "questions"):
        lines += _questions(env)
    by = {"heading": "sections found by heading", "model": "sections located by the model",
          "mixed": "some sections located by the model"}[env["sections_by"]]
    coverage = (f"_(Reviewed {env['requirements_reviewed']} requirements; {by}; {env['mode']} mode; confidence "
                f"{env['confidence']}. Ask \"show score breakdown\", \"only blockers\" or \"as json\".)_")
    lines += ["", coverage]
    lines += [*env["notes"], *notes]
    if env["needs_human"]:
        lines.append("_A person should decide whether this PRD goes back to its author before review._")
    return "\n".join(lines)


def render_breakdown(env: dict) -> str:
    rows = [f"- -{d['points']}: {clean(d['reason'])}" if d["points"] > 0 else f"- +{-d['points']}: {clean(d['reason'])}"
            for d in env["deductions"]]
    total = sum(d["points"] for d in env["deductions"])
    floor = " (floored at 0)" if total > 100 else ""
    return "\n".join([f"**Score breakdown: 100 - {total} = {env['score']}{floor} ({env['band']})**",
                      *(rows or ["- No deductions."])])


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"
