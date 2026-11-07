"""Pure: the header lines and the evidence items of one paste.

Header lines (`Person:`, `Role:`, `Level:`, `Period:`, `Pronouns:`, `Competencies:`, `Length:`) may sit anywhere.
Evidence is bullets, numbered lines, paragraphs or one item per line. A source label (`Manager:`, `Peer (Alex):`,
`Self:`, `Customer:`, `Metric:`, `PR:`, `Kudos:`) sets `source`; unlabeled is `manager`. A date at the start or in
brackets is the item's date. Identical items are kept once. Ids are e1..en in order."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date

from dates import find_date

MAX_ITEMS = 150
MAX_CHARS = 60_000
SOURCES = ("manager", "peer", "self", "customer", "metric", "pr", "kudos")
HEADER_KEYS = ("person", "role", "level", "period", "pronouns", "competencies", "length")

_HEADER = re.compile(rf"^\s*({'|'.join(HEADER_KEYS)})\s*:\s*(.*)$", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*•‣▪]|\d{1,3}[.)])\s+")
_LABEL = re.compile(r"^(manager|peer|self|customer|metric|pr|kudos)\b\s*(?:\(([^)]{1,40})\))?\s*[:\-–]\s*",
                    re.IGNORECASE)
_LEAD_DATE = re.compile(r"^\s*[\[(]?\s*(20\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?|[A-Za-z]{3,9}\.? \d{1,2}(?:,? 20\d{2})?|"
                        r"\d{1,2} [A-Za-z]{3,9}(?:,? 20\d{2})?)\s*[\])]?\s*[:\-–]?\s*")
_BRACKET_DATE = re.compile(r"[\[(]\s*(20\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?)\s*[\])]")
_REQUEST = re.compile(r"\b(?:review|evidence|feedback|examples?|draft|write[- ]?up|appraisal)\b", re.IGNORECASE)
_FOR_NAME = re.compile(r"\b(?:review|appraisal|write[- ]?up) (?:for|of) ([A-Z][a-z]+)\b")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Evidence:
    id: str
    text: str
    source: str = "manager"
    author: str = ""
    date: str = ""                     # ISO, as written; "" when none
    competencies: tuple[str, ...] = ()
    valence: str = "neutral"
    specific: bool = False
    tagged: bool = False
    excluded: str = ""                 # reason it is left out, "" when usable

    def to_dict(self) -> dict:
        return {**asdict(self), "competencies": list(self.competencies)}

    @staticmethod
    def from_dict(data: dict) -> Evidence:
        return Evidence(**{**data, "competencies": tuple(data.get("competencies") or ())})

    def with_tags(self, **changes) -> Evidence:
        return replace(self, **changes)

    @property
    def day(self) -> date | None:
        return date.fromisoformat(self.date) if self.date else None


@dataclass(frozen=True)
class Paste:
    header: dict = field(default_factory=dict)
    instruction: str = ""
    items: tuple[Evidence, ...] = ()
    total: int = 0                     # items found before the 150 cap
    over_chars: bool = False


def parse(text: str, default_year: int, source: str = "", find_instruction: bool = True) -> Paste:
    header: dict[str, str] = {}
    body: list[str] = []
    for line in (text or "").splitlines():
        match = _HEADER.match(line)
        if match and match.group(2).strip():
            header[match.group(1).casefold()] = match.group(2).strip()
        else:
            body.append(line)
    instruction, body = _instruction(body) if find_instruction else ("", body)
    if "person" not in header and (m := _FOR_NAME.search(instruction)):
        header["person"] = m.group(1)
    over_chars = sum(len(line) for line in body) > MAX_CHARS
    blocks = _blocks(body)
    items = _items(blocks, default_year, source)
    return Paste(header, instruction, items[:MAX_ITEMS], len(items), over_chars)


def _instruction(lines: list[str]) -> tuple[str, list[str]]:
    """A first non-bullet line that asks for the review is the request, not evidence."""
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if not _BULLET.match(line) and not _LABEL.match(line.strip()) and not _dated(line) and _REQUEST.search(line) \
                and (line.rstrip().endswith(":") or len(line.split()) <= 25):
            return line.strip().rstrip(":"), lines[:i] + lines[i + 1:]
        return "", lines
    return "", lines


def _dated(line: str) -> bool:
    lead = _LEAD_DATE.match(line)
    return bool(lead and find_date(lead.group(1), 2000))


def _blocks(lines: list[str]) -> list[str]:
    if any(_BULLET.match(line) for line in lines):
        blocks: list[str] = []
        for line in lines:
            if _BULLET.match(line):
                blocks.append(_BULLET.sub("", line, count=1))
            elif line.strip() and blocks:
                blocks[-1] += " " + line.strip()
            elif line.strip():
                blocks.append(line.strip())
        return blocks
    paragraphs = [p for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    if len(paragraphs) == 1:
        return [line for line in paragraphs[0].splitlines() if line.strip()]
    return paragraphs


def _items(blocks: list[str], default_year: int, forced_source: str) -> list[Evidence]:
    seen: set[str] = set()
    items: list[Evidence] = []
    for raw in blocks:
        item = _item(_SPACES.sub(" ", raw).strip(), default_year, forced_source)
        key = item.text.casefold()
        if not item.text or key in seen:
            continue
        seen.add(key)
        items.append(replace(item, id=f"e{len(items) + 1}"))
    return items


def _item(text: str, default_year: int, forced_source: str) -> Evidence:
    rest, when = text, None
    lead = _LEAD_DATE.match(rest)
    if lead and (when := find_date(lead.group(1), default_year)):
        rest = rest[lead.end():]
    label = _LABEL.match(rest)
    source, author = forced_source or "manager", ""
    if label:
        source, author = label.group(1).casefold(), (label.group(2) or "").strip()
        rest = rest[label.end():]
        if when is None and (lead := _LEAD_DATE.match(rest)) and (when := find_date(lead.group(1), default_year)):
            rest = rest[lead.end():]
    if when is None and (bracket := _BRACKET_DATE.search(rest)):
        when = find_date(bracket.group(1), default_year)
    return Evidence(id="", text=rest.strip(), source=source, author=author, date=when.isoformat() if when else "")


def looks_like_request(text: str) -> bool:
    return bool(_REQUEST.search(text or ""))


def from_envelope(text: str) -> str | None:
    """Another agent's envelope with `per_person[]` (Kudos Collector) as paste text: the person's examples
    become `Kudos:` items. None when `text` is not such an envelope."""
    body = (text or "").strip()
    fenced = re.match(r"\A```(?:json)?\s*(.*?)\s*```\Z", body, re.DOTALL)
    body = fenced.group(1) if fenced else body
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    people = data.get("per_person") if isinstance(data, dict) else None
    if not isinstance(people, list) or not people or not isinstance(people[0], dict):
        return None
    person = people[0]
    lines = [f"Person: {person['person']}"] if isinstance(person.get("person"), str) else []
    for example in person.get("examples") or ():
        if isinstance(example, str):
            lines.append(f"- Kudos: {example}")
        elif isinstance(example, dict) and isinstance(example.get("text"), str):
            when = f"[{example['date']}] " if isinstance(example.get("date"), str) else ""
            lines.append(f"- {when}Kudos: {example['text']}")
    return "\n".join(lines)
