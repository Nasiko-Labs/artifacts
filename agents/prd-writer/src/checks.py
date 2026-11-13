"""Pure: the PRD's content rules, enforced in code after every model answer.

- Every number, date, email, link and name in a statement must be in the brief (or the user's answers):
  a failing statement is dropped; in a goal the invented number becomes [target?].
- Problem rests on >= 1 problem fact, else the section is empty (a gap question follows).
- `must` needs a cue word (required, must, blocker) in its basis snippets, else it is `should`.
- Vague requirements are flagged; metric values not in the brief become placeholders.
`problems` lists what one repair call may fix; the caller decides whether to spend it."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from grounding import normalise, source_form, tokens, ungrounded
from metric_checks import checked_metrics
from prd import MAX_PROPOSED_NON_GOALS, TARGET_PH, Fact, Prd, Requirement, Statement

_MUST_CUE = re.compile(r"\b(?:required|requires?|requirement|must|blockers?|blocking|mandatory)\b", re.IGNORECASE)
_VAGUE = re.compile(r"\b(?:fast|quick(?:ly)?|easy|easily|intuitive|seamless(?:ly)?|robust|scalable|"
                    r"user-friendly|etc\.?|as needed|appropriate(?:ly)?)(?![\w-])", re.IGNORECASE)
_DIGIT = re.compile(r"\d")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
NO_HINT = "[check?]"


@dataclass(frozen=True)
class Checked:
    prd: Prd
    problems: tuple[str, ...] = ()
    dropped: int = 0


class _Ctx:
    def __init__(self, facts: Sequence[Fact], corpus: str):
        self.norm = normalise(corpus)
        self.sentences = tuple(t for t in _SENTENCE_BREAK.split(corpus or "") if t.strip())
        self.kinds = {f.id: f.kind for f in facts}
        self.snippets = {f.id: f.snippet for f in facts}
        self.metric_facts = tuple(f for f in facts if f.kind in ("metric", "goal"))  # goals: only with a target


def check(prd: Prd, facts: Sequence[Fact], corpus: str) -> Checked:
    ctx = _Ctx(facts, corpus)
    problems: list[str] = []
    dropped = 0
    sections: dict = {}
    for name in ("problem", "users", "non_goals"):
        kept, lost = _grounded(getattr(prd, name), name, ctx, problems)
        sections[name], dropped = kept, dropped + lost
    sections["goals"] = tuple(_goal(s, ctx) for s in prd.goals)
    if not any(ctx.kinds.get(b) == "problem" for s in sections["problem"] for b in s.basis):
        if sections["problem"] and any(k == "problem" for k in ctx.kinds.values()):
            problems.append("problem: every statement must list a problem fact in its basis")
        dropped += len(sections["problem"])
        sections["problem"] = ()
    proposed = [s for s in sections["non_goals"] if s.proposed][MAX_PROPOSED_NON_GOALS:]
    sections["non_goals"] = tuple(s for s in sections["non_goals"] if s not in proposed)
    reqs, lost = _requirements(prd.requirements, ctx, problems)
    mets, lost_m = checked_metrics(prd.metrics, ctx)
    questions = tuple(q for q in prd.open_questions if not ungrounded(q.question, ctx.norm))
    dropped += lost + lost_m + len(prd.open_questions) - len(questions)
    if not reqs:
        problems.append("requirements: the section is missing; add the behaviors the facts call for")
    checked = prd.with_(requirements=reqs, metrics=mets, open_questions=questions, **sections)
    return Checked(checked, tuple(problems), dropped)


def _grounded(entries: Sequence[Statement], name: str, ctx: _Ctx, problems: list[str]) -> tuple[tuple, int]:
    kept = []
    for s in entries:
        missing = ungrounded(s.text, ctx.norm)
        if missing:
            problems.append(f"{name}: '{s.text}' uses {', '.join(missing)}, which the brief does not contain")
        else:
            kept.append(s)
    return tuple(kept), len(entries) - len(kept)


def _goal(s: Statement, ctx: _Ctx) -> Statement:
    """A goal keeps its wording; an invented number or date in it becomes [target?]."""
    text = s.text
    for token in tokens(text):
        spelled = source_form(token, ctx.norm)  # "30 percent" -> the brief's "30%"
        text = text.replace(token, spelled if spelled is not None else TARGET_PH, 1)
    for name in ungrounded(text, ctx.norm):
        text = text.replace(name, "[ ? ]", 1)
    return replace(s, text=text)


def _requirements(reqs: Sequence[Requirement], ctx: _Ctx, problems: list[str]) -> tuple[tuple, int]:
    kept = []
    for r in reqs:
        missing = ungrounded(r.text, ctx.norm)
        if missing:
            problems.append(f"requirement {r.id}: uses {', '.join(missing)}, which the brief does not contain")
            continue
        if not r.priority:
            problems.append(f"requirement {r.id}: has no priority (must, should or could)")
        priority = r.priority or "should"
        if priority == "must" and not any(_MUST_CUE.search(ctx.snippets.get(b, "")) for b in r.basis):
            priority = "should"
        vague = bool(_VAGUE.search(r.text)) and not _DIGIT.search(r.text)
        if vague:
            problems.append(f"requirement {r.id}: '{r.text}' is vague; make it one measurable behavior")
        hint = r.acceptance_hint if r.acceptance_hint and not ungrounded(r.acceptance_hint, ctx.norm) else NO_HINT
        solution = any(ctx.kinds.get(b) == "solution_idea" for b in r.basis)
        kept.append(replace(r, priority=priority, vague=vague, acceptance_hint=hint, from_solution=solution))
    return tuple(kept), len(reqs) - len(kept)
