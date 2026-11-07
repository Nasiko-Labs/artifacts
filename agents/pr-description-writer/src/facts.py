"""Pure: every fact about the change, computed in code from the parsed diff and the user's context. The model
gets these as exact values and is told never to compute: counts, change type, scope, risk level and surfaces,
test files, symbols, routes, env vars, ticket references, test commands, size and split notes."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, fields

import kinds as k
import risk
from diff_parse import FileDiff, ParsedDiff
from risk import LARGE_LINES, Surface

SCOPE_SHARE = 0.7
GENERIC_DIRS = {"src", "lib", "app", "pkg", "internal", "cmd", "packages", "source", "main"}
CODE_KINDS = {"source", "migration", "config"}
_TICKET = re.compile(r"(?<![\w#&])#\d{1,6}\b|\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
_JIRA = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
_CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(#\d{1,6}|[A-Z][A-Z0-9]{1,9}-\d{1,6})\b",
                     re.IGNORECASE)
_FIX_WORDS = re.compile(r"\b(?:bug|fix|fixes|fixed|error|crash|crashes|broken|regression|incorrect|wrong|"
                        r"exception|fails?|failing)\b", re.IGNORECASE)
_PERF_WORDS = re.compile(r"\b(?:perf|performance|faster|slow|slower|latency|speed ?up|throughput)\b", re.IGNORECASE)
_GUARD = re.compile(r"^\s*(?:if|elif|else if|guard|unless|except|catch)\b")
_TESTED = re.compile(r"[^.\n]*\b(?:tested|ran|verified|run (?:the )?tests?)\b[^.\n]*", re.IGNORECASE)
_NOT_BREAKING = re.compile(r"\bnot (?:a )?breaking\b|\bnon-?breaking\b", re.IGNORECASE)
MODEL_TYPES = ("feat", "fix", "refactor", "perf", "chore")


@dataclass(frozen=True)
class Facts:
    files: int = 0
    added: int = 0
    removed: int = 0
    paths: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()  # parallel to `paths`
    change_type: str | None = None
    scope: str | None = None
    risk: str = "low"
    surfaces: tuple[Surface, ...] = ()
    test_files: tuple[str, ...] = ()
    removed_symbols: tuple[str, ...] = ()
    changed_symbols: tuple[str, ...] = ()
    new_symbols: tuple[str, ...] = ()
    new_routes: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    tickets: tuple[str, ...] = ()
    closes: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()
    tested: str | None = None
    size_note: str | None = None
    split_note: str | None = None
    breaking: tuple[str, ...] = ()
    fix_cue: bool = False
    perf_cue: bool = False
    stat_only: bool = False
    generated: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.added + self.removed

    def kind(self, path: str) -> str:
        return self.kinds[self.paths.index(path)] if path in self.paths else "source"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> Facts | None:
        if not data:
            return None
        values = {}
        for f in fields(Facts):
            value = data.get(f.name)
            if f.name == "surfaces":
                value = tuple(Surface(**s) for s in value or ())
            elif isinstance(value, list):
                value = tuple(value)
            elif isinstance(value, float):
                value = int(value)
            if value is not None:
                values[f.name] = value
        return Facts(**values)


def _scope(files: list[FileDiff], kinds: dict[str, str]) -> str | None:
    code = [f.path for f in files if kinds[f.path] in CODE_KINDS] or [f.path for f in files]
    dirs = []
    for path in code:
        parts = path.split("/")[:-1]
        while len(parts) > 1 and parts[0].casefold() in GENERIC_DIRS:
            parts = parts[1:]
        dirs.append(parts[0] if parts and parts[0].casefold() not in GENERIC_DIRS else None)
    top = Counter(d for d in dirs if d).most_common(1)
    if not top or top[0][1] / len(code) < SCOPE_SHARE:
        return None
    return re.sub(r"[^a-z0-9_-]+", "-", top[0][0].casefold()).strip("-") or None


def code_type(kind_set: set[str]) -> str | None:
    for only, name in (({"docs"}, "docs"), ({"test"}, "test"), ({"ci"}, "ci"), ({"build", "generated"}, "build")):
        if kind_set and kind_set <= only:
            return name
    return None


def decide_type(facts: Facts, model_type: str | None) -> tuple[str, bool]:
    """(type, decided). The model's pick must meet its requirement; else the code default."""
    if facts.change_type:
        return facts.change_type, True
    new_public = bool(facts.new_symbols or facts.new_routes or facts.options or facts.env_vars)
    ok = {"feat": new_public, "fix": facts.fix_cue, "perf": facts.perf_cue, "refactor": True, "chore": True}
    picked = (model_type or "").strip().casefold()
    if ok.get(picked):
        return picked, True
    fallback = "feat" if new_public else "fix" if facts.fix_cue else "refactor"
    return fallback, picked in MODEL_TYPES


