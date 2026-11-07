"""Pure: CODEOWNERS parsing and matching, gitignore-style (the code host's documented rules): a pattern
without a slash (other than a trailing one) matches at any depth; a leading slash or an inner slash
anchors it to the root; `*` is one path segment, `**` any number; a trailing slash or a plain name also
matches everything under it; the LAST matching rule wins. Owners are kept exactly as written
(`@org/team`, `@user`, emails)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class Rule:
    pattern: str
    owners: tuple[str, ...]


def parse(text: str) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for raw in (text or "").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "[")):
            continue
        parts = line.split()
        rules.append(Rule(parts[0], tuple(parts[1:])))
    return tuple(rules)


def owners_of(path: str, rules: Sequence[Rule]) -> tuple[str, ...]:
    """The owners of the last rule matching `path` (empty when none matches or that rule names nobody)."""
    rule = matching_rule(path, rules)
    return rule.owners if rule else ()


def matching_rule(path: str, rules: Sequence[Rule]) -> Rule | None:
    clean = path.strip().strip("/")
    found = None
    for rule in rules:
        if _regex(rule.pattern).match(clean):
            found = rule
    return found


def _regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.startswith("/") or "/" in pattern.strip("/")
    body = pattern.strip("/")
    last = body.rsplit("/", 1)[-1]
    open_ended = pattern.endswith("/") or not any(ch in last for ch in "*?") or body.endswith("**")
    translated = _translate(body)
    prefix = "^" if anchored else "^(?:.*/)?"
    suffix = "(?:/.*)?$" if open_ended else "$"
    return re.compile(prefix + translated + suffix)


def _translate(glob: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("(?:/.*)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "".join(out)
