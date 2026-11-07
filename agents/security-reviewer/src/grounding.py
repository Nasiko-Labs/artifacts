"""Pure: the verbatim checks. Text is compared normalised (NFKC, smart quotes -> straight, whitespace
collapsed), so a quote copied from the code matches however the model spaced it."""

from __future__ import annotations

import re
import unicodedata

from diff_parse import CodeFile, Line

MIN_QUOTE_CHARS = 6
_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "–": "-", "—": "-", "…": "..."})
_WHITESPACE = re.compile(r"\s+")
_BACKTICKED = re.compile(r"`([^`\n]{1,120})`")
_IDENTIFIER = re.compile(r"^[\w.$/:\-\[\]'\"]+(?:\(\))?$")
_QUOTED_IN_REASON = re.compile(r"`([^`\n]{6,200})`|\"([^\"\n]{6,200})\"|'([^'\n]{6,200})'")


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", folded).strip()


def locate(quote: str, files: tuple[CodeFile, ...], prefer: str = "") -> tuple[CodeFile, Line] | None:
    """The line holding the quote's first substantial line, in `prefer` first; every other line of a
    multi-line quote must also be in that file. None when the quote is not verbatim in the input."""
    parts = [normalise(p) for p in (quote or "").split("\n") if normalise(p)]
    anchor = next((p for p in parts if len(p) >= MIN_QUOTE_CHARS), None)
    if anchor is None:
        return None
    for code in sorted(files, key=lambda f: f.path != prefer):
        texts = [normalise(ln.text) for ln in code.lines]
        hit = next((i for i, t in enumerate(texts) if anchor in t), None)
        if hit is None:
            continue
        joined = "\n".join(texts)
        if all(p in joined for p in parts):
            return code, code.lines[hit]
    return None


def corpus(files: tuple[CodeFile, ...], extra: tuple[str, ...] = ()) -> str:
    return normalise("\n".join([*(ln.text for f in files for ln in f.lines), *(f.path for f in files), *extra]))


def unknown_identifiers(text: str, norm_corpus: str) -> list[str]:
    """Backticked identifiers / paths / calls in `text` that do not appear in the input."""
    missing = []
    for token in _BACKTICKED.findall(text or ""):
        stripped = token.strip().removesuffix("()")
        if " " in stripped or not _IDENTIFIER.match(token.strip()) or "<<SECRET:" in stripped:
            continue
        if normalise(stripped) not in norm_corpus:
            missing.append(stripped)
    return missing


def reason_quotes_code(reason: str, norm_corpus: str) -> bool:
    """A downgrade needs a reason that quotes the code it relies on."""
    for groups in _QUOTED_IN_REASON.findall(reason or ""):
        fragment = normalise(next(g for g in groups if g))
        if fragment and fragment in norm_corpus:
            return True
    return False
