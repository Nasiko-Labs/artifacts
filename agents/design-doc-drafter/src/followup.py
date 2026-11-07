"""Pure: follow-ups on the stored draft, parsed in code.

    "as json"                                     -> re-render, zero calls
    "Q1: p95 under 2 seconds; Q2: 200 refunds/hour" -> answers: requirements updated, one REVISE call
    "add a requirement: ..."                      -> one extract call + one REVISE call
    anything else short                           -> one REVISE call (which may also say off_topic)
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


def unknown_ids(answers: Sequence[tuple[str, str]], open_questions: Sequence[dict]) -> list[str]:
    known = {q["id"] for q in open_questions}
    return [qid for qid, _ in answers if qid not in known]


def revise_instruction(answers: Sequence[tuple[str, str]]) -> str:
    listed = "; ".join(f"{qid}: {text}" for qid, text in answers)
    return (f"The user answered open questions ({listed}). Update only the sections these answers affect, "
            "replacing the matching [TBD] values with the given values.")
