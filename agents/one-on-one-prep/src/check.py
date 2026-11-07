"""Pure: the model's extracted items checked in code. A snippet must be a verbatim quote of its own part
(an update quote cited for a notes item is rejected); model text with an ungrounded number, date or name, a
judgement word, or over 25 words is rewritten to its quote once, else dropped and counted. A quote that itself
judges the person is dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from extract import Raw
from grounding import clean_quote, in_source, normalise, ungrounded
from judgement import has_judgement

MAX_TEXT_WORDS = 25
MAX_QUOTE_WORDS = 45
PREFIX = {"commitments": "c", "topics": "t", "concerns_raised": "k", "goals": "g", "accomplishments": "a",
          "blockers": "b", "events": "e"}
TEXT_KEY = {"commitments": "what", "accomplishments": "what", "blockers": "what", "events": "what"}
_FIRST_PERSON = re.compile(r"\b(?:i|i'll|i'd|i'm|i've|me|my)\b", re.IGNORECASE)
_THIRD_PERSON = re.compile(r"\b(?:she|he|they|she'll|he'll|they'll|her|his|their)\b", re.IGNORECASE)


@dataclass
class Verified:
    items: dict[str, list[dict]] = field(default_factory=lambda: {k: [] for k in PREFIX})
    unverified: int = 0


def _text(raw: dict, kind: str) -> str:
    return " ".join(str(raw.get(TEXT_KEY.get(kind, "text")) or "").split())


def checked_text(text: str, quote: str, ground: str) -> str | None:
    """`text` if it is short, grounded and judgement-free; else the quote; None when neither is usable."""
    if has_judgement(quote) or len(quote.split()) > MAX_QUOTE_WORDS:
        return None
    ok = text and len(text.split()) <= MAX_TEXT_WORDS and not has_judgement(text) and not ungrounded(text, ground)
    return text if ok else quote


def owner(who: str, quote: str, person: str) -> str:
    """manager / report / a name written in the quote / other: never a guess the quote doesn't support."""
    claim = (who or "").strip()
    named = bool(person) and re.search(rf"\b{re.escape(person.casefold())}\b", quote.casefold()) is not None
    first, third = bool(_FIRST_PERSON.search(quote)), bool(_THIRD_PERSON.search(quote))
    lowered = claim.casefold()
    if lowered == "manager":
        return "manager" if first else ("report" if named else "other")
    if lowered in ("report", person.casefold()) and claim:
        return "report" if named or (third and not first) or (not person and not first) else (
            "manager" if first else "other")
    if claim and lowered != "other" and re.search(rf"\b{re.escape(lowered)}\b", quote.casefold()):
        return claim
    return "other"


def _source(part: str) -> str:
    return {"NOTES": "notes", "UPDATES": "updates"}.get(part, "pasted")


def verify(raw: Raw, notes: str, updates: str, person: str = "") -> Verified:
    out = Verified()
    texts = {"NOTES": notes, "UPDATES": updates, "PASTED": notes + "\n" + updates}
    seen: set[str] = set()
    for kind, entries in raw.items.items():
        for part, item in entries:
            ground = texts[part]
            quote = clean_quote(str(item.get("snippet") or ""))
            key = f"{kind}:{normalise(quote)}"
            if not in_source(quote, ground):
                out.unverified += 1
                continue
            if key in seen:
                continue
            text = checked_text(_text(item, kind), quote, ground)
            if text is None:
                out.unverified += 1
                continue
            seen.add(key)
            entry = {"id": f"{PREFIX[kind]}{len(out.items[kind]) + 1}", "text": text, "quote": quote,
                     "source": _source(part)}
            for extra in ("due", "date"):
                value = " ".join(str(item.get(extra) or "").split())
                if value and value.casefold() != "null" and normalise(value) in normalise(ground):
                    entry[extra] = value
            if kind == "commitments":
                entry["who"] = owner(str(item.get("who") or ""), quote, person)
            out.items[kind].append(entry)
    return out
