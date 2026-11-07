"""Pure: the risk surfaces that fire for a change, each with the code-supplied token the model words a note
about. The level is computed from them: any high surface makes the change high, any other makes it medium."""

from __future__ import annotations

from dataclasses import dataclass

import kinds as k
from diff_parse import FileDiff

LARGE_LINES = 1_000
MEDIUM_LINES = 300
CODE_KINDS = {"source", "migration", "config"}


@dataclass(frozen=True)
class Surface:
    name: str
    level: str  # high | medium
    token: str


def surfaces(facts, files: list[FileDiff], kinds: dict[str, str], logic: list[FileDiff],
              changed: list[str]) -> tuple[Surface, ...]:
    out: list[Surface] = []
    for f in files:
        if kinds[f.path] == "migration":
            out.append(Surface("migration", "high", f.path))
        if k.has_destructive_sql(f.plus):
            out.append(Surface("destructive_sql", "high", f.path))
    sensitive = next((f.path for f in files if kinds[f.path] in CODE_KINDS and k.sensitive_word(f.path)), None)
    if sensitive:
        out.append(Surface("sensitive_path", "high", sensitive))
    out += [Surface("removed_public", "high", s) for s in facts.removed_symbols]
    out += [Surface("changed_signature", "high", s) for s in facts.changed_symbols]
    if facts.total > LARGE_LINES:
        out.append(Surface("large", "high", f"{facts.total:,} changed lines"))
    config = next((f.path for f in files if kinds[f.path] == "config"), None)
    for token in ([config] if config else []) + list(facts.env_vars) + list(facts.options):
        out.append(Surface("config", "medium", token))
    out += [Surface("dependency", "medium", f.path) for f in files if kinds[f.path] == "build"][:2]
    out += [Surface("ci", "medium", f.path) for f in files if kinds[f.path] == "ci"][:2]
    if MEDIUM_LINES <= facts.total <= LARGE_LINES:
        out.append(Surface("size", "medium", f"{facts.total:,} changed lines"))
    if logic and not facts.test_files:
        out.append(Surface("no_tests", "medium", logic[0].path))
    if k.has_concurrency(changed):
        out.append(Surface("concurrency", "medium", next(f.path for f in files if k.has_concurrency(f.plus + f.minus))))
    return tuple(dict.fromkeys(out))


_NOTES = {
    "migration": "Migration `{}` changes the database schema.",
    "destructive_sql": "`{}` drops or deletes data, which a rollback cannot restore.",
    "sensitive_path": "Touches a sensitive path: `{}`.",
    "removed_public": "Removes the public symbol or route `{}`.",
    "changed_signature": "Changes the signature of `{}`.",
    "large": "Large change ({}).",
    "config": "Changes configuration: `{}`.",
    "dependency": "Changes dependencies in `{}`.",
    "ci": "Changes CI in `{}`.",
    "size": "Medium-size change ({}).",
    "no_tests": "Source changes without test changes (`{}`).",
    "concurrency": "Touches concurrency primitives in `{}`.",
}


def code_notes(fired: tuple[Surface, ...], worded: list[dict], limit: int) -> list[dict]:
    """The model's notes, then a code-worded note for each fired surface the model did not cover."""
    covered = {n["surface"] for n in worded}
    extra = [{"surface": s.name, "text": _NOTES[s.name].format(s.token)} for s in fired
             if s.name not in covered and s.name in _NOTES]
    seen: set[str] = set()
    unique = [n for n in extra if not (n["text"] in seen or seen.add(n["text"]))]
    return (list(worded) + unique)[:limit]
