"""Pure: the checks on a model narrative. Every number must be a digest figure, every cited id a digest id,
every causal clause a supplied driver, and the length within its band's maximum. Plus text cleaning for
anything rendered (no images, no raw HTML, no links that are not in the input)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import drivers
from digest import LENGTHS, NUMBER, Digest, norm_number

CAUSAL = re.compile(r"\b(?:because(?: of)?|due to|driven by|as a result of|caused by|owing to|thanks to|"
                    r"attributable to|on the back of|resulting from)\b", re.IGNORECASE)
OVERLAP_SHARE = 0.6
MIN_WORD = 3
_STOP = {"the", "and", "for", "with", "was", "were", "this", "that", "from", "its", "their", "our", "new", "higher",
         "lower", "more", "less", "budget", "actual", "over", "under"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML = re.compile(r"<[^>\n]{1,200}>")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BARE_URL = re.compile(r"https?://[^\s)>\]]+")


@dataclass(frozen=True)
class Problems:
    numbers: tuple[str, ...] = ()
    causes: tuple[str, ...] = ()       # sentences whose cause no supplied driver backs
    unknown_ids: tuple[str, ...] = ()
    too_long: bool = False

    @property
    def ok(self) -> bool:
        return not (self.numbers or self.causes or self.unknown_ids or self.too_long)

    def describe(self) -> str:
        notes = []
        if self.numbers:
            notes.append("figures not in the digest: " + ", ".join(self.numbers))
        if self.causes:
            notes.append("causes not supplied by the user: " + " | ".join(self.causes))
        if self.unknown_ids:
            notes.append("ids not in the digest: " + ", ".join(self.unknown_ids))
        if self.too_long:
            notes.append("too long for the requested length")
        return "; ".join(notes)


def words(text: str) -> int:
    return len((text or "").split())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split((text or "").strip()) if s.strip()]


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(w) >= MIN_WORD and w not in _STOP}


def supported(clause: str, supplied: Sequence[str]) -> bool:
    plain = " ".join(re.findall(r"[a-z0-9]+", clause.casefold()))
    mine = _content(clause)
    for driver in supplied:
        theirs = " ".join(re.findall(r"[a-z0-9]+", driver.casefold()))
        if plain and theirs and (plain in theirs or theirs in plain):
            return True
        if mine and len(mine & _content(driver)) / len(mine) >= OVERLAP_SHARE:
            return True
    return False


def unsupported_causes(text: str, supplied: Sequence[str]) -> list[str]:
    bad = []
    for sentence in sentences(text):
        match = CAUSAL.search(sentence)
        if match and not supported(sentence[match.end():], supplied):
            bad.append(sentence)
    return bad


def check(text: str, uses: Sequence[str], digest: Digest) -> Problems:
    allowed = digest.allowed_numbers()
    numbers = [t for t in NUMBER.findall(text) if norm_number(t) not in allowed]
    supplied = [ln.item.driver for ln in (*digest.lines, *digest.offsets) if ln.item.driver]
    return Problems(numbers=tuple(dict.fromkeys(numbers)), causes=tuple(unsupported_causes(text, supplied)),
                    unknown_ids=tuple(u for u in uses if u not in digest.ids),
                    too_long=words(text) > LENGTHS[digest.length][1])


def replace_causes(text: str, bad: Sequence[str], names: Sequence[str]) -> str:
    """Each unsupported causal sentence becomes "<Item>: driver not provided." (or goes, if it names no line)."""
    out = []
    for sentence in sentences(text):
        if sentence not in bad:
            out.append(sentence)
            continue
        named = drivers.mentions(sentence, names)
        if named:
            out.append(f"{named[0]}: driver not provided.")
    return " ".join(out)


def clean_text(text: str, source: str) -> str:
    """No images, no raw HTML, links only when their URL appears verbatim in `source`."""
    cleaned = _HTML.sub("", _IMAGE.sub("", text or ""))
    cleaned = _LINK.sub(lambda m: m.group(0) if m.group(2) in source else m.group(1), cleaned)
    cleaned = _BARE_URL.sub(lambda m: m.group(0) if m.group(0) in source else "(link removed)", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def cell(text: str, source: str) -> str:
    """A table cell: cleaned, pipes escaped, on one line."""
    return " ".join(clean_text(text, source).replace("|", "\\|").split())
