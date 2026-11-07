"""Pure: the alert key an incident belongs to, from its title.

Incidents raised by the same alert rule differ only in the values the rule fills in (host names, pod
suffixes, ids, numbers). Those are replaced with `<*>`, so incidents sharing a key AND a service form
one alert. The first raw title in a group is shown verbatim as the alert's name.
"""

from __future__ import annotations

import re

WILDCARD = "<*>"

_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_HEX = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{6,}\b")
_HOST_SUFFIX = re.compile(r"(?<=[a-z0-9]-)(?=[a-z0-9]*\d)[a-z0-9]{5,10}\b")
_DIGITS = re.compile(r"\d+(?:\.\d+)?")
_REPEATED = re.compile(r"(?:<\*>[\s\-_.:/]*){2,}")
_SPACES = re.compile(r"\s+")


def alert_key(title: str) -> str:
    """`HighCPU on api-7f9c2d4b1a (10.0.3.4)` → `highcpu on api-<*> (<*>)`."""
    text = (title or "").casefold()
    for pattern in (_QUOTED, _UUID, _IP, _HEX, _HOST_SUFFIX, _DIGITS):
        text = pattern.sub(WILDCARD, text)
    text = _REPEATED.sub(lambda m: WILDCARD + (" " if m.group(0).endswith(" ") else ""), text)
    return _SPACES.sub(" ", text).strip()


def group_key(title: str, service: str) -> tuple[str, str]:
    """Same key on a different service stays a different alert."""
    return alert_key(title), (service or "").strip().casefold()


def name_tokens(name: str) -> tuple[str, ...]:
    """Words of an alert name worth searching for (no wildcards, no short words)."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", (name or "").replace(WILDCARD, " "))
    return tuple(dict.fromkeys(words))
