"""Pure: owner resolution and grouping.

Order: 1) a tag in the paste (owner, team, created_by, contact, project); 2) a code-host result, kept
only when its evidence quote and link and every handle, email and name it gives appear verbatim in this
turn's tool output; 3) unknown. A chat user found for a tag owner adds the chat id the message goes to.
Delivery is decided in code: chat when a chat user resolved, else email when an email is known, else
none (the draft is offered to copy). Groups are by owner key (case-folded), sorted by total monthly cost
descending, resources inside by cost descending."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from findings import UNKNOWN, Row

CODE_SOURCES = {"code_search": "code host search", "commit": "code host commit"}
_WS = re.compile(r"\s+")
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class CodeOwner:
    resource_id: str
    handle: str
    email: str
    display_name: str
    source: str
    evidence_link: str
    evidence_quote: str


@dataclass(frozen=True)
class Contact:
    name: str        # the tag owner or email it was looked up for
    chat_id: str
    email: str


@dataclass(frozen=True)
class Group:
    key: str
    owner: str
    source: str
    email: str
    chat_id: str
    rows: tuple[Row, ...]

    @property
    def total(self) -> Decimal:
        return sum((r.cost for r in self.rows if r.cost is not None), Decimal(0))

    @property
    def known(self) -> bool:
        return self.key != UNKNOWN

    @property
    def delivery(self) -> str:
        return "chat" if self.chat_id else "email" if self.email else "none"

    @property
    def label(self) -> str:
        return self.owner if self.known else "Unknown owner"


def load_json_object(text: str) -> dict | None:
    cleaned = _FENCE.sub("", (text or "").strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalise(text: str) -> str:
    return _WS.sub(" ", text or "").strip().casefold()


def seen_index(seen_text: str) -> str:
    """This turn's tool output, normalised, plus a JSON-unescaped copy (tool output is often a JSON string,
    where a quoted line reads `volume_id = \\"vol-1\\"`)."""
    unescaped = seen_text.replace('\\"', '"').replace("\\n", "\n").replace("\\/", "/")
    return normalise(seen_text) + "\n" + normalise(unescaped)


def _seen(value: str, seen: str) -> bool:
    return bool(value) and normalise(value) in seen


def parse_lookup(text: str) -> tuple[tuple[dict, ...], tuple[dict, ...]] | None:
    """The owner step's JSON: (owners, contacts), or None when it is not the agreed shape."""
    data = load_json_object(text)
    if data is None or not isinstance(data.get("owners", []), list) or not isinstance(data.get("contacts", []), list):
        return None
    owners = tuple(o for o in data.get("owners") or () if isinstance(o, dict))
    contacts = tuple(c for c in data.get("contacts") or () if isinstance(c, dict))
    return owners, contacts


def check_code_owners(raw: Sequence[Mapping[str, Any]], asked: Sequence[str],
                      seen_text: str) -> tuple[tuple[CodeOwner, ...], int]:
    """Only owners for resources that were asked about, with a verbatim quote, link and identity."""
    seen = seen_index(seen_text)
    kept: dict[str, CodeOwner] = {}
    dropped = 0
    for item in raw:
        owner = CodeOwner(resource_id=text_of(item.get("resource_id")), handle=text_of(item.get("handle")).lstrip("@"),
                          email=text_of(item.get("email")), display_name=text_of(item.get("display_name")),
                          source=text_of(item.get("source")), evidence_link=text_of(item.get("evidence_link")),
                          evidence_quote=text_of(item.get("evidence_quote")))
        if owner.resource_id not in asked or owner.resource_id in kept:
            continue
        identity = [v for v in (owner.handle, owner.email, owner.display_name) if v]
        if (owner.source not in CODE_SOURCES or not identity or not _seen(owner.evidence_quote, seen)
                or not _seen(owner.evidence_link, seen) or not all(_seen(v, seen) for v in identity)
                or (owner.email and not _EMAIL.match(owner.email))):
            dropped += 1
            continue
        kept[owner.resource_id] = owner
    return tuple(kept.values()), dropped


