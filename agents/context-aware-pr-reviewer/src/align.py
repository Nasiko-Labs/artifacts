"""Pure: ticket alignment in code. Merging per-chunk criterion verdicts, the label, the `met/total`
coverage string, the keyword-overlap checks (criterion stems against changed paths and added lines), scope
creep, and the `ticket_gap` comment for each criterion the diff does not implement."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from diffing import FilePatch
from verify import Comment

STATUSES = ("met", "partial", "not_found")
SCOPE_CREEP_MIN_FILES = 3
SCOPE_CREEP_MIN_PERCENT = 40
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "then", "should", "must", "will", "shall",
    "can", "are", "was", "were", "has", "have", "not", "but", "all", "any", "each", "user", "users", "able",
    "show", "shows", "new", "use", "used", "using", "also", "only", "its", "their", "there", "which", "what",
    "via", "per", "add", "added", "adds", "make", "made", "file", "files", "src", "lib", "test", "tests", "spec",
    "def", "return", "self", "none", "true", "false", "import", "class", "const", "let", "var", "function",
    "clear", "given", "case", "work", "works", "get", "set", "one", "two", "page", "after", "before"})


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str
    status: str = "not_found"
    evidence: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "status": self.status,
                "evidence": [{"file": f, "line": n} for f, n in self.evidence]}

    @staticmethod
    def from_dict(d: dict) -> Criterion:
        ev = tuple((str(e.get("file") or ""), int(e.get("line") or 0)) for e in d.get("evidence") or ())
        return Criterion(str(d.get("id") or ""), str(d.get("text") or ""), str(d.get("status") or "not_found"), ev)


def stems(text: str) -> frozenset[str]:
    out = set()
    for word in _WORD.findall(_CAMEL.sub(" ", text or "")):
        w = word.casefold()
        if len(w) < 3 or w in _STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)]
                break
        out.add(w[:6])
    return frozenset(out)


def merge(criteria: Sequence[Criterion], per_chunk: Sequence[Sequence[Criterion]]) -> tuple[Criterion, ...]:
    """met if any chunk says met (with verified evidence), partial if any says partial, else not_found."""
    out = []
    for c in criteria:
        seen = [r for chunk in per_chunk for r in chunk if r.id == c.id]
        best = min((STATUSES.index(r.status) for r in seen), default=2)
        evidence = tuple(dict.fromkeys(e for r in seen if STATUSES.index(r.status) == best for e in r.evidence))
        out.append(Criterion(c.id, c.text, STATUSES[best], evidence if best < 2 else ()))
    return tuple(out)


def coverage(criteria: Sequence[Criterion]) -> str:
    met = sum(1 for c in criteria if c.status == "met")
    return f"{met}/{len(criteria)}"


def label(criteria: Sequence[Criterion], files: Sequence[FilePatch], has_ticket: bool) -> str:
    if not has_ticket:
        return "no_ticket"
    if not criteria:
        return "unclear"
    met = sum(1 for c in criteria if c.status == "met")
    partial = sum(1 for c in criteria if c.status == "partial")
    if met == len(criteria):
        return "aligned"
    if (met == 0 and partial == 0) or not any(overlap(c, files) for c in criteria):
        return "misaligned"
    return "partial"


def file_stems(f: FilePatch) -> frozenset[str]:
    return stems(f.path.replace("/", " ").replace("_", " ").replace(".", " ").replace("-", " ")) | stems(f.added_text())


def overlap(c: Criterion, files: Iterable[FilePatch]) -> bool:
    mine = stems(c.text)
    return any(mine & file_stems(f) for f in files)


def is_test_path(path: str) -> bool:
    p = path.casefold()
    name = p.rsplit("/", 1)[-1]
    return ("/test" in f"/{p}" or "/spec" in f"/{p}" or name.startswith("test_") or "_test." in name
            or ".test." in name or ".spec." in name)


def scope_creep(criteria: Sequence[Criterion], files: Sequence[FilePatch]) -> tuple[tuple[str, ...], int]:
    """(paths, percent of changed lines): non-test files that share no stem with any criterion, listed only
    when there are at least 3 of them and they hold at least 40% of the changed lines."""
    if not criteria:
        return (), 0
    wanted = frozenset().union(*(stems(c.text) for c in criteria))
    total = sum(f.changed_lines for f in files)
    out = [f for f in files if not is_test_path(f.path) and not (file_stems(f) & wanted)]
    lines = sum(f.changed_lines for f in out)
    percent = (lines * 100 // total) if total else 0
    if len(out) >= SCOPE_CREEP_MIN_FILES and percent >= SCOPE_CREEP_MIN_PERCENT:
        return tuple(f.path for f in out), percent
    return (), percent


def gap_comments(criteria: Sequence[Criterion], files: Sequence[FilePatch]) -> tuple[Comment, ...]:
    """One `ticket_gap` comment per criterion not found: at the changed file sharing most stems with it,
    on its first changed line; PR-level (no file, line 0) when no file shares any."""
    out = []
    for c in criteria:
        if c.status != "not_found":
            continue
        mine = stems(c.text)
        scored = sorted(((len(mine & file_stems(f)), i, f) for i, f in enumerate(files)), key=lambda t: (-t[0], t[1]))
        best = scored[0][2] if scored and scored[0][0] > 0 else None
        line = next((h.new_start for h in best.hunks), 0) if best else 0
        out.append(Comment(best.path if best else "", line, "suggestion", "ticket_gap",
                           f"Ticket criterion {c.id} is not implemented in this diff: \"{c.text}\"", "", None))
    return tuple(out)
