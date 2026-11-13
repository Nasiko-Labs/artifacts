"""Pure: is a message after an explanation clearly about that error? (Mirrors the Rust twin's relevance.rs.)

A trace in the history does not make every later message a follow-up: "what's 2+2?" is unrelated and must be
declined. A question that uses error vocabulary ("fix", "why", "line") or names something from the trace
(`KeyError`, `get_order`, `service.py`) or from the last answer (a term of the explanation or fix, e.g.
`defaultdict`) is clearly a follow-up and skips the relatedness check. Anything else gets one cheap check that
leans towards "related" and declines only what is clearly unrelated."""

from __future__ import annotations

import re

ERROR_WORDS = frozenset([
    "fix", "fixed", "fixing", "fixes", "error", "errors", "exception", "exceptions", "crash", "crashes",
    "crashed", "crashing", "bug", "bugs", "trace", "traceback", "stack", "line", "lines", "cause", "caused",
    "causes", "causing", "why", "verify", "test", "tests", "testing", "repro", "reproduce", "debug",
    "debugging", "happen", "happens", "happened", "happening", "mean", "means", "meaning", "explain",
    "explanation", "code", "function", "method", "file", "frame", "fail", "fails", "failed", "failing",
    "failure", "solve", "solution", "prevent", "avoid", "workaround", "patch", "change", "wrong", "broken",
    "issue", "problem", "null", "none", "nil", "undefined", "missing", "throw", "throws", "thrown", "raise",
    "raises", "raised", "handle", "catch", "confidence", "sure", "certain", "elaborate", "detail", "details",
    "example",
])
STOP_WORDS = frozenset([
    "the", "and", "for", "what", "whats", "how", "who", "with", "from", "this", "that", "are", "you", "can",
    "does", "did", "was", "were", "has", "have", "its", "into", "your", "about", "most", "recent", "call",
    "last", "not", "but", "all", "any", "out",
])
MIN_TOKEN_CHARS = 3
MIN_REPLY_TERM_CHARS = 5     # the last answer's prose has many short common words
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokens(text: str) -> list[str]:
    found = (m.group().casefold() for m in _TOKEN.finditer(text or ""))
    return [t for t in found if len(t) >= MIN_TOKEN_CHARS or t in ERROR_WORDS]


def prose_lines(reply: str) -> list[str]:
    """The reply without its italic note lines ("_Analysis only: ..._")."""
    return [line for line in (reply or "").splitlines() if not line.lstrip().startswith("_")]


def clearly_about(question: str, trace: str, last_reply: str) -> bool:
    terms = {t for t in _tokens(trace) if t not in STOP_WORDS}
    for line in prose_lines(last_reply):
        terms |= {t for t in _tokens(line) if len(t) >= MIN_REPLY_TERM_CHARS and t not in STOP_WORDS}
    return any(t in ERROR_WORDS or (t not in STOP_WORDS and t in terms) for t in _tokens(question))
