"""Pure: which commit in the suspect range most likely broke the build, and why, in code.

Score per commit: 3 if a failure location falls inside one of its hunks on the same path (±3 lines),
else 2 if it touches a failure path or the failing test file, else 1 if it touches the file under test
(`test_foo.py` ↔ `foo.py`, `FooTest.java` ↔ `Foo.java`, `foo.test.ts` ↔ `foo.ts`), else 0.
Highest score wins, the newest on a tie. The top commit is the culprit when it scores ≥ 1 or is alone.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from excerpt import Location

HUNK_SLACK = 3
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_TEST_STEM = re.compile(r"^(?:test_(?P<a>.+)|(?P<b>.+?)_test|(?P<c>.+?)Tests?|(?P<d>.+?)\.(?:test|spec))$")


@dataclass(frozen=True)
class ChangedFile:
    path: str
    patch: str = ""


@dataclass(frozen=True)
class Commit:
    sha: str
    author_login: str = ""
    author_name: str = ""
    date: str = ""
    subject: str = ""
    url: str = ""
    files: tuple[ChangedFile, ...] | None = None   # None: not fetched yet

    @property
    def author(self) -> str:
        if self.author_login and self.author_name and self.author_login != self.author_name:
            return f"{self.author_name} ({self.author_login})"
        return self.author_name or self.author_login or "unknown author"


@dataclass(frozen=True)
class Scored:
    commit: Commit
    score: int
    evidence: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Narrowed:
    culprit: Scored | None
    ranked: tuple[Scored, ...]
    note: str = ""   # "in range a..b, no commit touches the failing code"


def hunks(patch: str) -> tuple[tuple[int, int], ...]:
    """New-side line ranges from a unified diff's hunk headers."""
    out = []
    for m in _HUNK.finditer(patch or ""):
        start, count = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
        out.append((start, start + max(count, 1) - 1))
    return tuple(out)


def same_path(a: str, b: str) -> bool:
    a, b = a.strip("/"), b.strip("/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def subject_stem(test_path: str) -> str:
    name = test_path.rsplit("/", 1)[-1]
    stem = name.split(".", 1)[0] if not re.search(r"\.(?:test|spec)\.", name) else name.rsplit(".", 1)[0]
    m = _TEST_STEM.match(stem)
    if not m:
        return ""
    return next(v for v in m.groupdict().values() if v).casefold()


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].split(".", 1)[0].casefold()


def score(commit: Commit, locations: Sequence[Location], test_path: str) -> Scored:
    files = commit.files or ()
    for f in files:
        ranges = hunks(f.patch)
        for loc in locations:
            hit = next(((a, b) for a, b in ranges if a - HUNK_SLACK <= loc.line <= b + HUNK_SLACK), None)
            if same_path(f.path, loc.path) and hit:
                return Scored(commit, 3, (f"changes `{f.path}` lines {hit[0]}–{hit[1]}; the failure is at `{loc}`",))
    failure_paths = [loc.path for loc in locations] + ([test_path] if test_path else [])
    for f in files:
        if any(same_path(f.path, p) for p in failure_paths):
            what = "the failing test file" if test_path and same_path(f.path, test_path) else "where the failure is"
            return Scored(commit, 2, (f"changes `{f.path}`, {what}",))
    stems = {subject_stem(p) for p in failure_paths if subject_stem(p)}
    for f in files:
        if _stem(f.path) in stems and not any(same_path(f.path, p) for p in failure_paths):
            return Scored(commit, 1, (f"changes `{f.path}`, the code under test",))
    return Scored(commit, 0)


def narrow(commits: Sequence[Commit], locations: Sequence[Location], test_path: str) -> Narrowed:
    """`commits` oldest first (the suspect range, or every pull request commit)."""
    if not commits:
        return Narrowed(None, ())
    indexed = [(i, score(c, locations, test_path)) for i, c in enumerate(commits)]
    ranked = tuple(s for _, s in sorted(indexed, key=lambda p: (p[1].score, p[0]), reverse=True))
    top = ranked[0]
    if top.score >= 1 or len(commits) == 1:
        return Narrowed(top, ranked)
    note = (f"in range {commits[0].sha[:7]}..{commits[-1].sha[:7]}, no commit touches the failing code")
    return Narrowed(None, ranked, note)


def in_hunk(commit: Commit, path: str, line: int) -> bool:
    return any(same_path(f.path, path) and any(a <= line <= b for a, b in hunks(f.patch))
               for f in commit.files or ())


def suspect_range(commits: Sequence[Commit], last_green_sha: str, boundary_sha: str) -> tuple[Commit, ...]:
    """Commits in (last_green, B], from an oldest-first list; the whole list when the ends are absent."""
    shas = [c.sha for c in commits]
    start = next((i + 1 for i, s in enumerate(shas) if last_green_sha and s.startswith(last_green_sha)), 0)
    end = next((i + 1 for i, s in enumerate(shas) if boundary_sha and s.startswith(boundary_sha)), len(shas))
    picked = tuple(commits[start:end])
    return picked or tuple(commits)
