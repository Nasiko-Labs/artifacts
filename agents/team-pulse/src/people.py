"""Pure: who the team is, with no roster.

Either the people the user named (each name checked to appear in the user's own message), or the
people active in this turn's code-host and tracker records in the current window, at most
MAX_PEOPLE, in alphabetical order (never by any metric). Identities from different tools are merged
only on an exact (case-insensitive) match of a handle, name, email or email local part: a guess
would put one person's activity on another's card.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from zoneinfo import ZoneInfo

from records import Identity, Person

MAX_PEOPLE = 10
MAX_LABEL_CHARS = 60
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")
_UNSAFE = re.compile(r"[\[\]()<>*_`|#\\\x00-\x1f]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Team:
    people: tuple[Person, ...]
    named: bool  # True: the user named them; False: inferred from activity
    left_out: int = 0  # active people beyond MAX_PEOPLE

    def labels(self) -> tuple[str, ...]:
        return tuple(p.label for p in self.people)

    def replay_people(self) -> tuple[tuple[str, dict[str, str]], ...]:
        return tuple((p.label, dict(p.known)) for p in self.people)

    def chat_listing(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Label and the other names the tools used for each person (for the chat step's prompt)."""
        return tuple((p.label, tuple(dict.fromkeys(v for k, v in p.known if k != "label" and v != p.label)))
                     for p in self.people)

    def person_of(self, identity: Identity) -> str | None:
        aliases = identity.aliases()
        return next((p.label for p in self.people if p.aliases & aliases), None)

    def with_zones(self, zones: dict[str, ZoneInfo]) -> Team:
        return replace(self, people=tuple(replace(p, tz=zones.get(p.label, p.tz)) for p in self.people))


def clean_label(text: str) -> str:
    """A display-safe label: no markup characters, one line, bounded."""
    return _SPACES.sub(" ", _UNSAFE.sub(" ", text or "")).strip()[:MAX_LABEL_CHARS]


def is_bot(identity: Identity) -> bool:
    handle = identity.handle.casefold()
    return handle.endswith("[bot]") or "bot" in _tokens(handle) or "bot" in _tokens(identity.name.casefold())


def named_in_message(names: Iterable[object], message: str) -> tuple[str, ...]:
    """The names the model says the user named, kept only when they appear in the message as words."""
    text = f" {' '.join(_tokens(message.casefold()))} "
    kept: list[str] = []
    for raw in names:
        if not isinstance(raw, str):
            continue
        tokens = _tokens(raw.casefold())
        label = clean_label(raw)
        if tokens and label and f" {' '.join(tokens)} " in text and label not in kept:
            kept.append(label)
    return tuple(kept[:MAX_PEOPLE])


def named_team(names: Sequence[str], identities: Iterable[Identity]) -> Team:
    """One person per name; each picks up the aliases of every identity whose words contain the name's."""
    pool = tuple(identities)
    people = []
    for name in names:
        wanted = _tokens(name.casefold())
        matched = [i for i in pool if _names_match(wanted, i)]
        aliases = frozenset({name.casefold()}.union(*(i.aliases() for i in matched)))
        known = (("label", name), *(matched[0].known() if matched else ()))
        people.append(Person(label=name, aliases=aliases, known=known))
    return Team(tuple(people), named=True)


def inferred_team(identities: Iterable[Identity]) -> Team:
    groups: list[set[str]] = []
    shown: list[Identity] = []
    for identity in identities:
        aliases = set(identity.aliases())
        if not aliases or is_bot(identity):
            continue
        hits = [i for i, g in enumerate(groups) if g & aliases]
        merged = aliases.union(*(groups[i] for i in hits))
        best = _better(identity, *(shown[i] for i in hits))
        groups = [g for i, g in enumerate(groups) if i not in hits] + [merged]
        shown = [s for i, s in enumerate(shown) if i not in hits] + [best]
    people = sorted((Person(label=_label(s), aliases=frozenset(g), known=(("label", _label(s)), *s.known()))
                     for g, s in zip(groups, shown, strict=True)),
                    key=lambda p: p.label.casefold())
    people = [p for p in people if p.label]
    return Team(tuple(people[:MAX_PEOPLE]), named=False, left_out=max(0, len(people) - MAX_PEOPLE))


def _better(*identities: Identity) -> Identity:
    """Prefer an identity with a display name for the label."""
    return next((i for i in identities if i.name.strip()), identities[0])


def _label(identity: Identity) -> str:
    return clean_label(identity.name or identity.handle or identity.email.split("@", 1)[0])


def _names_match(wanted: tuple[str, ...], identity: Identity) -> bool:
    if not wanted:
        return False
    for value in (identity.handle, identity.name, identity.email.split("@", 1)[0]):
        tokens = set(_tokens(value.casefold()))
        if tokens and set(wanted) <= tokens:
            return True
    return False


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in _TOKEN_SPLIT.split(text) if t)
