"""Pure: split long Markdown into parts that fit one chat message, on a heading boundary when possible,
else on a blank line, else hard. Counts computed in code."""

from __future__ import annotations

PAGE_CHARS = 60_000


def split(markdown: str, limit: int = PAGE_CHARS) -> list[str]:
    parts: list[str] = []
    rest = markdown
    while len(rest) > limit:
        cut = _cut(rest, limit)
        parts.append(rest[:cut].rstrip("\n") + "\n")
        rest = rest[cut:].lstrip("\n")
    parts.append(rest)
    return parts


def _cut(text: str, limit: int) -> int:
    window = text[:limit]
    heading = max(window.rfind("\n# "), window.rfind("\n## "), window.rfind("\n### "))
    if heading > limit // 4:
        return heading + 1
    blank = window.rfind("\n\n")
    if blank > limit // 4:
        return blank + 2
    line = window.rfind("\n")
    return line + 1 if line > limit // 4 else limit
