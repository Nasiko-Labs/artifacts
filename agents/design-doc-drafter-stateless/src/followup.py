"""Pure: follow-ups on the stored draft, parsed in code.

Each one runs the drafting crew again (3 calls) on the recalled requirements, then:
    "as json"                                     -> the rebuilt draft as JSON
    "Q1: p95 under 2 seconds; Q2: 200 refunds/hour" -> answers in the crew's REQUEST; requirements noted, closed
    "add a requirement: ..."                      -> appended to the recalled requirements
    anything else short                           -> the request in the crew's REQUEST (may say off_topic)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the (?:result|doc|draft))? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_ANSWER = re.compile(r"(?:^|[\s;,.])Q(\d{1,3})\s*[:=\-]\s*", re.IGNORECASE)
_ADD = re.compile(r"^\W*(?:add|new|also|another)(?: an?| the| these| this)?(?: new)? (?:requirements?|reqs?)"
                  r"\s*[:\-]?\s*(.+)$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Command:
    kind: str                                   # json | answers | add | revise
    answers: tuple[tuple[str, str], ...] = ()   # (Qn, answer text)
    text: str = ""


def parse(text: str) -> Command:
    message = (text or "").strip()
    if _JSON.match(message):
        return Command("json")
    answers = parse_answers(message)
    if answers:
        return Command("answers", answers=answers)
    added = _ADD.match(message)
    if added and added.group(1).strip():
        return Command("add", text=added.group(1).strip())
    return Command("revise", text=message)


def parse_answers(text: str) -> tuple[tuple[str, str], ...]:
    """Every `Qn: answer` in the message; the answer runs to the next `Qm:` (separators trimmed)."""
    marks = list(_ANSWER.finditer(text or ""))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        answer = text[mark.end():end].strip(" \t\n;,.")
        if answer:
            out.append((f"Q{int(mark.group(1))}", " ".join(answer.split())))
    return tuple(out)


def apply_answers(doc: dict, answers: Sequence[tuple[str, str]], open_questions: Sequence[dict]) -> dict:
    """The draft with the answers recorded: the requirements each question blocks carry the answer, the
    answer becomes grounding text, and the question's key is closed."""
    by_id = {q["id"]: q for q in open_questions}
    notes = {}
    lines = list(doc.get("answers") or ())
    closed = list(doc.get("answered") or ())
    for qid, answer in answers:
        question = by_id[qid]
        lines.append(f"{qid} ({question['text']}): {answer}")
        closed.append(question["key"])
        for rid in question["blocks"]:
            notes.setdefault(rid, []).append(f"{qid}: {answer}")
    reqs = [{**r, "text": r["text"] + "".join(f" ({n})" for n in notes.get(r["id"], ()))} for r in doc["reqs"]]
    return {**doc, "reqs": reqs, "answers": lines, "answered": closed}


def seed_numbers(questions: Sequence[dict], recalled: Sequence[tuple[str, int]]) -> dict:
    """A key -> number map that gives a rebuilt question the number the user saw for the same text; numbers of
    recalled questions that are gone stay reserved, so a new question never takes one."""
    by_text = dict(recalled)
    seeded = {q["key"]: by_text[q["text"]] for q in questions if q["text"] in by_text}
    taken = set(seeded.values())
    return {**seeded, **{f"recalled:{n}": n for _, n in recalled if n not in taken}}


def record_answers(doc: dict, answers: Sequence[tuple[str, str]], open_questions: Sequence[dict]) -> dict:
    """`apply_answers` for the questions still open; an answer to a recalled question the rebuilt draft no
    longer raises is kept as grounding text only."""
    open_ids = {q["id"] for q in open_questions}
    known = [a for a in answers if a[0] in open_ids]
    gone = [f"{qid}: {text}" for qid, text in answers if qid not in open_ids]
    applied = apply_answers(doc, known, open_questions)
    return {**applied, "answers": [*applied["answers"], *gone]}


def unknown_ids(answers: Sequence[tuple[str, str]], open_questions: Sequence[dict]) -> list[str]:
    known = {q["id"] for q in open_questions}
    return [qid for qid, _ in answers if qid not in known]


def revise_instruction(answers: Sequence[tuple[str, str]]) -> str:
    listed = "; ".join(f"{qid}: {text}" for qid, text in answers)
    return (f"The user answered open questions ({listed}). Update only the sections these answers affect, "
            "replacing the matching [TBD] values with the given values.")
