"""Pure: the PRD the user last saw, read back from our own rendered reply in the chat history.

`render.render` writes fixed sections; this reads them back into a `Prd`: statements (`- text`, with
` (proposed)` marking an entry with no basis), the requirements and metrics tables, the numbered open
questions. Source quotes, the sources list and our notes are skipped. The fact ids an entry rested on are not
in the markdown, so a supported entry gets `SHOWN_BASIS`, which keeps it unmarked when rendered again. A reply
that lacks any section (the open-questions view, or a reply the history cut short) is not a full PRD: None.
A JSON reply is read through its `prd_markdown`."""

from __future__ import annotations

import json
import re

from prd import Metric, Prd, Question, Requirement, Statement, req_number
from render import EMPTY_PROBLEM, PROPOSED

SHOWN_BASIS = ("shown",)
DRAFT_MARK = "# PRD: "
VAGUE_TAG, SOLUTION_TAG = " [vague: define]", " (from brief: proposed solution)"
HEADINGS = {"Problem": "problem", "Users": "users", "Goals": "goals", "Non-goals": "non_goals",
            "Requirements": "requirements", "Success metrics": "metrics", "Open questions": "open_questions"}
EMPTY_LINES = frozenset({EMPTY_PROBLEM, "[Users not named in the brief]", "None stated", "None yet", "None"})
_QUESTION = re.compile(r"^\d{1,2}\.\s+(\(blocking\)\s+)?(.+)$")


def markdown_of(reply: str) -> str:
    """The rendered PRD in one of our replies (markdown, or the JSON envelope's `prd_markdown`), else ""."""
    text = (reply or "").strip()
    if text.startswith(DRAFT_MARK):
        return text
    if text.startswith("```json"):
        try:
            data = json.loads(text.strip("`").removeprefix("json"))
        except ValueError:
            return ""
        markdown = data.get("prd_markdown") if isinstance(data, dict) else None
        return markdown if isinstance(markdown, str) and markdown.startswith(DRAFT_MARK) else ""
    return ""


def read(markdown: str) -> Prd | None:
    lines = (markdown or "").splitlines()
    if not lines or not lines[0].startswith(DRAFT_MARK):
        return None
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines[1:]:
        if line.startswith("## "):
            current = HEADINGS.get(line[3:].strip())
            if current:
                sections[current] = []
        elif current:
            sections[current].append(line.rstrip())
    if set(sections) != set(HEADINGS.values()):
        return None
    statements = {name: _statements(sections[name]) for name in ("problem", "users", "goals", "non_goals")}
    reqs = _requirements(sections["requirements"])
    return Prd(title=lines[0][len(DRAFT_MARK):].strip() or "Untitled", requirements=reqs,
               metrics=_metrics(sections["metrics"]), open_questions=_questions(sections["open_questions"]),
               next_req=max((req_number(r.id) for r in reqs), default=0) + 1, **statements)


def _unmark(text: str) -> tuple[str, bool]:
    """(the text without the proposed marker, whether it was proposed)."""
    return (text[:-len(PROPOSED)], True) if text.endswith(PROPOSED) else (text, False)


def _statements(lines: list[str]) -> tuple[Statement, ...]:
    out = []
    for line in lines:
        if not line.startswith("- ") or line[2:].strip() in EMPTY_LINES:
            continue
        text, proposed = _unmark(line[2:].strip())
        out.append(Statement(text, () if proposed else SHOWN_BASIS))
    return tuple(out)


def _cells(line: str) -> list[str] | None:
    if not line.startswith("|") or set(line) <= set("|- "):
        return None
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _requirements(lines: list[str]) -> tuple[Requirement, ...]:
    out = []
    for line in lines:
        cells = _cells(line)
        if not cells or len(cells) != 4 or not re.fullmatch(r"R\d{1,2}", cells[0]):
            continue
        text, proposed = _unmark(cells[1])
        solution = text.endswith(SOLUTION_TAG)
        text = text.removesuffix(SOLUTION_TAG)
        vague = text.endswith(VAGUE_TAG)
        out.append(Requirement(id=cells[0], text=text.removesuffix(VAGUE_TAG), priority=cells[2],
                               basis=() if proposed else SHOWN_BASIS, acceptance_hint=cells[3],
                               from_solution=solution, vague=vague))
    return tuple(out)


def _metrics(lines: list[str]) -> tuple[Metric, ...]:
    out = []
    for line in lines:
        cells = _cells(line)
        if not cells or len(cells) != 4 or cells == ["Metric", "Baseline", "Target", "Window"]:
            continue
        name, proposed = _unmark(cells[0])
        out.append(Metric(name=name, baseline=cells[1], target=cells[2], window=cells[3],
                          basis=() if proposed else SHOWN_BASIS))
    return tuple(out)


def _questions(lines: list[str]) -> tuple[Question, ...]:
    found = (_QUESTION.match(line.strip()) for line in lines)
    return tuple(Question(m.group(2).strip(), "general", bool(m.group(1))) for m in found if m)



def as_json(prd: Prd) -> str:
    """The PRD as the reviser reads it (CURRENT_PRD): the entries' words, ids and values, no fact ids."""
    data = {"title": prd.title,
            **{name: [s.text for s in getattr(prd, name)] for name in ("problem", "users", "goals", "non_goals")},
            "requirements": [{"id": r.id, "text": r.text, "priority": r.priority,
                              "acceptance_hint": r.acceptance_hint} for r in prd.requirements],
            "metrics": [{"name": m.name, "baseline": m.baseline, "target": m.target, "window": m.window}
                        for m in prd.metrics],
            "open_questions": [q.question for q in prd.open_questions]}
    return json.dumps(data, ensure_ascii=False, indent=1)
