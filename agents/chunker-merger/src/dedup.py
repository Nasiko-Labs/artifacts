"""Pure: conservative de-duplication of one merged list. Two items are duplicates only when their key
text is equal after normalisation and their tie fields (owner, due) are equal too; the first stays.
Items that share at least 90% of their words are kept and only reported as possible duplicates."""

from __future__ import annotations

import json
import string
import unicodedata
from dataclasses import dataclass

KEY_FIELDS = ("quote", "source_quote", "text", "task", "what", "story", "decision", "translation", "name")
TIE_FIELDS = ("owner", "due")
IGNORED_FIELDS = frozenset({"part", "rank"})
NEAR_SHARE = 0.9
MAX_PAIRWISE = 500  # items beyond this are not compared for near-duplicates


@dataclass(frozen=True)
class Deduped:
    items: tuple
    removed: int
    possible_pairs: int


def normalise(value: str) -> str:
    text = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return text.strip(string.punctuation + " ")


def key_text(item) -> str:
    if isinstance(item, dict):
        for name in KEY_FIELDS:
            if isinstance(item.get(name), str) and item[name].strip():
                return normalise(item[name])
        rest = {k: v for k, v in item.items() if k not in IGNORED_FIELDS}
        return json.dumps(rest, sort_keys=True, ensure_ascii=False)
    if isinstance(item, str):
        return normalise(item)
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def identity(item) -> tuple:
    ties = ()
    if isinstance(item, dict):
        ties = tuple((name, normalise(str(item[name]))) for name in TIE_FIELDS if item.get(name) is not None)
    return key_text(item), ties


def dedup(items: list) -> Deduped:
    seen: set[tuple] = set()
    kept = []
    for item in items:
        ident = identity(item)
        if ident in seen:
            continue
        seen.add(ident)
        kept.append(item)
    return Deduped(tuple(kept), len(items) - len(kept), _possible_pairs(kept))


def _possible_pairs(items: list) -> int:
    words = [set(key_text(item).split()) for item in items[:MAX_PAIRWISE]]
    keys = [key_text(item) for item in items[:MAX_PAIRWISE]]
    pairs = 0
    for i in range(len(words)):
        for j in range(i + 1, len(words)):
            if keys[i] == keys[j] or not words[i] or not words[j]:
                continue
            if len(words[i] & words[j]) / max(len(words[i]), len(words[j])) >= NEAR_SHARE:
                pairs += 1
    return pairs
