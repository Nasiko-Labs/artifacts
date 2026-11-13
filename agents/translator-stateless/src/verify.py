"""Pure: the checks run on each chunk the model returns, and the code fixes applied when a repair did not
help. Problems are worded for the repair prompt ("missing ⟦P2⟧ in segment 3", "digits changed: 14 vs 41")."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

import languages
import mask

_DIGIT_MAP = {**{0x0660 + i: str(i) for i in range(10)}, **{0x06F0 + i: str(i) for i in range(10)},
              **{0x0966 + i: str(i) for i in range(10)}, **{0xFF10 + i: str(i) for i in range(10)}}
_DIGITS = re.compile(r"\d+", re.ASCII)
_SPACE = re.compile(r"\s+")
LENGTH_BAND = (0.4, 2.5)
CJK_LENGTH_BAND = (0.2, 2.0)
LENGTH_MIN_SOURCE_CHARS = 40
MAX_NOTES = 6
NOTE_MAX_CHARS = 240


def digits(text: str) -> Counter:
    """ASCII digit runs outside mask tokens, after mapping Arabic-Indic, Devanagari and full-width digits."""
    return Counter(_DIGITS.findall(mask.without_tokens(text).translate(_DIGIT_MAP)))


def token_problems(source: str, translated: str, n: int) -> list[str]:
    expected = mask.tokens_in(source)
    counts = Counter(mask.tokens_in(translated))
    found = [f"missing {t} in segment {n}" for t in expected if counts[t] == 0]
    found += [f"{t} appears {counts[t]} times in segment {n}; keep it once" for t in expected if counts[t] > 1]
    found += [f"{t} does not belong in segment {n}; remove it" for t in counts if t not in expected]
    return found


def digit_problem(source: str, translated: str) -> str | None:
    want, got = digits(source), digits(translated)
    if want == got:
        return None
    return f"digits changed: {' '.join(sorted(want.elements())) or 'none'} vs {' '.join(sorted(got.elements())) or 'none'}"


def chunk_problems(sources: Sequence[str], translations: Sequence[str], first: int) -> list[str]:
    """Every token and digit problem of one chunk; `first` is the 1-based number of its first segment."""
    found: list[str] = []
    for offset, (src, out) in enumerate(zip(sources, translations, strict=True)):
        found += token_problems(src, out, first + offset)
    problem = digit_problem("\n".join(sources), "\n".join(translations))
    return found + ([problem] if problem else [])


def unchanged(sources: Sequence[str], translations: Sequence[str]) -> bool:
    return [_SPACE.sub(" ", s).strip() for s in sources] == [_SPACE.sub(" ", t).strip() for t in translations]


def place_tokens(source: str, translated: str, spans: Mapping[str, str], n: int) -> tuple[str, list[str]]:
    """Code fallback after a repair: duplicates and foreign tokens removed, missing tokens appended at the end
    of the segment. Returns (text, notes)."""
    expected = mask.tokens_in(source)
    seen: set[str] = set()

    def keep_first(found: re.Match) -> str:
        tok = found.group(0)
        if tok not in expected or tok in seen:
            return ""
        seen.add(tok)
        return tok

    text = mask.TOKEN.sub(keep_first, translated)
    notes = []
    for tok in expected:
        if tok not in seen:
            text = f"{text.rstrip()} {tok}"
            notes.append(f"Could not place \"{spans.get(tok, tok)}\" in the translation; check segment {n}.")
    return text, notes


def length_note(source: str, translated: str, source_code: str | None, target_code: str) -> str | None:
    if len(source) < LENGTH_MIN_SOURCE_CHARS:
        return None
    low, high = CJK_LENGTH_BAND if languages.is_cjk(source_code) or languages.is_cjk(target_code) else LENGTH_BAND
    ratio = len(translated) / len(source)
    if low <= ratio <= high:
        return None
    word = "short" if ratio < low else "long"
    return f"The translation is unusually {word} for this language pair; check for omissions or additions."


def model_notes(raw: object, source: str) -> list[dict]:
    """Notes from the model whose `term` is found verbatim in the source; others are dropped."""
    kept = []
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, Mapping):
            continue
        term, note = str(entry.get("term") or "").strip(), str(entry.get("note") or "").strip()
        if term and note and term in source:
            kept.append({"term": term[:120], "note": note[:NOTE_MAX_CHARS]})
    return kept[:MAX_NOTES]
