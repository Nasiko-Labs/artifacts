"""Pure: robots.txt rules (RFC 9309): groups by user-agent, longest-match Allow/Disallow with `*` and `$`,
Allow winning a tie, case-insensitive agent names, the `*` group as the fallback.

Used twice: to honour robots.txt for our own fetch, and to report which AI crawlers the site blocks (a
choice, reported at info severity, never a failure)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OUR_AGENT = "NasikoSEOAudit"
AI_CRAWLERS = ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot", "Applebot-Extended")


@dataclass(frozen=True)
class Rule:
    allow: bool
    path: str
    line: str           # the raw line, as evidence


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    rule: Rule | None = None   # the deciding rule, None when nothing matched


def parse(text: str) -> list[Group]:
    groups: list[Group] = []
    current: Group | None = None
    in_agents = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (s.strip() for s in line.split(":", 1))
        key = key.casefold()
        if key == "user-agent":
            if current is None or not in_agents:
                current = Group()
                groups.append(current)
            current.agents.append(value.casefold())
            in_agents = True
        elif key in ("allow", "disallow") and current is not None:
            in_agents = False
            if key == "disallow" and not value:
                continue  # an empty Disallow allows everything
            current.rules.append(Rule(key == "allow", value, raw.strip()))
    return groups


def _groups_for(groups: list[Group], agent: str) -> list[Group]:
    name = agent.casefold()
    named = [g for g in groups if name in g.agents]
    return named or [g for g in groups if "*" in g.agents]


def _matches(pattern: str, path: str) -> int:
    """The match length (specificity), or -1 for no match."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if c == "*" else re.escape(c) for c in body) + ("$" if anchored else "")
    return len(pattern) if re.match(regex, path) else -1


def verdict(groups: list[Group], agent: str, path: str) -> Verdict:
    best: tuple[int, bool, Rule] | None = None
    for group in _groups_for(groups, agent):
        for rule in group.rules:
            length = _matches(rule.path, path or "/")
            if length < 0:
                continue
            if best is None or (length, rule.allow) > (best[0], best[1]):
                best = (length, rule.allow, rule)
    if best is None:
        return Verdict(True)
    return Verdict(best[1], best[2])


def ai_crawlers(text: str, path: str) -> dict[str, Verdict]:
    groups = parse(text)
    return {name: verdict(groups, name, path) for name in AI_CRAWLERS}
