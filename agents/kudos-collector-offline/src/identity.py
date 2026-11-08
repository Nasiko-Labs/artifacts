"""Pure: who a name refers to. Names exist only as written; there is no directory.

A person key is (1) the alias-mapped canonical name when a handle or name is in the alias map, else (2) the
normalised full name (two or more tokens), else (3) the handle as written (`@sam`, or a PR login), else (4) a
first-name-only key that is NEVER merged with anyone: "Priya (first name only; unmerged)". Two full names with
different last names never share a key."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from aliases import handle_key

HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof", "sir"})
ALIAS, FULL, HANDLE, FIRST = "alias", "full", "handle", "first_name"
_PUNCT = re.compile(r"[^\w\s'-]+")
_LOGIN = re.compile(r"^@?[a-z0-9][\w.-]*$")
FIRST_NAME_LABEL = " (first name only; unmerged)"


@dataclass(frozen=True)
class Identity:
    key: str
    name: str
    kind: str

    @property
    def ambiguous(self) -> bool:
        return self.kind == FIRST


def normalise(name: str) -> str:
    tokens = _PUNCT.sub(" ", (name or "").replace("@", " ").casefold()).split()
    return " ".join(t.strip("'-") for t in tokens if t.strip("'-") and t not in HONORIFICS)


def display(name: str) -> str:
    tokens = (name or "").replace("@", " ").split()
    return " ".join(t for t in tokens if t.casefold().strip(".") not in HONORIFICS).strip(" ,;:")


def resolve(raw: str, aliases: dict[str, str], is_login: bool = False) -> Identity | None:
    """The identity a name refers to, or None when it is empty. `is_login` marks a PR author field, where
    a single lowercase token is a code-host login, i.e. a handle."""
    text = " ".join((raw or "").split()).strip(" ,;:")
    if not text or not normalise(text):
        return None
    mapped = aliases.get(handle_key(text))
    if mapped:
        return Identity(normalise(mapped), display(mapped), ALIAS)
    if text.startswith("@") or (is_login and _LOGIN.match(text) and " " not in text):
        handle = handle_key(text)
        return Identity("@" + handle, "@" + text.lstrip("@"), HANDLE)
    norm = normalise(text)
    if len(norm.split()) >= 2:
        return Identity(norm, display(text), FULL)
    return Identity("first:" + norm, display(text) + FIRST_NAME_LABEL, FIRST)


def matches(query: str, name: str, handles: Iterable[str] = ()) -> bool:
    """Exact match on the normalised full name or one of the person's handles (People: option)."""
    q = normalise(query)
    return bool(q) and (q == normalise(name.removesuffix(FIRST_NAME_LABEL))
                        or q in {handle_key(h) for h in handles})


def loosely_matches(query: str, name: str, handles: Iterable[str] = ()) -> bool:
    """Follow-up filter ("only Priya"): every query token is a token of the name, or a handle matches."""
    q = normalise(query).split()
    tokens = normalise(name.removesuffix(FIRST_NAME_LABEL)).split()
    return bool(q) and (all(t in tokens for t in q) or matches(query, name, handles))


def appears_in(name: str, text: str, aliases: dict[str, str]) -> bool:
    """A credited name must be written in the record: as normalised words, or as an `@handle` (or alias
    handle) mapped to it."""
    norm, hay = normalise(name), f" {normalise(text)} "
    if norm and f" {norm} " in hay:
        return True
    for handle, canonical in aliases.items():
        if normalise(canonical) == norm and f" {normalise(handle)} " in hay:
            return True
    return False
