"""Pure: a pasted PR template read into its headings, in order, each with the section it maps to (or none) and
its own body lines (checkboxes, HTML comments and prose kept verbatim; the agent never ticks a checkbox)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_KEYS = (
    ("breaking", r"breaking changes?"),
    ("test_plan", (r"test(?:ing)?(?: plan| steps| instructions| done)?|how (?:to test|has this been tested|was "
                   r"this tested|to verify)|qa|verification|testing done")),
    ("why", r"why|motivation|context|background|problem|reason"),
    ("risk", r"risks?|impact|risks? (?:and|&) rollback|rollout|rollback|risk assessment"),
    ("reviewers", r"notes? (?:for|to) reviewers?|reviewer notes?|notes|review notes"),
    ("related", r"related (?:issues?|tickets?|links?)|issues?|tickets?|links?|refs?|references|fixes|closes"),
    ("changes", r"changes(?: made)?|what changed|what's changed|implementation|details|changelog"),
    ("summary", r"summary|description|what(?: does this pr do| is this| this pr does)?|overview|about|tl;?dr"),
)
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]")


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    key: str | None
    body: tuple[str, ...] = ()

    @property
    def checkboxes(self) -> tuple[str, ...]:
        return tuple(line for line in self.body if _CHECKBOX.match(line))

    @property
    def blank(self) -> bool:
        return not any(line.strip() for line in self.body)


def key_for(title: str) -> str | None:
    norm = re.sub(r"[^a-z0-9;&' ]+", " ", title.casefold()).strip()
    norm = re.sub(r"\s+", " ", norm)
    for key, pattern in _KEYS:
        if re.fullmatch(pattern, norm):
            return key
    return None


def parse(text: str) -> tuple[Heading, ...]:
    """Every heading of the template in order; text before the first heading is dropped."""
    headings: list[Heading] = []
    current: tuple[int, str] | None = None
    body: list[str] = []
    in_comment = False
    for line in (text or "").split("\n"):
        in_comment = (in_comment or "<!--" in line) and "-->" not in line
        match = None if in_comment else _HEADING.match(line)
        if match:
            if current:
                headings.append(_heading(current, body))
            current, body = (len(match.group(1)), match.group(2).strip()), []
        elif current:
            body.append(line.rstrip())
    if current:
        headings.append(_heading(current, body))
    return tuple(headings)


def _heading(head: tuple[int, str], body: list[str]) -> Heading:
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return Heading(head[0], head[1], key_for(head[1]), tuple(body))
