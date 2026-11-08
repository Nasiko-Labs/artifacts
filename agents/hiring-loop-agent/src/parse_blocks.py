"""Pure: split pasted feedback into one block per interviewer, in code.

A block starts at a header line:
    Interviewer: Dana Smith (System design) - Lean no      `Interviewer:` / `From:` / `Reviewer:` / `Panelist:`
    Dana Smith (System design, Lean no):                   a line that is just a name followed by a colon
    ---                                                    a separator; the next line names the interviewer
Inside a block, `Interview:` / `Round:` give the interview and `Rating:` / `Score:` / `Recommendation:` /
`Overall:` give the rating when it is one the rating table knows. Text before the first header is the
user's request. An envelope from an earlier step ({"candidate", "feedback": [...]}) is accepted too.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ratings import is_rating
from records import Block

_NAME = r"[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*){0,3}"
_LABELLED = re.compile(r"^\s*(?:interviewer|from|reviewer|panelist)\s*[:\-–]\s*(?P<name>[^(,\n]+?)\s*"
                       r"(?:\((?P<paren>[^)]*)\))?\s*(?:[,\-–]\s*(?P<rest>.*))?$", re.IGNORECASE)
_NAME_COLON = re.compile(rf"^\s*(?P<name>{_NAME})\s*(?:\((?P<paren>[^)]*)\))?\s*:\s*(?P<rest>.*)$")
_NAME_ONLY = re.compile(rf"^\s*(?P<name>{_NAME})\s*(?:\((?P<paren>[^)]*)\))?\s*(?:[\-–,]\s*(?P<rest>.*))?$")
_SEPARATOR = re.compile(r"^\s*(?:-{3,}|={3,}|\*{3,}|_{3,})\s*$")
_INTERVIEW_FIELD = re.compile(r"^\s*(?:interview(?:\s+type)?|round|focus)\s*:\s*(?P<value>.+)$", re.IGNORECASE)
_RATING_FIELD = re.compile(r"^\s*(?:rating|score|recommendation|overall(?:\s+rating)?|verdict|decision)\s*:\s*"
                           r"(?P<value>.+)$", re.IGNORECASE)
_LEADING_RATING = re.compile(r"^(?P<rating>[^.;,–\-]{1,30})(?:[.;,–\-]|$)")
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
# First words that make a "Word:" line a field or a competency, never an interviewer's name.
NOT_NAMES = frozenset({
    "interview", "interviewer", "round", "focus", "rating", "score", "recommendation", "overall", "verdict",
    "decision", "notes", "note", "feedback", "comments", "comment", "strengths", "strength", "concerns", "concern",
    "summary", "candidate", "role", "position", "date", "competencies", "competency", "expected", "panel",
    "evidence", "coding", "code", "communication", "architecture", "design", "system", "debugging", "ownership",
    "leadership", "collaboration", "testing", "problem", "technical", "behavioral", "behavioural", "culture",
    "values", "motivation", "domain", "algorithms", "pros", "cons", "questions", "question", "answer", "follow",
    "debrief", "hi", "hello", "please", "thanks", "subject", "to", "cc", "sent", "jd", "level", "team", "pair",
})


@dataclass(frozen=True)
class Parsed:
    instruction: str
    blocks: tuple[Block, ...]
    candidate: str = ""           # from an envelope only


def parse(text: str) -> Parsed:
    envelope = _envelope(text)
    if envelope is not None:
        return envelope
    lines = (text or "").split("\n")
    instruction: list[str] = []
    blocks: list[dict] = []
    current: dict | None = None
    after_separator = False
    for line in lines:
        if _SEPARATOR.match(line):
            current, after_separator = None, True
            continue
        header = _header(line, after_separator)
        if header is not None:
            current = header
            blocks.append(current)
            after_separator = False
            continue
        if line.strip():
            after_separator = False
        if current is None:
            if not blocks:
                instruction.append(line)
            continue
        _field(current, line)
        current["lines"].append(line)
    built = tuple(b for b in (_block(raw) for raw in blocks) if b is not None)
    return Parsed("\n".join(instruction).strip() if built else (text or "").strip(), built)


def _header(line: str, after_separator: bool) -> dict | None:
    found = _LABELLED.match(line)
    if found is None:
        found = _NAME_COLON.match(line)
        if found is not None and _is_field_word(found.group("name")):
            found = None
    if found is None and after_separator:
        found = _NAME_ONLY.match(line)
        if found is not None and _is_field_word(found.group("name")):
            found = None
    if found is None:
        return None
    name = " ".join(found.group("name").split()).strip(" .")
    if not name or len(name) > 60:
        return None
    raw = {"interviewer": name, "interview": "", "rating_text": "", "lines": []}
    for part in (found.group("paren") or "").split(","):
        _classify_part(raw, part.strip())
    rest = (found.group("rest") or "").strip()
    if rest:
        lead = _LEADING_RATING.match(rest)
        if lead and is_rating(lead.group("rating").strip()) and not raw["rating_text"]:
            raw["rating_text"] = lead.group("rating").strip()
        raw["lines"].append(rest)
    return raw


def _classify_part(raw: dict, part: str) -> None:
    if not part:
        return
    if is_rating(part) and not raw["rating_text"]:
        raw["rating_text"] = part
    elif not raw["interview"]:
        raw["interview"] = part


def _field(raw: dict, line: str) -> None:
    interview = _INTERVIEW_FIELD.match(line)
    if interview and not raw["interview"]:
        raw["interview"] = interview.group("value").strip()
        return
    rating = _RATING_FIELD.match(line)
    if rating and not raw["rating_text"] and is_rating(rating.group("value")):
        raw["rating_text"] = rating.group("value").strip().rstrip(".")


def _is_field_word(name: str) -> bool:
    return name.split()[0].casefold().strip(".") in NOT_NAMES


def _block(raw: dict) -> Block | None:
    text = "\n".join(raw["lines"]).strip()
    if not text and not raw["rating_text"]:
        return None
    return Block(interviewer=raw["interviewer"], text=text, interview=raw["interview"], rating_text=raw["rating_text"])


def _envelope(text: str) -> Parsed | None:
    body = _JSON_FENCE.sub("", (text or "").strip())
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("feedback"), list):
        return None
    blocks = []
    for item in data["feedback"]:
        if not isinstance(item, dict) or not isinstance(item.get("interviewer"), str):
            continue
        text_value = item.get("text") if isinstance(item.get("text"), str) else ""
        rating = item.get("rating_text") if isinstance(item.get("rating_text"), str) else ""
        if not item["interviewer"].strip() or not (text_value.strip() or rating.strip()):
            continue
        interview = item.get("interview") if isinstance(item.get("interview"), str) else ""
        blocks.append(Block(interviewer=item["interviewer"].strip(), text=text_value.strip(), interview=interview.strip(),
                            rating_text=rating.strip(), source="envelope"))
    candidate = data.get("candidate") if isinstance(data.get("candidate"), str) else ""
    return Parsed("", tuple(blocks), candidate.strip())