def check_contacts(raw: Sequence[Mapping[str, Any]], asked: Sequence[str], seen_text: str) -> tuple[Contact, ...]:
    """Chat users for names that were asked about, each id/email verbatim in tool output."""
    seen = seen_index(seen_text)
    wanted = {a.casefold(): a for a in asked}
    found: dict[str, Contact] = {}
    for item in raw:
        name = wanted.get(text_of(item.get("name")).casefold())
        chat_id = text_of(item.get("chat_id") or item.get("handle")).lstrip("@")
        email = text_of(item.get("email"))
        if name is None or name.casefold() in found:
            continue
        chat_id = chat_id if _seen(chat_id, seen) else ""
        email = email if _seen(email, seen) and _EMAIL.match(email) else ""
        if chat_id or email:
            found[name.casefold()] = Contact(name, chat_id, email)
    return tuple(found.values())


def apply_lookup(rows: Sequence[Row], owners: Sequence[CodeOwner], contacts: Sequence[Contact]) -> tuple[Row, ...]:
    by_resource = {o.resource_id: o for o in owners}
    by_name = {c.name.casefold(): c for c in contacts}
    out: tuple[Row, ...] = ()
    for row in rows:
        if not row.known_owner and row.resource_id in by_resource:
            row = _from_code(row, by_resource[row.resource_id])
        contact = by_name.get(row.owner.casefold()) or (by_name.get(row.email.casefold()) if row.email else None)
        if row.known_owner and contact is not None:
            row = replace(row, chat_id=row.chat_id or contact.chat_id, email=row.email or contact.email)
        out = (*out, row)
    return out


def _from_code(row: Row, owner: CodeOwner) -> Row:
    who = owner.display_name or owner.email or owner.handle
    key = (owner.email or owner.handle or owner.display_name).casefold()
    return replace(row, owner=who, owner_key=key, owner_source=CODE_SOURCES[owner.source], email=owner.email,
                   code_handle=owner.handle, evidence_link=owner.evidence_link, evidence_quote=owner.evidence_quote)


def lookup_candidates(rows: Sequence[Row], limit: int) -> tuple[Row, ...]:
    """Unknown-owner rows to search the code host for: the most expensive first, at most `limit`."""
    unknown = [r for r in rows if not r.known_owner]
    return tuple(sorted(unknown, key=lambda r: (-(r.cost or Decimal(0)), r.resource_id))[:limit])


def contact_candidates(rows: Sequence[Row]) -> tuple[str, ...]:
    """Tag owners with no chat id to look up in chat (by name, or by email when the tag is one)."""
    return tuple(dict.fromkeys(r.owner for r in rows if r.known_owner and not r.chat_id))


def group(rows: Sequence[Row], owner_filter: str = "") -> tuple[tuple[Group, ...], tuple[Row, ...]]:
    """(groups by total desc, the rows renumbered in that order). `owner_filter` keeps one owner's rows."""
    wanted = owner_filter.casefold()
    kept = [r for r in rows if not wanted or wanted in r.owner.casefold() or wanted in r.email.casefold()]
    buckets: dict[str, list[Row]] = {}
    for r in kept:
        buckets.setdefault(r.owner_key, []).append(r)
    groups = [_group(key, members) for key, members in buckets.items()]
    groups.sort(key=lambda g: (-g.total, not g.known, g.label.casefold()))
    numbered: tuple[Row, ...] = ()
    final: tuple[Group, ...] = ()
    for g in groups:
        ordered = sorted(g.rows, key=lambda r: (-(r.cost or Decimal(-1)), r.resource_id))
        renumbered = tuple(replace(r, id=str(len(numbered) + i + 1)) for i, r in enumerate(ordered))
        numbered = (*numbered, *renumbered)
        final = (*final, replace(g, rows=renumbered))
    return final, numbered


def regroup(rows: Sequence[Row]) -> tuple[Group, ...]:
    """Groups of already-numbered rows, in the shown order (no renumbering)."""
    buckets: dict[str, list[Row]] = {}
    for r in rows:
        buckets.setdefault(r.owner_key, []).append(r)
    return tuple(_group(key, members) for key, members in buckets.items())


def _group(key: str, members: Sequence[Row]) -> Group:
    first = members[0]
    chat_id = next((r.chat_id for r in members if r.chat_id), "")
    email = next((r.email for r in members if r.email), "")
    return Group(key=key, owner=first.owner if key != UNKNOWN else "", source=first.owner_source, email=email,
                 chat_id=chat_id, rows=tuple(members))
