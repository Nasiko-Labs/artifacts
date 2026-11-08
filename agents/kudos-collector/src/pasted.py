"""Pure: evidence the user pasted under their request (the lines after the first), labelled "pasted by
you". Accepted as given; nothing is fetched or checked against a tool.

- A pull request line holds a pull or merge request link; it belongs to the one person the line names,
  or to the only person in the request.
- A praise line names a person, contains a praise phrase, and gives its author as "Author: text" or
  "text - Author"; the author must be someone other than the person.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import people as people_
import praise_terms
from people import Author
from records import Person, PraiseItem, PullRequest

MAX_PASTED_LINES = 400
_PR_URL = re.compile(r"https?://\S+?/(?:pull|pulls|merge_requests|pull-requests)/(\d+)\S*")
_AUTHOR_FIRST = re.compile(r"^\s*[-*•]?\s*([A-Z][\w.-]*(?:\s+[A-Z][\w.-]*)?)\s*(?:\([^)]*\))?\s*:\s*(.+)$")
_AUTHOR_LAST = re.compile(r"^\s*[-*•]?\s*[\"“](.+?)[\"”]\s*(?:-|—|–|~)\s*([A-Z][\w.-]*(?:\s+[A-Z][\w.-]*)?)\b.*$")
_BULLET = re.compile(r"^[\s\-*•#\d.:)]+")


@dataclass(frozen=True)
class Pasted:
    prs: tuple[PullRequest, ...] = ()
    praise: tuple[PraiseItem, ...] = ()

    @property
    def any(self) -> bool:
        return bool(self.prs or self.praise)


def has_paste(message: str) -> bool:
    return bool(_lines(message))


def parse(message: str, people: Sequence[Person]) -> Pasted:
    prs, praise = [], []
    for line in _lines(message):
        if m := _PR_URL.search(line):
            pr = _pr(line, m, people)
            if pr is not None:
                prs.append(pr)
            continue
        item = _praise(line, people)
        if item is not None:
            praise.append(item)
    return Pasted(tuple({p.url: p for p in prs}.values()), tuple(praise))


def _lines(message: str) -> list[str]:
    rest = (message or "").split("\n", 1)[1] if "\n" in (message or "") else ""
    return [ln for ln in rest.splitlines() if ln.strip()][:MAX_PASTED_LINES]


def _owner(line: str, people: Sequence[Person]) -> Person | None:
    hits = [p for p in people if people_.mentions(line, p)]
    if len(hits) == 1:
        return hits[0]
    return people[0] if not hits and len(people) == 1 else None


def _pr(line: str, match: re.Match, people: Sequence[Person]) -> PullRequest | None:
    person = _owner(line, people)
    if person is None:
        return None
    title = _BULLET.sub("", line.replace(match.group(0), " ")).strip(" -—:")
    return PullRequest(number=int(match.group(1)), url=match.group(0).rstrip(").,;"), title=title[:200],
                       person=person.label, merged_at=None, pasted=True)


def _praise(line: str, people: Sequence[Person]) -> PraiseItem | None:
    parsed = _split(line)
    if parsed is None:
        return None
    author, quote = parsed
    if not praise_terms.has_praise_phrase(quote):
        return None
    hits = [p for p in people if people_.mentions(quote, p)]
    if len(hits) != 1 or people_.person_for(Author(name=author), hits) is not None:
        return None
    return PraiseItem(hits[0].label, quote.strip(), author, "pasted by you", "", None, pasted=True)


def _split(line: str) -> tuple[str, str] | None:
    if m := _AUTHOR_LAST.match(line):
        return m.group(2).strip(), m.group(1).strip()
    if m := _AUTHOR_FIRST.match(line):
        return m.group(1).strip(), m.group(2).strip().strip("\"“”")
    return None
