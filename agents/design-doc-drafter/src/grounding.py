"""Pure: is a snippet, a number or a technology name really in what the user gave? Both sides are normalised
the same way. A number the input never gave becomes `[TBD: <what>]`; a technology the input never named and
no decision proposes becomes a generic noun."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from tech_names import TECH

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " "})
_WHITESPACE = re.compile(r"\s+")
_EDGE = " .,;:!?\"'()[]-*`"
_NUMBER = re.compile(r"(?<![\w.$/-])\$?(\d[\d,]*(?:\.\d+)?)(?:\s?(%|[A-Za-z][A-Za-z/-]*))?")
_STOPWORDS = frozenset({"of", "and", "or", "to", "the", "a", "an", "in", "on", "for", "per", "is", "are", "at", "by",
                        "with", "from", "as", "times", "x"})
_PROTECTED = re.compile(r"\b(?:HTTP|HTTPS|OAuth|TLS|SSL|AES|SHA|IPv|status|error|code|p)[- /]?\d[\d.]*x*\b",
                        re.IGNORECASE)
_TBD = re.compile(r"\[TBD[^\]]*\]")


def normalise(text: str) -> str:
    """Casefold, NFKC, straight quotes, plain dashes, single spaces."""
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", plain).strip().casefold()


def snippet_in(snippet: str, norm_ground: str) -> bool:
    clean = normalise(snippet).strip(_EDGE)
    return len(clean) >= 3 and clean in norm_ground


def ground_numbers(norm_ground: str) -> frozenset[str]:
    return frozenset(_digits(m.group(1)) for m in _NUMBER.finditer(norm_ground))


def _digits(value: str) -> str:
    return value.replace(",", "").rstrip(".")


def ground_text_numbers(text: str, numbers: frozenset[str]) -> tuple[str, tuple[str, ...]]:
    """(text with every number the input never gave replaced by [TBD: unit], the units that were replaced)."""
    protected = [m.span() for m in _PROTECTED.finditer(text or "")]
    protected += [m.span() for m in _TBD.finditer(text or "")]
    missing: list[str] = []

    def swap(match: re.Match) -> str:
        if any(start <= match.start() < end for start, end in protected):
            return match.group(0)
        if _digits(match.group(1)) in numbers:
            return match.group(0)
        unit = (match.group(2) or "").strip()
        if not unit or unit.casefold() in _STOPWORDS:
            missing.append("value")
            return "[TBD: value]" + match.group(0)[match.end(1) - match.start():]
        missing.append(unit)
        return f"[TBD: {unit}]"

    return _NUMBER.sub(swap, text or ""), tuple(missing)


def named_tech(text: str) -> tuple[str, ...]:
    return tuple(name for name in TECH if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text or ""))


def tech_in_ground(name: str, norm_ground: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name.casefold())}(?![\w])", norm_ground) is not None


def generic_tech(text: str, norm_ground: str, proposed: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """Rewrites technology names that are neither in the input nor a proposed decision to a generic noun."""
    allowed = {n for n in named_tech(" ".join(proposed))}
    replaced: list[str] = []
    out = text or ""
    for name in named_tech(out):
        if name in allowed or tech_in_ground(name, norm_ground):
            continue
        replaced.append(name)
        out = re.sub(rf"(?<![\w.]){re.escape(name)}(?![\w])", TECH[name], out)
    return out, tuple(replaced)


def tbd_count(text: str) -> int:
    return len(_TBD.findall(text or ""))
