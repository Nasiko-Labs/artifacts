"""Pure: code checks on what the model wrote, and safe text for the reply.

- `uses` ids must exist (unknown ones are ignored and counted as dropped).
- Every number must be one of the display strings (or sentence numbers) of the facts it cites, a number
  in the user's instruction, or part of a table label. A number that belongs to a fact the model forgot
  to cite adds that fact to `uses`; any other number fails the check.
- Every month the text names, and every quoted name, must exist in the table or the facts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cells import MONTH_INDEX
from display import number_tokens, numbers_in
from fact import Fact, FactSet

MAX_TEXT_CHARS = 1500
_MONTH_WORD = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|"
                         r"December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\b")
_QUOTED = re.compile(r"[\"“]([^\"”]{2,60})[\"”]")
_TAG = re.compile(r"<[^>]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_URL = re.compile(r"\bhttps?://[^\s)>\]]+", re.IGNORECASE)


@dataclass(frozen=True)
class Checked:
    problems: tuple[str, ...]
    uses: tuple[str, ...]
    dropped: int = 0


def known_ids(raw: Any, facts: Mapping[str, Fact]) -> tuple[tuple[str, ...], int]:
    """Cited ids that exist, tolerating slips (2 → f2, case, spaces); and how many were unknown."""
    items = [raw] if isinstance(raw, str | int) else raw if isinstance(raw, list) else []
    kept, unknown = [], 0
    for item in items:
        text = str(item).strip().casefold()
        fid = f"f{text}" if text.isdigit() else text
        if fid in facts:
            kept.append(fid)
        else:
            unknown += 1
    return tuple(dict.fromkeys(kept)), unknown


def _fact_numbers(fact: Fact) -> set[float]:
    found = numbers_in(fact.text)
    for value in fact.display:
        found |= numbers_in(value)
    return found


def check(text: str, uses_raw: Any, facts: FactSet, instruction: str = "") -> Checked:
    by_id = facts.by_id()
    uses, dropped = known_ids(uses_raw, by_id)
    cited: set[float] = set()
    for fid in uses:
        cited |= _fact_numbers(by_id[fid])
    free = numbers_in(instruction) | {n for label in facts.labels for n in numbers_in(label)}
    free.add(float(facts.points))  # "over 6 months": the number of rows is a computed count too
    problems: list[str] = []
    extra: list[str] = []
    for token, value in number_tokens(text):
        if value in cited or value in free:
            continue
        owner = next((f.id for f in facts.facts if value in _fact_numbers(f)), None)
        if owner is None:
            problems.append(f"the number {token} is not one of the given display strings; copy them exactly")
            continue
        extra.append(owner)
        cited |= _fact_numbers(by_id[owner])
    problems += label_problems(text, facts.labels, [f.text for f in facts.facts])
    return Checked(tuple(dict.fromkeys(problems)), tuple(dict.fromkeys([*uses, *extra])), dropped)


def label_problems(text: str, labels: Sequence[str], fact_texts: Iterable[str]) -> list[str]:
    known = " ".join([*labels, *fact_texts]).casefold()
    problems = []
    for word in dict.fromkeys(_MONTH_WORD.findall(text or "")):
        index = MONTH_INDEX[word.casefold()]
        aliases = [name for name, n in MONTH_INDEX.items() if n == index]
        if not any(re.search(rf"\b{alias}\b", known) for alias in aliases):
            problems.append(f"{word} is not in the table; name only labels that appear in it")
    for quoted in _QUOTED.findall(text or ""):
        if quoted.casefold().strip() not in known:
            problems.append(f"\"{quoted}\" is not in the table; quote only labels that appear in it")
    return problems


def clean_text(text: str, source: str = "") -> str:
    """No raw HTML, no images, links only when their URL is in the user's input."""
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), out)
    out = _URL.sub(lambda m: m.group(0) if m.group(0) in source else "", out)
    out = _TAG.sub("", out).replace("<", "‹").replace(">", "›")
    return " ".join(out.split())[:MAX_TEXT_CHARS]


def cell(text: object) -> str:
    """A markdown table cell: never a link, an image or HTML; pipes escaped."""
    plain = _TAG.sub("", str(text)).replace("<", "‹").replace(">", "›").replace("|", "\\|")
    plain = plain.replace("](", "] (").replace("![", "! [")
    return " ".join(plain.split())[:300]
