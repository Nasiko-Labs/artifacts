"""Pure: link one person's evidence across sources, and drop bots.

Linking order: the same email (case-insensitive) is the same person; then the same login on the same
source; then the same normalised name (lowercase, punctuation stripped, first + last word). A name that
matches two different people (same name, different emails) is never merged and the people are flagged
`ambiguous`, so the reply asks a human to check instead of silently guessing."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from models import Evidence, Who

_BOT = re.compile(r"(?:\[bot\]$|(?:^|[-_. ])bot$|^bot[-_. ]|dependabot|renovate)", re.IGNORECASE)
_NOT_NAME = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class Person:
    name: str
    emails: tuple[str, ...] = ()
    handles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    ambiguous: bool = False

    @property
    def sources(self) -> frozenset[str]:
        return frozenset(e.source for e in self.evidence)


def is_bot(who: Who) -> bool:
    local = who.email.split("@", 1)[0]
    return any(_BOT.search(value) for value in (who.name, who.handle, local) if value)


def norm_name(name: str) -> str:
    words = _NOT_NAME.sub(" ", name or "").casefold().split()
    return " ".join((words[0], words[-1])) if len(words) > 1 else (words[0] if words else "")


def link(evidence: Iterable[Evidence]) -> tuple[Person, ...]:
    items = [e for e in evidence if not is_bot(e.who)]
    groups: dict[str, list[Evidence]] = {}
    flagged: set[str] = set()
    for item in items:
        if item.who.email:
            groups.setdefault(f"email:{item.who.email.casefold()}", []).append(item)
    flagged |= _same_name_different_email(groups)
    for item in (i for i in items if not i.who.email):
        key, ambiguous = _key_for(item, groups)
        groups.setdefault(key, []).append(item)
        if ambiguous:
            flagged.add(key)
    return tuple(_person(items_, key in flagged) for key, items_ in groups.items())


def _key_for(item: Evidence, groups: Mapping[str, Sequence[Evidence]]) -> tuple[str, bool]:
    """(group key, ambiguous): an existing person by login, else by name, else a new one."""
    who = item.who
    if who.handle:
        by_handle = [k for k, g in groups.items() if any(_same_handle(e, item) for e in g)]
        if len(by_handle) == 1:
            return by_handle[0], False
    name = norm_name(who.name)
    if name:
        by_name = [k for k, g in groups.items() if k.startswith("email:") and any(norm_name(e.who.name) == name
                                                                                     for e in g)]
        if len(by_name) == 1:
            return by_name[0], False
        if len(by_name) > 1:
            return f"ambiguous:{name}", True
        return f"name:{name}", False
    return f"handle:{item.source}:{who.handle.casefold()}", False


def _same_handle(a: Evidence, b: Evidence) -> bool:
    return bool(a.who.handle) and a.source == b.source and a.who.handle.casefold() == b.who.handle.casefold()


def _same_name_different_email(groups: Mapping[str, Sequence[Evidence]]) -> set[str]:
    by_name: dict[str, list[str]] = {}
    for key, items in groups.items():
        for name in {norm_name(e.who.name) for e in items if e.who.name}:
            by_name.setdefault(name, []).append(key)
    return {key for keys in by_name.values() if len(set(keys)) > 1 for key in keys}


def _person(items: Sequence[Evidence], ambiguous: bool) -> Person:
    names = Counter(e.who.name for e in items if e.who.name)
    handles: dict[str, tuple[str, ...]] = {}
    for item in items:
        if item.who.handle and item.who.handle not in handles.get(item.source, ()):
            handles = {**handles, item.source: (*handles.get(item.source, ()), item.who.handle)}
    by_case = {e.who.email.casefold(): e.who.email for e in reversed(items) if e.who.email}
    emails = tuple(dict.fromkeys(by_case[e.who.email.casefold()] for e in items if e.who.email))
    fallback = next((h for hs in handles.values() for h in hs), "") or (emails[0] if emails else "unknown")
    name = names.most_common(1)[0][0] if names else fallback
    return Person(name, emails, handles, tuple(items), ambiguous)


def is_me(person: Person, me: Who) -> bool:
    if me.email and me.email.casefold() in {e.casefold() for e in person.emails}:
        return True
    if me.handle and me.handle.casefold() in {h.casefold() for hs in person.handles.values() for h in hs}:
        return True
    return bool(me.name) and norm_name(me.name) == norm_name(person.name)


def without(people: Sequence[Person], me: Who | None) -> tuple[Person, ...]:
    return tuple(people) if me is None else tuple(p for p in people if not is_me(p, me))

