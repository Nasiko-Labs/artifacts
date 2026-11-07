"""Pure: the code checks on the judge's output. Quotes and quoted tokens must be verbatim in the input
(normalised: NFKC, smart quotes straightened, whitespace collapsed); a path or identifier the model names must
appear in the input, else its sentence is replaced by the probe's generic outside place."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from probes import PROBES

MIN_QUOTE_CHARS = 3
_SMART = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"',
                        "„": '"', "′": "'", " ": " "})
_LEADING_LINE_NO = re.compile(r"^\s*(?:L?\d+[:\s]\s*)?[+\- ]?\s*")
_QUOTED = re.compile(r"`([^`]{1,120})`|\"([^\"]{1,120})\"|(?<![\w])'([^']{1,120})'(?![\w])")
_PLACE = re.compile(r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.(?:py|js|jsx|ts|tsx|kt|go|java|rb|rs|sql|ya?ml|json|"
                    r"toml|md|cs|php|swift|scala|proto|graphql|tf|sh|cfg|ini|env)\b|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|"
                    r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_URL = re.compile(r"https?://\S+")
_HTML = re.compile(r"<[^>\n]{1,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SAFETY_CLAIM = re.compile(r"safe to (?:merge|ship|deploy|release)|\blgtm\b|\bapproved?\b|\bship it\b",
                          re.IGNORECASE)


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").translate(_SMART).split())


def verbatim(quote: str, haystack_norm: str) -> bool:
    q = normalise(_LEADING_LINE_NO.sub("", quote or "", count=1))
    return len(q) >= MIN_QUOTE_CHARS and q in haystack_norm


def claims_safety(text: str) -> bool:
    """The agent never calls a change safe to merge; model text that does fails its check."""
    return bool(_SAFETY_CLAIM.search(text or ""))


def quoted_tokens_ok(text: str, input_norm: str) -> bool:
    return all(normalise(a or b or c) in input_norm for a, b, c in _QUOTED.findall(text or ""))


def clean(text: str, input_norm: str) -> str:
    """No raw HTML, no images, and a URL only when it is in the input."""
    text = _IMAGE.sub("", _HTML.sub("", text or ""))
    text = _URL.sub(lambda m: m.group(0) if normalise(m.group(0)) in input_norm else "[link removed]", text)
    return " ".join(text.split())


def unknown_places(text: str, input_norm: str) -> list[str]:
    folded = input_norm.casefold()
    return [p for p in _PLACE.findall(text or "") if p.casefold() not in folded]


def replace_invented(text: str, generic: str, input_norm: str) -> tuple[str, int]:
    """(text, sentences replaced): a sentence naming a place that is not in the input becomes `generic`."""
    out, replaced = [], 0
    for sentence in _SENTENCE.split(text or ""):
        if unknown_places(sentence, input_norm):
            replaced += 1
            if generic not in out:
                out.append(generic)
        else:
            out.append(sentence)
    return " ".join(s for s in out if s), replaced


@dataclass(frozen=True)
class Verified:
    applies: object            # True | False | "unclear" | None (not judged)
    what: str
    why: str
    safe_if: str
    diff_ok: bool
    context_ok: bool
    generic: bool
    dropped: bool
    replaced: int
    context_quote: str = ""


def generic_text(fired: dict) -> tuple[str, str, str]:
    probe = PROBES[fired["probe"]]
    what = f"{probe.kind.capitalize()}: {fired['detail']}" if fired.get("detail") else probe.kind.capitalize()
    return what, probe.outside, f"nothing outside this diff still depends on `{fired['token']}`"


def verify(fired: dict, judgement: dict | None, diff_norm: str, context_norm: str) -> Verified:
    what, why, safe_if = generic_text(fired)
    if judgement is None:
        return Verified(None, what, why, safe_if, False, False, True, False, 0)
    input_norm = f"{diff_norm}\n{context_norm}"
    diff_ok = verbatim(judgement["evidence_quote"], diff_norm)
    cq = judgement.get("context_quote") or ""
    context_ok = bool(context_norm) and verbatim(cq, context_norm) and fired["token"].casefold() in cq.casefold()
    texts = (judgement["what"], judgement["why"], judgement["safe_if"])
    text_ok = all(texts[:2]) and all(quoted_tokens_ok(t, input_norm) and not claims_safety(t) for t in texts)
    if not diff_ok or not text_ok:
        return Verified(judgement["applies"], what, why, safe_if, False, context_ok, True, True, 0,
                        cq if context_ok else "")
    outside = PROBES[fired["probe"]].outside
    m_what, r1 = replace_invented(clean(judgement["what"], input_norm), outside, input_norm)
    m_why, r2 = replace_invented(clean(judgement["why"], input_norm), outside, input_norm)
    m_safe, r3 = replace_invented(clean(judgement["safe_if"], input_norm), safe_if, input_norm)
    return Verified(judgement["applies"], m_what or what, m_why or why, m_safe or safe_if, True, context_ok,
                    False, False, r1 + r2 + r3, cq if context_ok else "")
