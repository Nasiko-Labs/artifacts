"""Pure: is a model-written example grounded in its record? Every number, link, ticket id and proper noun must
appear in the record; `led/owned/drove/architected` only if the record says so; no adjectives about the person
and no ranking words; the right shape and length. A failing example is repaired once, then falls back to the
cleaned title (PR, feature) or the verbatim quote (praise)."""

from __future__ import annotations

import re

from records import PRAISE, Record
from shapes import clean_title

SENTENCE_MAX_WORDS = 30
PHRASE_MAX_WORDS = 25
QUOTE_MAX_CHARS = 240
NUMBER = re.compile(r"\d[\d,.]*%?")
LINK = re.compile(r"https?://\S+|\b[\w-]+\.(?:com|io|dev|org|net)\b\S*", re.IGNORECASE)
TICKET = re.compile(r"#\d+|\b[A-Z]{2,10}-\d+\b")
PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][\w'-]*")
LEADERSHIP = {"led": ("led", "lead"), "owned": ("own",), "drove": ("drove", "drive"), "architected": ("architect",),
              "spearheaded": ("spearhead",), "headed": ("head",)}
PERSON_ADJECTIVES = re.compile(r"\b(?:brilliant|amazing|talented|awesome|outstanding|exceptional|incredible|"
                               r"rockstar|superstar|hero|heroic|tireless|stellar|fantastic|excellent|great)\b",
                               re.IGNORECASE)
RANKING = re.compile(r"\b(?:top|best|rank(?:ed|ing)?|score[ds]?|most|more than|better than|leaderboard)\b",
                     re.IGNORECASE)
IRREGULAR_PAST = frozenset({"built", "wrote", "ran", "made", "led", "took", "caught", "found", "drove", "shipped",
                            "brought", "sent", "taught", "kept", "held", "gave", "did", "spent", "set", "got", "put",
                            "cut", "rewrote", "split", "began", "won", "fixed", "sped", "stood", "undid", "redid"})
_CASE_EXEMPT = frozenset({"I", "PR", "PRs", "API", "UI", "CSV", "PDF", "SSO", "QA", "CI", "JSON", "SQL"})


def _in(token: str, haystack: str) -> bool:
    return token.casefold().strip(".,;:") in haystack


def problems(example: str, record: Record) -> list[str]:
    text = " ".join((example or "").split())
    if not text:
        return ["empty"]
    hay = record.text.casefold()
    found: list[str] = []
    found += [f"number '{n}' not in record" for n in NUMBER.findall(text) if not _in(n.rstrip(".,"), hay)]
    found += [f"link '{u}' not in record" for u in LINK.findall(text) if not _in(u, hay)]
    found += [f"id '{t}' not in record" for t in TICKET.findall(text) if not _in(t, hay)]
    found += [f"name '{p}' not in record" for p in PROPER.findall(text)
              if p not in _CASE_EXEMPT and not _in(p, hay)]
    for word, stems in LEADERSHIP.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE) and not any(s in hay for s in stems):
            found.append(f"'{word}' is not in the record")
    found += [f"adjective about the person: '{a}'" for a in PERSON_ADJECTIVES.findall(text)
              if not _in(a, hay)]
    found += [f"ranking word: '{w}'" for w in RANKING.findall(text)]
    found += _shape(text, record.kind == PRAISE)
    return found


def _shape(text: str, praise: bool) -> list[str]:
    words = text.split()
    first = words[0].casefold().strip(",.")
    if praise:
        if len(words) > PHRASE_MAX_WORDS:
            return [f"over {PHRASE_MAX_WORDS} words"]
        return [] if first.endswith("ing") else ["must start with an -ing verb"]
    if len(words) > SENTENCE_MAX_WORDS:
        return [f"over {SENTENCE_MAX_WORDS} words"]
    return [] if first.endswith("ed") or first in IRREGULAR_PAST else ["must start with a past-tense verb"]


def quote(record: Record) -> str:
    body = " ".join((record.body or record.text).split())
    return body if len(body) <= QUOTE_MAX_CHARS else body[:QUOTE_MAX_CHARS - 3].rstrip() + "..."


def fallback(record: Record) -> str:
    """The code-only example: the cleaned title for a PR or feature, the quote for praise."""
    if record.kind == PRAISE:
        return f'"{quote(record)}"'
    title = clean_title(record.title) or clean_title(record.text)
    return title if title.endswith((".", "!", "?")) else f"{title}."
