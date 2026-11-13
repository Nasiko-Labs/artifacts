"""Pure: read the user's brief into a decision, options, constraints, data, criteria, a stated
preference, an audience and a decide-by date. Everything is parsed in code; the model never parses.

Labelled sections (`Decision:`, `Options:`, `Constraints:`, `Data:`/`Facts:`, `Criteria:`,
`Context:`, `Preference:`, `Audience:`, `Decision needed by:`) may sit on their own lines or inline.
Text before the first label is the request; its first sentence is the decision unless one is labelled."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import criteria as crit
import options as opts
from options import Option
from text import strip_markers

_LABELS = {"decision needed by": "needed_by", "decide by": "needed_by", "decision": "decision",
           "options": "options", "choices": "options", "alternatives": "options", "constraints": "constraints",
           "constraint": "constraints", "data": "data", "facts": "data", "context": "data", "background": "data",
           "criteria": "criteria", "preference": "preference", "audience": "audience"}
_LABEL = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _LABELS), key=len, reverse=True))
                    + r")\s*:", re.IGNORECASE)
_REQUEST_LEAD = re.compile(
    r"^(?:please\s+)?(?:(?:can you\s+)?(?:write|draft|make|create|prepare|give me)\s+)?(?:me\s+)?(?:a\s+|an\s+)?"
    r"(?:one[- ]page\s+)?(?:decision\s+)?(?:memo|doc|write-?up)?\s*(?::|\b)", re.IGNORECASE)
_FOR_AUDIENCE = re.compile(
    r"\bfor\s+(?:the\s+|my\s+|our\s+)?(board|vp|svp|ceo|cto|cfo|coo|cpo|execs?|exec(?:utive)? team|leadership(?: team)?|"
    r"manager|director|investors|stakeholders|team)\b(?:\s+(?:on|about|regarding)\b)?", re.IGNORECASE)
_PREFERENCE = re.compile(
    r"\b(?:lean(?:ing)?\s+(?:towards?|to)|prefer(?:ence)?(?:\s+is)?|(?:my|our)\s+(?:pick|choice)\s+is|"
    r"i(?:'d| would)\s+go\s+with)\s*:?\s*(?P<what>[^.,;\n]{1,60})", re.IGNORECASE)
_NEEDED_BY = re.compile(r"\b(?:decision\s+(?:is\s+)?needed\s+by|decide\s+by|need\s+(?:a|the)\s+decision\s+by)\s+"
                        r"(?P<when>[^.;\n]{2,40})", re.IGNORECASE)
_CONSTRAINT_WORDS = re.compile(r"\b(?:budget|deadline|headcount|engineers?|must|cannot|can't|no more than|at most|"
                               r"limit|freeze|by (?:end|eoq|eoy|q[1-4]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))",
                               re.IGNORECASE)
_NEXT_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_OPTION_START = re.compile(r"\(?(?:option\s+)?[A-E1-9]\)|option\s", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.?!])\s+")


@dataclass(frozen=True)
class Brief:
    decision: str
    options: tuple[Option, ...]
    constraints: tuple[str, ...] = ()
    data: tuple[str, ...] = ()
    criteria: tuple[crit.Criterion, ...] = ()
    preference: str = ""
    audience: str = ""
    needed_by: str = ""
    raw: str = ""
    extra: tuple[str, ...] = field(default=())   # data added later ("add data: ...")

    @property
    def user_options(self) -> int:
        return sum(1 for o in self.options if not o.added_by_me)

    @property
    def grounding_text(self) -> str:
        return "\n".join([self.raw, *self.extra])


def to_dict(brief: Brief) -> dict:
    return asdict(brief)


def from_dict(data: dict | None) -> Brief | None:
    if not data:
        return None
    return Brief(decision=data["decision"], options=tuple(Option(**o) for o in data["options"]),
                 constraints=tuple(data.get("constraints") or ()), data=tuple(data.get("data") or ()),
                 criteria=tuple(crit.Criterion(**c) for c in data.get("criteria") or ()),
                 preference=data.get("preference") or "", audience=data.get("audience") or "",
                 needed_by=data.get("needed_by") or "", raw=data.get("raw") or "",
                 extra=tuple(data.get("extra") or ()))


def _sections(text: str) -> tuple[str, dict[str, str]]:
    found = list(_LABEL.finditer(text))
    head = text[:found[0].start()] if found else text
    sections: dict[str, str] = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        kind = _LABELS[m.group(1).casefold()]
        body = _cut(kind, text[m.end():end].strip())
        sections[kind] = (sections[kind] + "\n" + body).strip() if kind in sections else body
    return head.strip(), sections


def _cut(kind: str, body: str) -> str:
    """An inline (one-line) criteria or options body ends at the first sentence that is not part of it."""
    if kind not in ("criteria", "options") or "\n" in body:
        return body
    for m in _NEXT_SENTENCE.finditer(body):
        if not _OPTION_START.match(body[m.end():]):
            return body[:m.start()].strip()
    return body


def _decision(head: str, sections: dict[str, str]) -> tuple[str, list[str]]:
    """(decision sentence, the head's remaining lines)."""
    lines = [line for line in head.splitlines() if line.strip()]
    if "decision" in sections:
        return _tidy(sections["decision"].splitlines()[0]), lines
    if not lines:
        return "", []
    if opts.from_lines(lines[:1]):  # no decision sentence: the brief starts with its options
        return "", lines
    first = _SENTENCE.split(lines[0].strip(), maxsplit=1)
    rest = ([first[1]] if len(first) > 1 else []) + lines[1:]
    return _tidy(first[0]), rest


def _tidy(decision: str) -> str:
    text = _REQUEST_LEAD.sub("", decision.strip(), count=1).strip()
    text = _FOR_AUDIENCE.sub("", text, count=1).strip(" :,.")
    return text[:1].upper() + text[1:] if text else ""


def _lines(body: str) -> tuple[str, ...]:
    return tuple(" ".join(line.strip(" -*•\t").split()) for line in (body or "").splitlines() if line.strip(" -*•\t"))


def parse(text: str) -> Brief:
    raw = strip_markers(text or "").strip()
    head, sections = _sections(raw)
    decision, rest = _decision(head, sections)
    option_texts = opts.from_section(sections["options"]) if "options" in sections else opts.from_lines(rest)
    if not option_texts:
        option_texts = opts.from_decision(decision)
    loose = [line for line in rest if not opts.from_lines([line])]
    constraints = _lines(sections.get("constraints", "")) + tuple(
        line.strip() for line in loose if _CONSTRAINT_WORDS.search(line))
    data = _lines(sections.get("data", "")) + tuple(
        line.strip() for line in loose if re.search(r"\d", line) and not _CONSTRAINT_WORDS.search(line))
    options = opts.make(option_texts)
    preference = _preference(sections.get("preference", ""), raw, options)
    audience = sections.get("audience", "").strip() or _audience(raw)
    needed = sections.get("needed_by", "").splitlines()[0].strip() if sections.get("needed_by") else _needed_by(raw)
    return Brief(decision=decision or "[decision?]", options=options, constraints=constraints, data=data,
                 criteria=crit.parse(_without_preference(sections.get("criteria", ""))), preference=preference, audience=audience,
                 needed_by=needed, raw=raw)


def _without_preference(body: str) -> str:
    return "\n".join(line for line in body.splitlines() if not _PREFERENCE.search(line))


def _preference(section: str, raw: str, options: tuple[Option, ...]) -> str:
    if section.strip():
        key = opts.match(section.strip().splitlines()[0], options)
        if key:
            return key
    for m in _PREFERENCE.finditer(raw):
        key = opts.match(m.group("what"), options)
        if key:
            return key
    return ""


def _audience(text: str) -> str:
    m = _FOR_AUDIENCE.search(text)
    return m.group(1) if m else ""


def _needed_by(text: str) -> str:
    m = _NEEDED_BY.search(text)
    return m.group("when").strip() if m else ""


def with_options(brief: Brief, options: tuple[Option, ...]) -> Brief:
    return Brief(**{**_fields(brief), "options": options})


def _fields(brief: Brief) -> dict:
    return {k: getattr(brief, k) for k in brief.__dataclass_fields__}


def replace(brief: Brief, **changes) -> Brief:
    return Brief(**{**_fields(brief), **changes})
