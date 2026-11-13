"""Pure: the PRD as markdown, rendered in code. Fixed sections, no images, no HTML; the word cap is enforced
by leaving out the lowest-priority `could` requirements first, then proposed goals and non-goals."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from prd import Fact, Prd, Requirement, Statement

PROPOSED = " (proposed)"
PROPOSED_NOTE_SHARE = 0.4
EMPTY_PROBLEM = "[Problem statement not given in the brief]"
_HTML = re.compile(r"<[^>\n]{1,200}>")
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")


@dataclass(frozen=True)
class View:
    kind: str = "full"           # full | questions | sources
    cap: int = 900


def clean(text: str) -> str:
    """No HTML, no images. Links are kept: grounding already dropped any URL the brief does not contain."""
    return _HTML.sub("", _IMAGE.sub("", text or "")).strip()


def _cell(text: str) -> str:
    return clean(text).replace("|", "/").replace("\n", " ")


def _statement(s: Statement, facts: dict[str, Fact], sources: bool) -> str:
    line = f"- {clean(s.text)}{PROPOSED if s.proposed else ''}"
    return line + _sources(s.basis, facts) if sources else line


def _sources(basis: Sequence[str], facts: dict[str, Fact]) -> str:
    quotes = [f'  > "{clean(facts[b].snippet)}"' for b in basis if b in facts]
    return ("\n" + "\n".join(quotes)) if quotes else ""


def _req_text(r: Requirement) -> str:
    text = clean(r.text)
    if r.vague:
        text += " [vague: define]"
    if r.from_solution:
        text += " (from brief: proposed solution)"
    return text + (PROPOSED if r.proposed else "")


def _sections(prd: Prd, facts: dict[str, Fact], sources: bool) -> list[str]:
    def block(title: str, entries: Sequence[Statement], empty: str) -> str:
        lines = [_statement(s, facts, sources) for s in entries] or [empty]
        return f"## {title}\n" + "\n".join(lines)

    out = [block("Problem", prd.problem, EMPTY_PROBLEM), block("Users", prd.users, "[Users not named in the brief]"),
           block("Goals", prd.goals, "None stated"), block("Non-goals", prd.non_goals, "None stated")]
    reqs = ["## Requirements"]
    if prd.requirements:
        reqs += ["| ID | Requirement | Priority | Check |", "| --- | --- | --- | --- |"]
        reqs += [f"| {r.id} | {_cell(_req_text(r))} | {r.priority} | {_cell(r.acceptance_hint)} |"
                 for r in prd.requirements]
        if sources:
            reqs += [f"- {r.id}:{_sources(r.basis, facts) or ' (proposed, no source)'}" for r in prd.requirements]
    else:
        reqs.append("None yet")
    out.append("\n".join(reqs))
    mets = ["## Success metrics"]
    if prd.metrics:
        mets += ["| Metric | Baseline | Target | Window |", "| --- | --- | --- | --- |"]
        mets += [f"| {_cell(m.name + (PROPOSED if m.proposed else ''))} | {_cell(m.baseline)} | {_cell(m.target)} | "
                 f"{_cell(m.window)} |" for m in prd.metrics]
    else:
        mets.append("None stated")
    out.append("\n".join(mets))
    out.append(questions_block(prd))
    return out


def questions_block(prd: Prd) -> str:
    lines = [f"{n}. {'(blocking) ' if q.blocking else ''}{clean(q.question)}"
             for n, q in enumerate(prd.open_questions, start=1)]
    return "## Open questions\n" + ("\n".join(lines) if lines else "None")


def _compose(prd: Prd, facts: dict[str, Fact], sources: bool, notes: Sequence[str]) -> str:
    body = "\n\n".join(_sections(prd, facts, sources))
    words = len(body.split())
    head = (f"# PRD: {clean(prd.title)}\n_(draft, {words} words; items marked (proposed) are mine, not from your "
            "brief)_")
    tail = [f"_{n}_" for n in notes if n]
    return "\n\n".join([head, body, *tail])


def fit(prd: Prd, cap: int) -> tuple[Prd, int]:
    """(the PRD trimmed to the word cap, how many entries were left out for it)."""
    current, left_out = prd, 0
    while len(_compose(current, {}, False, ()).split()) > cap:
        reqs = list(current.requirements)
        coulds = [r for r in reqs if r.priority == "could"]
        if coulds:
            reqs.remove(coulds[-1])
            current = current.with_(requirements=tuple(reqs))
        elif any(s.proposed for s in (*current.goals, *current.non_goals)):
            name = "non_goals" if any(s.proposed for s in current.non_goals) else "goals"
            entries = list(getattr(current, name))
            entries.remove([s for s in entries if s.proposed][-1])
            current = current.with_(**{name: tuple(entries)})
        else:
            break
        left_out += 1
    return current, left_out


DEFAULT_VIEW = View()


def render(prd: Prd, facts: Sequence[Fact] = (), view: View = DEFAULT_VIEW, dropped: int = 0,
           notes: Sequence[str] = ()) -> str:
    by_id = {f.id: f for f in facts}
    if view.kind == "questions":
        return f"# PRD: {clean(prd.title)}: open questions\n\n{questions_block(prd)}"
    shown, left_out = fit(prd, view.cap) if view.kind == "full" else (prd, 0)
    extra = list(notes)
    if dropped:
        extra.append(f"({dropped} statement{'s' if dropped != 1 else ''} left out: no support in your brief.)")
    if left_out:
        extra.append(f"({left_out} lower-priority item{'s' if left_out != 1 else ''} left out to fit "
                     f"{view.cap} words; ask for 'detailed' to see everything.)")
    if prd.proposed_share() > PROPOSED_NOTE_SHARE:
        extra.append("Much of this is my proposal; confirm it before you share it.")
    return _compose(shown, by_id, view.kind == "sources", extra)


def render_facts(facts: Sequence[Fact], note: str) -> str:
    lines = [f"- [{f.kind}] {clean(f.text)}" for f in facts] or ["- (none)"]
    return f"{note}\n\n## What I could extract\n" + "\n".join(lines)
