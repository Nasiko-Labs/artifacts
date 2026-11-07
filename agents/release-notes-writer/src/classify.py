"""Pure: the kind of each change and whether it is internal-only, from its prefix, scope, labels and title.

Breaking and security changes are never internal. A change whose prefix and labels name no kind keeps
`kind=None`: the rewrite call decides its kind and whether customers would notice it."""

from __future__ import annotations

import re
from dataclasses import replace

from change import Change

NEW = frozenset({"feat", "feature", "features", "enhancement", "new", "added", "add"})
IMPROVED = frozenset({"perf", "performance", "improve", "improvement", "improved", "improvements", "changed"})
FIXED = frozenset({"fix", "fixes", "fixed", "bug", "bugfix", "hotfix"})
SECURITY = frozenset({"security", "sec", "cve", "vulnerability"})
INTERNAL = frozenset({"chore", "ci", "build", "test", "tests", "refactor", "style", "docs", "doc", "deps",
                      "dependencies", "bump", "merge", "revert", "wip", "internal", "infra", "tooling"})
_INTERNAL_TITLE = re.compile(r"\btypo\b|\blint(?:ing|er)?\b|\brename (?:a |the )?variables?\b|^merge (?:pull request|"
                             r"branch)\b|^bump\b|\bwip\b", re.IGNORECASE)
_MIGRATION = re.compile(r"(?:[,;(]\s*|\binstead,?\s+)((?:please )?(?:use|switch to|migrate to|move to|upgrade to)\b[^,;)]{3,160})",
                        re.IGNORECASE)
_CVE = re.compile(r"\bCVE-\d{4}-\d{3,}\b", re.IGNORECASE)


def _keys(change: Change) -> list[str]:
    return [k.casefold().strip() for k in (change.prefix, *change.labels) if k]


def kind_of(change: Change) -> str | None:
    keys = _keys(change)
    scope = change.scope.casefold()
    if scope in SECURITY or any(k in SECURITY for k in keys) or _CVE.search(change.title):
        return "security"
    if any(k.startswith(("deprecat", "remov")) for k in keys):
        return "deprecated"
    for key in keys:  # the prefix first, then labels in order
        for kind, words in (("new", NEW), ("improved", IMPROVED), ("fixed", FIXED)):
            if key in words:
                return kind
    return None


def is_internal(change: Change, kind: str | None) -> bool:
    if change.breaking or kind == "security":
        return False
    if change.prefix in INTERNAL or _INTERNAL_TITLE.search(change.title):
        return True
    return kind is None and any(label.casefold() in INTERNAL for label in change.labels)


def migration_of(change: Change) -> str:
    """Pasted migration words for a breaking change: a BREAKING CHANGE line, or a "use X instead" clause."""
    if change.migration or not change.breaking:
        return change.migration
    for text in (f", {change.body}" if change.body else "", change.title):
        match = _MIGRATION.search(text)
        if match:
            return match.group(1).strip().rstrip(".")
    return ""


def classify(change: Change, include_internal: bool = False) -> Change:
    kind = kind_of(change)
    internal = is_internal(change, kind) and not include_internal
    if include_internal and kind is None and is_internal(change, kind):
        kind = "improved"
    return replace(change, kind=kind, internal=internal, migration=migration_of(change))


def classify_all(changes: list[Change], include_internal: bool = False) -> list[Change]:
    return [classify(c, include_internal) for c in changes]
