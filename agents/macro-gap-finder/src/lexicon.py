"""Pure: the word rules behind similarity. A fixed English stopword list (support greetings included) and a
light suffix stemmer, so "resetting", "resets" and "reset" count as one word."""

from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset((
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did",
    "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into",
    "is", "it", "its", "itself", "just", "let", "me", "more", "most", "my", "myself", "no", "nor", "not", "now",
    "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same",
    "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with",
    "would", "you", "your", "yours", "yourself", "yourselves", "also", "get", "got", "may", "might", "must",
    "shall", "us", "hi", "hello", "hey", "dear", "thanks", "thank", "please", "regards", "best", "cheers",
    "kind", "sincerely", "team", "support", "ok", "okay", "sure", "know", "hope", "help", "happy", "glad",
    "sorry", "apologies", "today", "yes", "yeah", "one", "two", "via", "per", "etc", "ll", "ve", "re",
))

_PLACEHOLDER = re.compile(r"\{\{\s*[a-z_]+\s*\}\}")
_NOT_WORD = re.compile(r"[^\w]+")
_SUFFIXES = ("ing", "ed", "es", "ly", "s")
MIN_STEM_CHARS = 3
MIN_TOKEN_CHARS = 2


def stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM_CHARS:
            return word[: -len(suffix)]
    return word


def content_tokens(text: str) -> list[str]:
    """NFKC, casefold, placeholders and punctuation out, stopwords out, stemmed, tokens under 2 chars out."""
    plain = _PLACEHOLDER.sub(" ", unicodedata.normalize("NFKC", text or "").casefold())
    words = (w.strip("_") for w in _NOT_WORD.sub(" ", plain).split())
    return [stem(w) for w in words if len(w) >= MIN_TOKEN_CHARS and w not in STOPWORDS]
