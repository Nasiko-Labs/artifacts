"""Pure: how two configuration values are made comparable. The model never decides whether values differ.

- keys compare without case, `_` or `-` (`cidr_blocks` = `CidrBlocks`);
- booleans compare without case (`True` = `true` = true); numbers compare by value (`"80"` = `80`);
- lists of scalars under a set-like key compare as sets; other lists compare in order;
- lists of maps pair their items by `name` / `port` / `id` (etc.) when every item has one.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

Path = tuple[str | int, ...]

SET_LIKE_KEYS = frozenset({
    "securitygroups", "vpcsecuritygroupids", "securitygroupids", "cidrblocks", "ipv6cidrblocks", "subnetids",
    "prefixlistids", "availabilityzones", "aliases", "sourcesecuritygroupids", "managedpolicyarns",
})
ITEM_KEYS = ("name", "containerport", "port", "id", "key", "sid")

# Never compared as extras: server-set or bookkeeping fields (a key the IaC sets is still compared).
IGNORED_KEYS = frozenset({
    "uid", "resourceversion", "creationtimestamp", "generation", "managedfields", "arn", "id", "tagsall",
    "clusterip", "clusterips", "dnspolicy", "terminationgraceperiodseconds", "selflink", "ownerid",
    "ownerreferences", "schedulername", "restartpolicy", "securitycontext", "progressdeadlineseconds",
    "revisionhistorylimit", "terminationmessagepath", "terminationmessagepolicy", "imagepullpolicy",
    "kubectl.kubernetes.io/lastappliedconfiguration", "deployment.kubernetes.io/revision", "ipfamilies",
    "ipfamilypolicy", "internaltrafficpolicy", "sessionaffinity",
})
TOP_LEVEL_IGNORED = frozenset({"status"})
_KEY_NOISE = re.compile(r"[_\-]")


def canon_key(key: Any) -> str:
    return _KEY_NOISE.sub("", str(key)).casefold()


def scalar(value: Any) -> Any:
    """A scalar's comparable form."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return _number(str(value))
    if isinstance(value, str):
        text = value.strip()
        lowered = text.casefold()
        if lowered in ("true", "false"):
            return lowered == "true"
        number = _number(text)
        return number if number is not None else text
    return value


def _number(text: str) -> Decimal | None:
    if not re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
        return None
    try:
        return Decimal(text).normalize()
    except InvalidOperation:
        return None


def is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def same_scalar(a: Any, b: Any) -> bool:
    return scalar(a) == scalar(b)


def same_set(a: list, b: list) -> bool:
    return sorted(map(repr, map(scalar, a))) == sorted(map(repr, map(scalar, b)))


def is_set_like(key: Any) -> bool:
    return canon_key(key) in SET_LIKE_KEYS


def item_key(items: list) -> str | None:
    """The field that identifies each map in a list, when every item carries one with distinct values."""
    if not items or not all(isinstance(i, dict) for i in items):
        return None
    for wanted in ITEM_KEYS:
        found = [next((k for k in i if canon_key(k) == wanted), None) for i in items]
        if all(found):
            values = [repr(scalar(i[k])) for i, k in zip(items, found, strict=True)]
            if len(set(values)) == len(values):
                return wanted
    return None


def lookup(mapping: dict, key: Any) -> tuple[bool, Any]:
    """`mapping[key]` matched by canonical key."""
    if key in mapping:
        return True, mapping[key]
    wanted = canon_key(key)
    for k, v in mapping.items():
        if canon_key(k) == wanted:
            return True, v
    return False, None


def is_ignored(key: Any, depth: int) -> bool:
    canon = canon_key(key)
    return canon in IGNORED_KEYS or (depth == 0 and canon in TOP_LEVEL_IGNORED)


def path_text(path: Path) -> str:
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            text = str(part)
            needs_quotes = not re.fullmatch(r"[\w\-]+", text)
            piece = f'["{text}"]' if needs_quotes else text
            out += piece if needs_quotes or not out else f".{piece}"
    return out
