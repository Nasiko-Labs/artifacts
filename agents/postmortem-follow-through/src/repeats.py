"""Pure: has the incident a postmortem was about happened again, and which past postmortems does a new
incident match. Title similarity is a Jaccard over content words; services are compared normalised."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dates import parse_date
from records import Incident, Postmortem

SERVICE_SIM = 0.3     # same service on both sides
NO_SERVICE_SIM = 0.5  # no service on either side
TOP_MATCHES = 3

_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]*")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")
STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "after", "before", "during", "was", "were", "are", "has",
    "had", "have", "not", "but", "our", "this", "that", "via", "due", "all", "out", "off", "its", "per",
    "postmortem", "post", "mortem", "post-mortem", "review", "rca", "incident", "incidents", "sev",
    "issue", "issues", "follow", "action", "items", "root", "cause", "analysis", "today", "todays",
    "today's", "new", "old", "does", "match", "matches", "any",
})


def tokens(title: str) -> frozenset[str]:
    """Lower-case words of 3+ characters, minus stop words, numbers and ids (anything with a digit)."""
    words = _WORD.findall((title or "").casefold())
    return frozenset(w.strip("'-_") for w in words
                     if len(w.strip("'-_")) >= 3 and w.strip("'-_") not in STOP_WORDS and not any(c.isdigit() for c in w))


def similarity(a: str, b: str) -> float:
    left, right = tokens(a), tokens(b)
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def service_key(service: str | None) -> str:
    return _NOT_ALNUM.sub("", (service or "").casefold())


def is_match(postmortem: Postmortem, title: str, service: str | None) -> bool:
    """Both sides name a service: it must be the same and similarity >= 0.3. Otherwise (a side has no
    service) similarity >= 0.5."""
    sim = similarity(postmortem.title, title)
    left, right = service_key(postmortem.service), service_key(service)
    if left and right:
        return left == right and sim >= SERVICE_SIM
    return sim >= NO_SERVICE_SIM


@dataclass(frozen=True)
class Repeat:
    incident_id: str
    on: str  # YYYY-MM-DD


def repeats_by_postmortem(postmortems: Sequence[Postmortem], incidents: Sequence[Incident]) -> dict[str, Repeat]:
    """The earliest incident created after each postmortem that matches it."""
    found: dict[str, Repeat] = {}
    for pm in postmortems:
        dated = [(parse_date(i.created_at), i) for i in incidents]
        later = sorted(((d, i) for d, i in dated if d is not None and d > pm.created and i.id != pm.incident_ref),
                       key=lambda pair: (pair[0], pair[1].id))
        hit = next((i for d, i in later if is_match(pm, i.title, i.service)), None)
        if hit is not None:
            found[pm.id] = Repeat(hit.id, parse_date(hit.created_at).isoformat())
    return found


def best_matches(postmortems: Sequence[Postmortem], title: str, service: str | None) -> tuple[Postmortem, ...]:
    """Top 3 postmortems the incident matches, same service first, then by similarity."""
    hits = [pm for pm in postmortems if is_match(pm, title, service)]
    ranked = sorted(hits, key=lambda pm: (service_key(pm.service) != service_key(service) or not service_key(service),
                                          -similarity(pm.title, title), pm.id))
    return tuple(ranked[:TOP_MATCHES])
