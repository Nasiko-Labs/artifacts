"""Pure: customer data out of every reply BEFORE anything else sees it, then the parts that are not the answer.

- PII -> placeholders: emails {{email}}, URLs with a query string {{url}}, phone numbers {{phone}}, digit runs
  of 5+ {{number}}, the greeted name ("Hi Sam,") {{customer_name}} wherever it recurs in that reply.
- Quoted text (">" lines, "On ... wrote:" onward) and a sign-off ("Best regards, ..." in the last lines) are cut.
- Boilerplate: a sentence that occurs in >= 40% of all replies (a shared footer) is removed and reported.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence

BOILERPLATE_SHARE = 0.40
BOILERPLATE_MIN_WORDS = 3
SIGNATURE_WINDOW_LINES = 4

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_QUERY = re.compile(r"(?:https?://|www\.)[^\s?]+\?\S*[^\s.,;:!?)\]]")
_PHONE = re.compile(r"(?<![\w{])(?:\+\d[\d ().-]{6,}\d|\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4})(?!\w)")
_LONG_DIGITS = re.compile(r"(?<![\w{])\d{5,}(?!\w)")
_NOT_A_NAME = {"there", "team", "all", "everyone", "folks", "again", "friend", "customer", "both", "guys"}
_GREETING = re.compile(
    r"^(\s*(?i:hi|hello|hey|dear|good (?:morning|afternoon|evening))[ \t]+(?:(?i:mr|ms|mrs|dr)\.?[ \t]+)?)"
    r"([A-Z][\w'’-]+(?:[ \t]+[A-Z][\w'’-]+)?)([ \t]*[,!:])")
_GREETING_START = re.compile(r"^\s*(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b", re.IGNORECASE)
_WROTE = re.compile(r"^\s*On .{3,200}wrote:\s*$", re.MULTILINE)
_SIGN_OFF_LINE = re.compile(
    r"^\s*(?:best(?: regards| wishes)?|kind regards|warm regards|regards|many thanks|thanks(?: again)?|thank you|"
    r"cheers|sincerely|all the best|--)\s*[,!.]?\s*(?:[A-Z][\w.'’ -]{0,40})?$", re.IGNORECASE)
_SIGN_OFF_INLINE = re.compile(
    r"(?<=[.!?])\s+(?:best regards|kind regards|warm regards|regards|best|cheers|sincerely|many thanks|thanks)"
    r"\s*,\s*[A-Z][\w .'’-]{0,40}$", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_SPACES = re.compile(r"\s+")


def _names_in(text: str) -> tuple[str, ...]:
    found = []
    for line in text.splitlines()[:3]:
        match = _GREETING.match(line)
        if match and match.group(2).split()[0].casefold() not in _NOT_A_NAME:
            full = match.group(2).strip()
            found += [full, full.split()[0]] if " " in full else [full]
    return tuple(dict.fromkeys(found))


def scrub_pii(text: str) -> tuple[str, tuple[str, ...]]:
    """(text with placeholders, the greeted names found). Run before any other step."""
    names = _names_in(text)
    out = text
    if names:
        out = _GREETING.sub(lambda m: f"{m.group(1)}{{{{customer_name}}}}{m.group(3)}"
                            if m.group(2).strip() in names else m.group(0), out, count=1)
    for name in sorted(names, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(name)}\b", "{{customer_name}}", out)
    out = _EMAIL.sub("{{email}}", out)
    out = _URL_QUERY.sub("{{url}}", out)
    out = _PHONE.sub("{{phone}}", out)
    out = _LONG_DIGITS.sub("{{number}}", out)
    return out, names


def scrub_names(text: str, names: Iterable[str]) -> str:
    """Known customer names (from any reply's greeting) out of model output too."""
    out = text
    for name in sorted(set(names), key=len, reverse=True):
        if len(name) >= 3:
            out = re.sub(rf"\b{re.escape(name)}\b", "{{customer_name}}", out)
    return scrub_pii(out)[0]


def strip_quoted_and_signature(text: str) -> str:
    wrote = _WROTE.search(text)
    body = text[:wrote.start()] if wrote else text
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    filled = [i for i, line in enumerate(lines) if line.strip()]
    for i in filled[-SIGNATURE_WINDOW_LINES:]:
        if i > filled[0] and _SIGN_OFF_LINE.match(lines[i]):
            lines = lines[:i]
            break
    joined = "\n".join(lines).strip()
    return _SIGN_OFF_INLINE.sub("", joined).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def _key(sentence: str) -> str:
    return _SPACES.sub(" ", sentence.casefold()).strip()


def find_boilerplate(bodies: Sequence[str]) -> tuple[tuple[str, float], ...]:
    """Sentences in >= 40% of replies: (sentence as first seen, share of replies), most common first."""
    if not bodies:
        return ()
    counts: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for body in bodies:
        for sentence in {_key(s): s for s in sentences(body)}.items():
            key, shown = sentence
            if len(key.split()) < BOILERPLATE_MIN_WORDS or _GREETING_START.match(key):
                continue
            counts[key] += 1
            first_seen.setdefault(key, shown)
    found = [(first_seen[k], n / len(bodies)) for k, n in counts.items() if n / len(bodies) >= BOILERPLATE_SHARE]
    return tuple(sorted(found, key=lambda pair: (-pair[1], pair[0])))


def strip_boilerplate(body: str, boilerplate: Iterable[str]) -> str:
    drop = {_key(s) for s in boilerplate}
    if not drop:
        return body
    kept_lines = []
    for line in body.splitlines():
        parts = [s for s in sentences(line) if _key(s) not in drop]
        if parts or not line.strip():
            kept_lines.append(" ".join(parts))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
