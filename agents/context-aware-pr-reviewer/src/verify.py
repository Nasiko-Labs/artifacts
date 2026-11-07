"""Pure: the grounding checks. A review comment survives only when its `file:line` is inside a changed
hunk of this turn's diff, its quote is a verbatim line of that file's patch, every identifier, number and
link it mentions appears in the diff, the ticket or the precedent, a blocker carries a quote, and any
precedent it cites is one of the similar pull requests supplied. Criterion evidence outside a changed
hunk is removed and the status goes down one step."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from diffing import FilePatch, in_hunk

SEVERITIES = ("blocker", "suggestion", "nit")
CATEGORIES = ("correctness", "security", "tests", "design", "style", "ticket_gap")
MAX_COMMENTS = 12
MAX_COMMENT_WORDS = 60
DEDUPE_LINES = 2
_URL = re.compile(r"https?://[^\s)>\]`'\"]+")
_TICK = re.compile(r"`([^`\n]{1,120})`")
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+|[a-z0-9][A-Z][A-Za-z0-9]*)+\b")
_NUMBER = re.compile(r"(?<![\w.])\d{2,}(?:\.\d+)?(?![\w.])")


@dataclass(frozen=True)
class Comment:
    file: str
    line: int
    severity: str
    category: str
    comment: str
    quote: str
    precedent: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Mapping) -> Comment:
        line = d.get("line")
        return Comment(str(d.get("file") or ""), int(line) if str(line).lstrip("-").isdigit() else 0,
                       str(d.get("severity") or ""), str(d.get("category") or ""), str(d.get("comment") or ""),
                       str(d.get("quote") or ""), (str(d["precedent"]) if d.get("precedent") else None))


def quote_in_patch(quote: str, patch: FilePatch | None) -> bool:
    q = (quote or "").strip()
    if not q or patch is None:
        return False
    return any(q in line[1:] for h in patch.hunks for line in h.lines if line[:1] in " +-")


def mentions(text: str) -> set[str]:
    found = set(_URL.findall(text)) | set(_TICK.findall(text)) | set(_IDENT.findall(_URL.sub(" ", text)))
    found |= set(_NUMBER.findall(_URL.sub(" ", _TICK.sub(" ", text))))
    return {m.strip() for m in found if m.strip()}


def check(raw: Mapping, files: Mapping[str, FilePatch], seen: str, precedents: frozenset[str]) -> Comment | None:
    """The comment, normalised, or None when it cannot be verified."""
    c = Comment.from_dict(raw)
    if c.severity not in SEVERITIES or c.category not in CATEGORIES or c.category == "ticket_gap":
        return None
    if not c.comment.strip() or len(c.comment.split()) > MAX_COMMENT_WORDS:
        return None
    patch = files.get(c.file)
    if not in_hunk(patch, c.line):
        return None
    if c.quote and not quote_in_patch(c.quote, patch):
        return None
    if c.severity == "blocker" and not c.quote.strip():
        return None
    if c.precedent and c.precedent not in precedents:
        return None
    if any(m not in seen for m in mentions(c.comment)):
        return None
    return Comment(c.file, c.line, c.severity, c.category, c.comment.strip(), c.quote.strip(), c.precedent)


def check_all(raws: Sequence, files: Mapping[str, FilePatch], seen: str,
              precedents: frozenset[str]) -> tuple[tuple[Comment, ...], int]:
    """(kept comments, how many were dropped as unverifiable)."""
    dicts = [r for r in raws if isinstance(r, Mapping)]
    kept = [c for c in (check(r, files, seen, precedents) for r in dicts) if c is not None]
    return tuple(kept), len(raws) - len(kept)


def finalize(comments: Sequence[Comment]) -> tuple[tuple[Comment, ...], int]:
    """Collapse duplicates (same file, line within 2, same category), sort blocker first then file and line,
    keep at most MAX_COMMENTS. Returns (comments, how many were cut beyond the cap)."""
    unique: list[Comment] = []
    ordered = sorted(comments, key=lambda c: (SEVERITIES.index(c.severity), c.file or "~", c.line))
    for c in ordered:
        if any(u.file == c.file and u.category == c.category and abs(u.line - c.line) <= DEDUPE_LINES for u in unique):
            continue
        unique.append(c)
    return tuple(unique[:MAX_COMMENTS]), max(0, len(unique) - MAX_COMMENTS)


def evidence(raw: object, files: Mapping[str, FilePatch]) -> tuple[tuple[tuple[str, int], ...], bool]:
    """(evidence inside a changed hunk, whether any claimed evidence was invalid)."""
    items = raw if isinstance(raw, list) else []
    valid, invalid = [], False
    for e in items:
        if not isinstance(e, Mapping):
            invalid = True
            continue
        path, line = str(e.get("file") or ""), e.get("line")
        n = int(line) if str(line).isdigit() else 0
        if in_hunk(files.get(path), n):
            valid.append((path, n))
        else:
            invalid = True
    return tuple(dict.fromkeys(valid)), invalid


def downgrade(status: str, valid: tuple, invalid: bool) -> str:
    """A met or partial claim without valid evidence, or with invalid evidence, goes down one step."""
    if status not in ("met", "partial"):
        return "not_found"
    if invalid or not valid:
        return "partial" if status == "met" else "not_found"
    return status
