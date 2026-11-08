"""Pure: who an access holder is on the roster. The primary key is the lowercased email. A code-host
login is matched, in order, by (1) the roster's handle column, (2) a mapping the user typed in chat,
(3) the profile's public email, (4) exact normalised-name equality with a roster name, which is
flagged `weak`. A weak or missing match never produces a removal draft."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from parse import RosterRow
from records import Profile

SERVICE_PATTERNS = re.compile(r"^svc[-_.]|[-_.]svc\b|\bbot\b|bot$|\[bot\]|automation|noreply|no-reply|\bci[-_]",
                              re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    person: RosterRow | None
    how: str = ""          # handle | mapping | email | profile email | name | ""
    weak: bool = False
    email: str = ""        # the email this holder is known by ("" when none)


class Roster:
    """Lookups over the parsed roster rows."""

    def __init__(self, rows: Sequence[RosterRow]):
        self.rows = tuple(rows)
        self._by_email = {r.email: r for r in rows if r.email}
        self._by_handle = {r.handle: r for r in rows if r.handle}
        names: dict[str, list[RosterRow]] = {}
        for r in rows:
            if r.name:
                names.setdefault(normal_name(r.name), []).append(r)
        self._by_name = {k: v[0] for k, v in names.items() if len(v) == 1}  # an ambiguous name never matches

    @property
    def present(self) -> bool:
        return bool(self.rows)

    def by_email(self, email: str) -> RosterRow | None:
        return self._by_email.get(email.casefold()) if email else None

    def by_name(self, name: str) -> RosterRow | None:
        return self._by_name.get(normal_name(name)) if name else None

    def by_handle(self, login: str) -> RosterRow | None:
        return self._by_handle.get(login.casefold())


def normal_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    if "," in text:  # "Lee, Dana" → "dana lee"
        last, _, first = text.partition(",")
        text = f"{first} {last}"
    return " ".join(re.sub(r"[^a-z ]+", " ", text).split())


def is_service_account(*labels: str) -> bool:
    return any(SERVICE_PATTERNS.search(label or "") for label in labels)


def match_row(roster: Roster, email: str, name: str) -> Match:
    """An export row: by email (strong), else by exact name (weak)."""
    if email:
        person = roster.by_email(email)
        return Match(person, "email" if person else "", False, email)
    person = roster.by_name(name)
    return Match(person, "name" if person else "", bool(person), "")


def match_login(roster: Roster, login: str, mappings: Mapping[str, str], profile: Profile | None) -> Match:
    """A code-host login, through the four tiers above."""
    person = roster.by_handle(login)
    if person:
        return Match(person, "handle", False, person.email)
    mapped = mappings.get(login.casefold(), "")
    if mapped:
        return Match(roster.by_email(mapped), "mapping", False, mapped)
    if profile and profile.email:
        person = roster.by_email(profile.email)
        return Match(person, "profile email" if person else "", False, profile.email)
    if profile and profile.name:
        person = roster.by_name(profile.name)
        if person:
            return Match(person, "name", True, "")
    return Match(None)
