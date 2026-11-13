"""Pure: which checked facts a summary sentence rests on. The model's fact ids are read leniently (`1`, `F1`,
`fact f1` and an inline `(f1)` all mean f1); a sentence that cites none is attributed in code to the facts whose
words it restates. A sentence that restates no fact cites nothing and cannot stand."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import grounding as g
from facts import Fact

FACT_REFS = re.compile(r"\s*[(\[]\s*(?:fact\s*)?f\d+(?:\s*,\s*(?:fact\s*)?f\d+)*\s*[)\]]", re.IGNORECASE)
MIN_SHARED_WORDS = 2
_ID = re.compile(r"^\s*(?:fact\s*)?f?\s*(\d{1,4})\s*$", re.IGNORECASE)
_INLINE_ID = re.compile(r"\bf(\d{1,4})\b", re.IGNORECASE)


def normalise_id(raw: object) -> str | None:
    match = _ID.match(str(raw)) if isinstance(raw, (str, int)) and not isinstance(raw, bool) else None
    return f"f{int(match.group(1))}" if match else None


def split_refs(text: str) -> tuple[str, tuple[str, ...]]:
    """(the sentence without inline fact refs, the ids those refs named)."""
    ids = tuple(f"f{int(n)}" for ref in FACT_REFS.findall(text or "") for n in _INLINE_ID.findall(ref))
    return FACT_REFS.sub("", text or "").strip(), ids


def ids_of(raw_ids: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(i for i in map(normalise_id, raw_ids) if i))


def attribute(text: str, facts: Sequence[Fact]) -> tuple[str, ...]:
    """The facts whose words the sentence restates (at least two shared words), in fact order."""
    words = g.content_words(text)
    return tuple(f.id for f in facts if len(words & g.content_words(f"{f.text} {f.snippet}")) >= MIN_SHARED_WORDS)


def cited(sentence: dict, facts: Sequence[Fact]) -> tuple[str, ...]:
    """The known fact ids a sentence cites, or, when it cites none, the ones code attributes it to."""
    known = {f.id for f in facts}
    given = tuple(i for i in sentence.get("fact_ids") or () if i in known)
    return given or attribute(sentence.get("text") or "", facts)
