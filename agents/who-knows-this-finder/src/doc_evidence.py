"""Document evidence, checked in code: a document counts when its title and snippet together contain at
least 2 of the topic's terms. Its creator becomes `doc_author` (on the created date) and its last
modifier, when a different person, `doc_edit` (on the modified date). Title, names, dates and link must
appear in this step's tool output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import keywords
from chat_evidence import same_person
from code_evidence import backed_who
from models import Evidence, Who
from verify import appears_in, normalise_text, parse_day, safe_link, text_of

MIN_DOC_TERMS = 2


@dataclass(frozen=True)
class Read:
    evidence: tuple[Evidence, ...]
    dropped: int = 0


def evidence_from(data: Mapping[str, Any], seen_text: str, terms: Sequence[str]) -> Read:
    seen = normalise_text(seen_text)
    rows = [r for r in data.get("docs") or () if isinstance(r, Mapping)] if isinstance(data.get("docs"), list) else []
    kept: list[Evidence] = []
    dropped = 0
    for row in rows:
        title = text_of(row.get("title"))
        snippet = text_of(row.get("snippet"))
        if keywords.hits(f"{title} {snippet if appears_in(snippet, seen) else ''}", terms) < MIN_DOC_TERMS:
            continue
        if not appears_in(title, seen):
            dropped += 1
            continue
        link = safe_link(text_of(row.get("link")), seen)
        author = _item("doc_author", row.get("created_by"), row.get("created_at"), title, link, seen)
        editor = _item("doc_edit", row.get("modified_by"), row.get("modified_at"), title, link, seen)
        if author is None and editor is None:
            dropped += 1
            continue
        if editor is not None and author is not None and same_person(editor.who, author.who):
            editor = None  # the author's own later edit is already counted as authorship
        kept.extend(e for e in (author, editor) if e is not None)
    return Read(tuple(kept), dropped)


def _item(kind: str, raw_who: Any, raw_date: Any, title: str, link: str, seen: str) -> Evidence | None:
    if not isinstance(raw_who, Mapping):
        return None
    who = backed_who(Who(text_of(raw_who.get("name")), text_of(raw_who.get("email")), ""), seen)
    day = parse_day(raw_date)
    if who is None or day is None or not appears_in(text_of(raw_date), seen):
        return None
    return Evidence(kind, who, title, day, link)
