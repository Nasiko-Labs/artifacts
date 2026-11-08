"""Pure: owners of a path, from CODEOWNERS rules (last matching rule wins) or, failing that, the top committer
of the path in the last 90 days (counted here). Never guessed: no rule and no commit data → "unknown"."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from records import Commit

CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
COMMIT_WINDOW_DAYS = 90
UNKNOWN = "unknown"

Rule = tuple[re.Pattern[str], tuple[str, ...]]


def parse(text: str) -> tuple[Rule, ...]:
    rules = []
    for raw in (text or "").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        owners = [o for o in owners if o.startswith("@") or "@" in o]
        if owners:
            rules.append((_compile(pattern), tuple(owners)))
    return tuple(rules)


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.startswith("/") or "/" in pattern.strip("/")
    body = ""
    i, core = 0, pattern.strip("/")
    while i < len(core):
        if core.startswith("**", i):
            body, i = body + ".*", i + 2
        elif core[i] == "*":
            body, i = body + "[^/]*", i + 1
        elif core[i] == "?":
            body, i = body + "[^/]", i + 1
        else:
            body, i = body + re.escape(core[i]), i + 1
    prefix = "^" if anchored else "^(?:.*/)?"
    return re.compile(f"{prefix}{body}(?:/.*)?$")


def owners_for(rules: Sequence[Rule], path: str) -> tuple[str, ...]:
    found: tuple[str, ...] = ()
    for pattern, owners in rules:
        if pattern.match(path.lstrip("/")):
            found = owners
    return found


def top_committer(commits: Iterable[Commit], now: datetime, days: int = COMMIT_WINDOW_DAYS) -> str:
    """The author with the most commits in the window; ties go to the alphabetically first name."""
    since = now - timedelta(days=days)
    counts = Counter(c.author for c in commits if (when := _when(c.date)) is not None and since <= when <= now)
    if not counts:
        return ""
    best = max(counts.values())
    return min(a for a, n in counts.items() if n == best)


def _when(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None
