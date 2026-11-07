"""Checks a renewal brief: exactly three talking points, each citing a pasted item."""

import re

from prompts import INPUT_LABELS

REQUIRED_TALKING_POINTS = 3

_LABELS_PATTERN = "|".join(re.escape(label) for label in INPUT_LABELS)
_CITATION = re.compile(rf"\[(?:{_LABELS_PATTERN}) #\d+\]", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_NUMBERED_ITEM = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _talking_points_section(brief: str) -> list[str] | None:
    """Lines under the "Talking points" heading, or None if there is no such heading."""
    lines = brief.splitlines()
    for index, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading and heading.group(1).strip(" *:").lower() == "talking points":
            rest = lines[index + 1:]
            end = next((i for i, l in enumerate(rest) if _HEADING.match(l)), len(rest))
            return rest[:end]
    return None


def _numbered_items(section: list[str]) -> list[str]:
    """Numbered items, with indented continuation lines joined onto their item."""
    items: list[str] = []
    for line in section:
        item = _NUMBERED_ITEM.match(line)
        if item:
            items = [*items, item.group(1)]
        elif items and line.strip():
            items = [*items[:-1], f"{items[-1]} {line.strip()}"]
    return items


def validate_brief(brief: str) -> list[str]:
    """Return the rule violations in `brief`; an empty list means it passes."""
    section = _talking_points_section(brief)
    if section is None:
        return ['The brief has no "## Talking points" section.']

    points = _numbered_items(section)
    problems = []
    if len(points) != REQUIRED_TALKING_POINTS:
        problems.append(
            f"The brief must have exactly three talking points; it has {len(points)}."
        )
    problems.extend(
        f"Talking point {number} does not cite a pasted item."
        for number, point in enumerate(points, start=1)
        if not _CITATION.search(point)
    )
    return problems
