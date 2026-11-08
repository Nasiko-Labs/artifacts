"""Pure: whether a title or body mentions the launch. `launch_terms` = the casefolded name tokens of at least
MIN_TOKEN_CHARS; a title mentions the launch when every token is in it, a body when at least
BODY_MENTION_SHARE of them are."""

from __future__ import annotations

from config import BODY_MENTION_SHARE, MIN_TOKEN_CHARS

_PUNCT = "\"'()[]{}.,;:!?“”‘’"


def launch_terms(name: str | None) -> tuple[str, ...]:
    tokens = tuple(dict.fromkeys(t.strip(_PUNCT).casefold() for t in (name or "").split()))
    long = tuple(t for t in tokens if len(t) >= MIN_TOKEN_CHARS)
    whole = (name or "").strip().casefold()
    return long or ((whole,) if whole else ())


def in_title(title: str | None, terms: tuple[str, ...]) -> bool:
    folded = (title or "").casefold()
    return bool(terms) and all(t in folded for t in terms)


def in_body(body: str | None, terms: tuple[str, ...]) -> bool:
    folded = (body or "").casefold()
    if not terms or not folded:
        return False
    return sum(t in folded for t in terms) >= BODY_MENTION_SHARE * len(terms)


def mentions(title: str | None, body: str | None, terms: tuple[str, ...]) -> bool:
    return in_title(title, terms) or in_body(body, terms)
