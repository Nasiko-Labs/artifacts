"""Pure: who the standup is for, and which code-host and tracker identities are the same person.

Two identities are linked only on an exact key: the same email, the same normalised display name
(lowercase, punctuation stripped, "Last, First" read as "First Last"), or the same handle. Nothing is
merged on a guess; a person found in one source but not the other is reported, and the user can fix
it ("maya is Maya Rao in the tracker"). A named person is found by their typed words appearing as
whole words in a handle, display name or email local part.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from records import Identity, Records

MAX_PEOPLE = 15
MAX_LABEL_CHARS = 60
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")
_UNSAFE = re.compile(r"[\[\]()<>*_`|#\\\x00-\x1f]")
_SPACES = re.compile(r"\s+")
_CORRECTION = re.compile(
    r"^\s*@?([\w.\-]+(?:\s+[\w.\-]+)?)\s+is\s+@?(.+?)\s+(?:in|on)\s+the\s+(tracker|code host)\s*\.?\s*$",
    re.IGNORECASE)


@dataclass(frozen=True)
class Person:
    label: str
    keys: frozenset[str]

    def owns(self, identity: Identity) -> bool:
        return bool(self.keys & keys_of(identity))


def clean_label(text: str) -> str:
    return _SPACES.sub(" ", _UNSAFE.sub(" ", text or "")).strip()[:MAX_LABEL_CHARS]


def tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in _TOKEN_SPLIT.split((text or "").casefold()) if t)


def normal_name(name: str) -> str:
    """"Rao, Maya" and "Maya Rao" both become "maya rao"."""
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    return " ".join(tokens(name))


def keys_of(identity: Identity) -> frozenset[str]:
    keys = set()
    if identity.email.strip():
        keys.add("e:" + identity.email.strip().casefold())
    if identity.handle.strip():
        keys.add("h:" + identity.handle.strip().lstrip("@").casefold())
    if normal_name(identity.name):
        keys.add("n:" + normal_name(identity.name))
    return frozenset(keys)


def all_identities(records: Records) -> tuple[Identity, ...]:
    found = [pr.author for pr in records.prs]
    found += [r.reviewer for pr in records.prs for r in pr.reviews]
    found += [c.author for c in records.commits]
    found += [i.assignee for i in records.issues]
    return tuple(found)


def groups(identities: Iterable[Identity]) -> list[tuple[frozenset[str], Identity]]:
    """Identities linked by shared keys, each with the identity used for its label."""
    linked: list[tuple[frozenset[str], Identity]] = []
    for identity in identities:
        keys = keys_of(identity)
        if not keys:
            continue
        hits = [g for g in linked if g[0] & keys]
        merged = keys.union(*(g[0] for g in hits))
        best = next((i for i in (*(g[1] for g in hits), identity) if i.name.strip()), identity)
        linked = [g for g in linked if g not in hits] + [(frozenset(merged), best)]
    return linked


def named_people(names: Sequence[str], records: Records, corrections: Mapping[str, Sequence[str]]) -> tuple[Person, ...]:
    """One person per typed name, with every identity linked to the ones their words match."""
    linked = groups(all_identities(records))
    people = []
    for name in names:
        label = clean_label(name)
        wanted = [tokens(name), *(tokens(alias) for alias in corrections.get(label.casefold(), ()))]
        extra = {f"n:{normal_name(a)}" for a in corrections.get(label.casefold(), ())}
        extra |= {f"h:{a.strip().lstrip('@').casefold()}" for a in corrections.get(label.casefold(), ())}
        keys = set(extra) | {f"t:{label.casefold()}"}
        for group_keys, _ in linked:
            if group_keys & extra or any(_words_match(w, group_keys) for w in wanted):
                keys |= group_keys
        people.append(Person(label=label, keys=frozenset(keys)))
    return tuple(people)


def everyone(records: Records) -> tuple[tuple[Person, ...], int]:
    """Everyone the records name, alphabetical, at most MAX_PEOPLE; and how many were left out."""
    people = sorted((Person(label=_label(best), keys=keys) for keys, best in groups(all_identities(records))),
                    key=lambda p: p.label.casefold())
    people = [p for p in people if p.label]
    return tuple(people[:MAX_PEOPLE]), max(0, len(people) - MAX_PEOPLE)


def parse_correction(message: str) -> tuple[str, str, str] | None:
    """("maya", "Maya Rao", "tracker") for "maya is Maya Rao in the tracker", else None."""
    found = _CORRECTION.match(message or "")
    if not found:
        return None
    return clean_label(found.group(1)), clean_label(found.group(2)), found.group(3).casefold()


def has_code_identity(person: Person, records: Records) -> bool:
    return any(person.owns(i) for i in (*(p.author for p in records.prs), *(c.author for c in records.commits)))


def has_tracker_identity(person: Person, records: Records) -> bool:
    return any(person.owns(i.assignee) for i in records.issues)


def _words_match(wanted: tuple[str, ...], group_keys: frozenset[str]) -> bool:
    if not wanted:
        return False
    for key in group_keys:
        kind, _, value = key.partition(":")
        if kind == "e":
            value = value.split("@", 1)[0]
        if set(wanted) <= set(tokens(value)):
            return True
    return False


def _label(identity: Identity) -> str:
    name = identity.name
    if "," in name:  # "Rao, Maya" is shown as "Maya Rao"
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}"
    return clean_label(name or identity.handle or identity.email.split("@", 1)[0])
