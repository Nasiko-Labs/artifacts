"""Pure: the risk class of each change, from a visible table (the model never rates risk).

    base:     destroy 5, replace 4, update 2, create 1, tag-only 0
    category: stateful +3 on destroy/replace, +1 on update; network +2 on destroy/replace/update;
              identity +2 on any non-create change
    flags:    +1 each, at most +2: an update touching engine_version, instance_class, cidr_block, ingress,
              egress, policy or assume_role_policy; deletion_protection changed to false; skip_final_snapshot = true
    level:    >= 7 critical, 5-6 high, 3-4 medium, else low
"""

from __future__ import annotations

from dataclasses import dataclass

import tf_types
from plan_model import Change

BASE = {"delete": 5, "replace": 4, "update": 2, "create": 1}
ACTION_WORD = {"delete": "destroy", "replace": "replace", "update": "update", "create": "create"}
STATEFUL_ADD = {"delete": 3, "replace": 3, "update": 1}
NETWORK_ADD = 2
IDENTITY_ADD = 2
MAX_FLAGS = 2
CRITICAL, HIGH, MEDIUM = 7, 5, 3
LEVEL_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class Risk:
    score: int
    level: str
    parts: tuple[str, ...]   # "destroy 5", "stateful 3", ...
    flags: tuple[str, ...]   # attribute flags that added to the score
    checks: tuple[str, ...]  # lines for "Check before apply"

    @property
    def parts_text(self) -> str:
        return " + ".join(self.parts)


def level_of(score: int) -> str:
    if score >= CRITICAL:
        return "critical"
    if score >= HIGH:
        return "high"
    if score >= MEDIUM:
        return "medium"
    return "low"


def assess(change: Change) -> Risk:
    if change.action not in BASE:
        return Risk(0, "low", (), (), ())
    if change.tag_only:
        return Risk(0, "low", ("tag-only 0",), (), ())
    parts = [f"{ACTION_WORD[change.action]} {BASE[change.action]}"]
    kind = tf_types.category(change.rtype)
    add = _category_add(kind, change.action)
    if add:
        parts.append(f"{kind} {add}")
    flags = _flags(change)[:MAX_FLAGS]
    parts += [f"{flag} 1" for flag in flags]
    score = BASE[change.action] + add + len(flags)
    return Risk(score, level_of(score), tuple(parts), tuple(flags), _checks(change, kind))


def _category_add(kind: str, action: str) -> int:
    if kind == "stateful":
        return STATEFUL_ADD.get(action, 0)
    if kind == "network":
        return NETWORK_ADD if action in ("delete", "replace", "update") else 0
    if kind == "identity":
        return IDENTITY_ADD if action != "create" else 0
    return 0


def _flags(change: Change) -> list[str]:
    flags = []
    if change.action == "update":
        flags += [key for key in tf_types.FLAG_KEYS if change.attr(key) is not None]
    if _protection_off(change):
        flags.append("deletion_protection")
    if _no_final_snapshot(change):
        flags.append("skip_final_snapshot")
    return flags


def _protection_off(change: Change) -> bool:
    attr = change.attr("deletion_protection")
    return attr is not None and attr.after == "false" and attr.before != "false"


def _no_final_snapshot(change: Change) -> bool:
    attr = change.attr("skip_final_snapshot")
    value = (attr.after if change.action != "delete" else attr.before) if attr else dict(change.known).get(
        "skip_final_snapshot")
    return change.action in ("update", "replace", "delete") and value == "true"


def _checks(change: Change, kind: str) -> tuple[str, ...]:
    checks = []
    if _protection_off(change):
        checks.append(f"{change.address} has deletion_protection = false.")
    if kind == "stateful" and change.action in ("delete", "replace"):
        verb = "destroys" if change.action == "delete" else "replaces (destroys and recreates)"
        checks.append(f"This plan {verb} {change.address}, which holds data.")
    return tuple(checks)
