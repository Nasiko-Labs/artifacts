"""Pure: who the packets are for, and exact identity matching.

People are the ones the user named (at most 8), else the authors of this turn's merged pull requests
(bots dropped, at most 10, alphabetical). A record belongs to a person only on an exact,
case-insensitive whole-word match of a handle, name or email local part: "Priya" matches "Priya
Natarajan" and "priya-n", never "Priyanka". Never fuzzy, never a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from records import Person
from request import clean_label, tokens

MAX_INFERRED = 10


@dataclass(frozen=True)
class Author:
    """How a tool named someone (values as returned)."""

    handle: str = ""
    name: str = ""
    email: str = ""

    def values(self) -> tuple[str, ...]:
        local = self.email.split("@", 1)[0] if "@" in self.email else self.email
        return tuple(v.strip() for v in (self.handle, self.name, local) if v and v.strip())

    def known(self) -> tuple[tuple[str, str], ...]:
        return tuple((k, v.strip()) for k, v in (("handle", self.handle), ("name", self.name)) if v and v.strip())


@dataclass(frozen=True)
class People:
    people: tuple[Person, ...]
    named: bool
    left_out: int = 0

    def labels(self) -> tuple[str, ...]:
        return tuple(p.label for p in self.people)

    def replay_people(self) -> tuple[tuple[str, dict[str, str]], ...]:
        return tuple((p.label, dict(p.known)) for p in self.people)

    def listing(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple((p.label, tuple(dict.fromkeys(v for _, v in p.known if v != p.label))) for p in self.people)


def is_bot(author: Author) -> bool:
    handle = author.handle.casefold()
    return handle.endswith("[bot]") or "bot" in tokens(handle) or "bot" in tokens(author.name)


def person_for(author: Author, people: Sequence[Person]) -> Person | None:
    """The one person this author is, or None (no match, or more than one person matches)."""
    hits = [p for p in people if any(_matches(alias, value) for alias in p.aliases for value in author.values())]
    return hits[0] if len(hits) == 1 else None


def mentions(text: str, person: Person) -> bool:
    """The text names the person by a whole-word alias."""
    words = f" {' '.join(tokens(text))} "
    return any(f" {' '.join(tokens(alias))} " in words for alias in person.aliases if tokens(alias))


def named(names: Sequence[str]) -> People:
    return People(tuple(Person(label=n, aliases=frozenset({n.lstrip("@").casefold()}),
                               known=(("label", n.lstrip("@")),)) for n in names), named=True)


def with_handles(people: People, seen_authors: Mapping[str, Iterable[Author]]) -> People:
    """Add the handles and names tools used for each person, for the chat step and replay."""
    out = []
    for p in people.people:
        extra = [kv for a in seen_authors.get(p.label, ()) for kv in a.known()]
        aliases = p.aliases | {v.casefold() for _, v in extra}
        out.append(replace(p, aliases=frozenset(aliases), known=tuple(dict.fromkeys((*p.known, *extra)))))
    return replace(people, people=tuple(out))


def inferred(authors: Iterable[Author]) -> People:
    by_key: dict[str, Author] = {}
    for a in authors:
        if is_bot(a) or not a.values():
            continue
        key = (a.handle or a.name or a.email).strip().casefold()
        current = by_key.get(key)
        by_key[key] = a if current is None or (a.name and not current.name) else current
    people = sorted((Person(label=clean_label(a.name or a.handle or a.email.split("@", 1)[0]),
                            aliases=frozenset(v.casefold() for v in a.values()),
                            known=(("label", clean_label(a.name or a.handle)), *a.known()), named=False)
                     for a in by_key.values()), key=lambda p: p.label.casefold())
    people = [p for p in people if p.label]
    return People(tuple(people[:MAX_INFERRED]), named=False, left_out=max(0, len(people) - MAX_INFERRED))


def _matches(alias: str, value: str) -> bool:
    wanted, have = tokens(alias), tokens(value)
    if not wanted or not have:
        return False
    if wanted == have:
        return True
    return len(wanted) == 1 and wanted[0] in have