def _refactor_only(f: FileDiff) -> bool:
    norm = sorted(" ".join(line.split()) for line in k.logic_lines(f.plus))
    return (f.status == "renamed" and not f.plus and not f.minus) or (bool(norm) and norm == sorted(
        " ".join(line.split()) for line in k.logic_lines(f.minus)))


def build(parsed: ParsedDiff, context: str) -> Facts:
    files = list(parsed.files)
    kinds = {f.path: k.kind_of(f.path) for f in files}
    code = [f for f in files if kinds[f.path] in CODE_KINDS]
    plus = [line for f in code for line in f.plus]
    minus = [line for f in code for line in f.minus]
    plus_defs, minus_defs = k.definitions(plus), k.definitions(minus)
    removed = tuple(n for n in minus_defs if n not in plus_defs)
    changed = tuple(n for n in minus_defs if n in plus_defs and " ".join(minus_defs[n].split())
                    != " ".join(plus_defs[n].split()))
    new = tuple(n for n in plus_defs if n not in minus_defs)
    new_routes = tuple(r for r in k.routes(plus) if r not in k.routes(minus))
    removed_routes = tuple(r for r in k.routes(minus) if r not in k.routes(plus))
    added, removed_lines = parsed.stat_totals if parsed.stat_only and parsed.stat_totals else (
        sum(f.added for f in files), sum(f.removed for f in files))
    tests = tuple(f.path for f in files if kinds[f.path] == "test" and f.status != "deleted")
    logic = [f for f in code if k.logic_lines(f.plus) or k.logic_lines(f.minus)]
    ci_build = [line for f in files if kinds[f.path] in ("ci", "build") for line in f.plus]
    tested = _TESTED.search(context or "")
    refactors = [f for f in code if _refactor_only(f)]
    facts = Facts(
        files=len(files), added=added, removed=removed_lines, paths=tuple(f.path for f in files),
        kinds=tuple(kinds[f.path] for f in files), change_type=code_type(set(kinds.values())),
        scope=_scope(files, kinds), test_files=tests, removed_symbols=removed + removed_routes,
        changed_symbols=changed, new_symbols=new, new_routes=new_routes, env_vars=tuple(k.env_vars(plus)),
        options=tuple(k.options(plus)), tickets=_tickets(context, parsed.raw),
        closes=tuple(dict.fromkeys(m.upper() if "-" in m else m for m in _CLOSES.findall(context or ""))),
        test_commands=tuple(k.test_commands(context) + k.test_commands("\n".join(ci_build))),
        tested=tested.group(0).strip() if tested else None,
        fix_cue=bool(_FIX_WORDS.search(context or "")) or any(
            _GUARD.match(line) for f in code if f.status == "modified" for line in f.plus),
        perf_cue=bool(_PERF_WORDS.search(context or "")), stat_only=parsed.stat_only,
        generated=tuple(f.path for f in files if kinds[f.path] == "generated"),
        breaking=() if _NOT_BREAKING.search(context or "") else removed + removed_routes,
        split_note=("Separate refactoring from feature work: some files only move or reformat code while others "
                    "change behaviour." if refactors and len(refactors) < len(logic) else None),
    )
    surfaces = risk.surfaces(facts, files, kinds, logic, plus + minus)
    level = "high" if any(s.level == "high" for s in surfaces) else "medium" if surfaces else "low"
    size_note = f"~{round(facts.total, -2):,} changed lines: consider splitting this PR." \
        if facts.total > LARGE_LINES else None
    return Facts(**{**facts.__dict__, "surfaces": surfaces, "risk": level, "size_note": size_note})


def _tickets(context: str, diff_raw: str) -> tuple[str, ...]:
    found = _TICKET.findall(context or "") + _JIRA.findall(diff_raw or "")
    return tuple(dict.fromkeys(found))
