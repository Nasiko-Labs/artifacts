"""Pure: severity of a drifted attribute, sensitive-value redaction and the environment, all from names."""

from __future__ import annotations

import re
from typing import Any

from normalise import canon_key

Path = tuple[str | int, ...]

HIGH_KEYS = frozenset({
    "ingress", "egress", "cidrblocks", "ipv6cidrblocks", "policy", "role", "public", "publiclyaccessible",
    "encryption", "encrypted", "storageencrypted", "kmskeyid", "securitycontext", "hostnetwork", "privileged",
    "securitygroups", "vpcsecuritygroupids", "allowprivilegeescalation", "acl", "assumerolepolicy",
})
HIGH_PREFIXES = ("iam",)
MEDIUM_KEYS = frozenset({
    "replicas", "instanceclass", "instancetype", "size", "engineversion", "image", "resources", "env", "memory",
    "cpu", "allocatedstorage", "minsize", "maxsize", "desiredcapacity", "limits", "requests",
})
LOW_KEYS = frozenset({"tags", "labels", "annotations", "tagsall"})
RANK = {"high": 0, "medium": 1, "low": 2}
REDACTED = "[redacted]"
_SENSITIVE = re.compile(r"password|secret|token|key|credential|private", re.IGNORECASE)
_ENV_WORDS = (("prod", ("prod", "production", "prd")), ("staging", ("staging", "stage", "stg")),
              ("dev", ("dev", "development")))
_TOKEN = re.compile(r"[a-z0-9]+")


def _names(path: Path) -> list[str]:
    return [canon_key(p) for p in path if isinstance(p, str)]


def severity(path: Path) -> str:
    names = _names(path)
    if any(n in LOW_KEYS for n in names):
        return "low"
    if any(n in HIGH_KEYS or n.startswith(HIGH_PREFIXES) for n in names):
        return "high"
    if any(n in MEDIUM_KEYS for n in names):
        return "medium"
    return "low"


def is_tag_path(path: Path) -> bool:
    return any(n in LOW_KEYS for n in _names(path))


def is_sensitive(path: Path) -> bool:
    keys = [p for p in path if isinstance(p, str)]
    if any(canon_key(k) == "kmskeyid" for k in keys):
        return False  # a key id names a key; it is not secret material
    return any(_SENSITIVE.search(k) for k in keys)


def redact(path: Path, value: Any) -> Any:
    return REDACTED if value is not None and is_sensitive(path) else value


def environment(*hints: str) -> str:
    """`prod`, `staging`, `dev` or "" from a file path, address or namespace."""
    for hint in hints:
        tokens = set(_TOKEN.findall((hint or "").casefold()))
        for env, words in _ENV_WORDS:
            if tokens & set(words):
                return env
    return ""
