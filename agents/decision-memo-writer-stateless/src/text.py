"""Pure text helpers: normalising for verbatim checks, finding numbers/dates in wording, word counts,
and cleaning model text before it is rendered."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
                         "‐": "-", "‑": "-", "…": "...", "*": " ", "`": " "})
_SPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"(?<![\w.])[$€£]?\s?\d+(?:[.,]\d+)*(?:\s?%|\s?(?:percent|million|billion|thousand|bn|k|m|b)\b)?",
                     re.IGNORECASE)
_CALENDAR = re.compile(
    r"\b(?:Q[1-4]|H[12]|FY\d{2,4}|Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b")
_UNITS = {"million": "m", "thousand": "k", "billion": "bn", "b": "bn", "percent": "%"}
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_HEADING = re.compile(r"#{1,6}(?=\s)")
_LEADING_MARKUP = re.compile(r"^[#>*\-\s]+")
_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).casefold()
    return _SPACE.sub(" ", folded).strip()


def words(text: str) -> int:
    return len((text or "").split())


def first_words(text: str, limit: int) -> str:
    parts = (text or "").split()
    return " ".join(parts[:limit]) + ("..." if len(parts) > limit else "")


def strip_markers(text: str) -> str:
    """Removes anything shaped like a data-fence marker, so pasted text cannot close a fence."""
    return _MARKER.sub("", text or "")


def _number_key(token: str) -> str:
    key = re.sub(r"[\s$€£,]", "", token.lower())
    match = re.match(r"([\d.]+)(.*)", key)
    if not match:
        return key
    return match.group(1).rstrip(".") + _UNITS.get(match.group(2), match.group(2))


def figures(text: str) -> set[str]:
    """The numbers, amounts, percentages, months and quarters in `text`, as comparable keys."""
    found = {_number_key(m.group()) for m in _NUMBER.finditer(text or "")}
    return found | {m.group().casefold()[:3] for m in _CALENDAR.finditer(text or "")}


def ungrounded(text: str, allowed: Iterable[str]) -> list[str]:
    """Figures in `text` that are not among the `allowed` keys (see `figures`)."""
    allowed_keys = set(allowed)
    out = []
    for m in _NUMBER.finditer(text or ""):
        key = _number_key(m.group())
        bare = re.sub(r"[^\d.]", "", key)
        if key not in allowed_keys and bare not in allowed_keys:
            out.append(m.group().strip())
    out += [m.group() for m in _CALENDAR.finditer(text or "") if m.group().casefold()[:3] not in allowed_keys]
    return out


def replace_ungrounded(text: str, allowed: Iterable[str], placeholder: str) -> str:
    result = text
    for token in ungrounded(text, allowed):
        result = re.sub(rf"(?<![\w.]){re.escape(token)}(?!\w)", placeholder, result, count=1)
    return result


def clean(text: str, urls: Iterable[str] = ()) -> str:
    """One line of plain text: tags, images, link targets, unknown URLs and markdown openers removed;
    table pipes escaped."""
    known = {u.rstrip(".,)") for u in urls}
    plain = _IMAGE.sub("", _TAG.sub("", text or ""))
    plain = _LINK.sub(lambda m: m.group(1) + (f" ({m.group(2)})" if m.group(2) in known else ""), plain)
    plain = _URL.sub(lambda m: m.group() if m.group().rstrip(".,)") in known else "[link removed]", plain)
    plain = _HEADING.sub(" ", plain)
    plain = _LEADING_MARKUP.sub("", _SPACE.sub(" ", plain)).replace("**", "").strip()
    return plain.replace("|", "/")


def urls_in(text: str) -> set[str]:
    return {m.group().rstrip(".,)") for m in _URL.finditer(text or "")}
