"""Pure: turn code-search hits into `affected[]` rows: attribution to a changed resource (only when the
search or the file text contains that resource's name, module call or identifier verbatim), kind,
environment from the path, de-duplication per (repo, path) and ranking.

Ranking: the driving resource's risk level (critical first), then environment (prod first), then kind
(reads_state > module_consumer > references > definition), then repo, path, line.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from plan_model import Change
from records import Hit, contains
from risk import LEVEL_ORDER
from search_plan import REMOTE_STATE, Needle, identifier

MAX_SNIPPET_CHARS = 160
PASTED = "(pasted)"
KIND_ORDER = {"reads_state": 0, "module_consumer": 1, "references": 2, "definition": 3}
ENV_ORDER = {"prod": 0, "staging": 1, "dev": 2, "qa": 3, "unknown": 4}
_ENV_RULES = (("prod", re.compile(r"(^|/)(prod|production|prd)(/|$|[._-])", re.IGNORECASE)),
              ("staging", re.compile(r"(^|/)(stag(e|ing))(/|$|[._-])", re.IGNORECASE)),
              ("dev", re.compile(r"(^|/)(dev|develop|development)(/|$|[._-])", re.IGNORECASE)),
              ("qa", re.compile(r"(^|/)(qa|test)(/|$|[._-])", re.IGNORECASE)))


@dataclass(frozen=True)
class Affected:
    change: str
    kind: str
    repo: str
    path: str
    line: int
    snippet: str
    environment: str
    owner: str
    risk_level: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> Affected:
        return Affected(str(data.get("change") or ""), str(data.get("kind") or "references"),
                        str(data.get("repo") or ""), str(data.get("path") or ""), int(data.get("line") or 0),
                        str(data.get("snippet") or ""), str(data.get("environment") or "unknown"),
                        str(data.get("owner") or "unknown"), str(data.get("risk_level") or "low"))


def environment(path: str) -> str:
    return next((env for env, rule in _ENV_RULES if rule.search(path or "")), "unknown")


def classify(needle_kind: str, snippet: str) -> str:
    text = snippet.lstrip()
    if REMOTE_STATE in text or text.startswith('data "') or needle_kind == "remote_state":
        return "reads_state"
    if needle_kind == "definition":
        return "definition"
    if needle_kind == "module_call" or text.startswith("module "):
        return "module_consumer"
    return "references"


def _lines(text: str) -> list[tuple[int, str]]:
    return [(n, line) for n, line in enumerate(text.splitlines(), start=1)]


def _find(text: str, test) -> tuple[int, str] | None:
    return next(((n, line.strip()[:MAX_SNIPPET_CHARS]) for n, line in _lines(text) if test(line)), None)


def _locate(hit: Hit, content: str, test) -> tuple[int, str] | None:
    """(line, snippet) from the file text, else from the search fragments (line unknown = 0)."""
    if content:
        found = _find(content, test)
        if found:
            return found
    for fragment in hit.fragments:
        found = _find(fragment, test)
        if found:
            return (hit.line, found[1])
    return None


def attribute(hits: Sequence[Hit], needles: Sequence[Needle], changes: Mapping[str, Change],
              contents: Mapping[tuple[str, str], str]) -> list[tuple[Needle, Hit, int, str]]:
    """Every (needle, hit, line, snippet) where the hit belongs to the needle's resource."""
    out = []
    for hit in hits:
        content = contents.get((hit.repo, hit.path), "")
        for needle in needles:
            if hit.repo not in (needle.repo, PASTED):
                continue
            if needle.kind == "remote_state":
                out += _remote_state(needle, hit, content, changes)
                continue
            found = _locate(hit, content, lambda line, n=needle: contains(line, n.text))
            if found is not None:
                out.append((needle, hit, *found))
            elif hit.repo != PASTED and contains(hit.query, needle.text):
                out.append((needle, hit, 0, ""))
    return out


def _remote_state(needle: Needle, hit: Hit, content: str, changes: Mapping[str, Change]) -> list:
    if not contains(hit.query, REMOTE_STATE) and hit.repo != PASTED:
        return []
    out = []
    for change in changes.values():
        marks = [m for m in (f'"{change.module_name}"' if change.module_name else "", identifier(change)) if m]
        test = (lambda line, ms=marks: REMOTE_STATE in line and any(m in line for m in ms))
        found = _locate(hit, content, test) if marks else None
        if found is not None:
            out.append((Needle("remote_state", needle.text, needle.repo, change.address), hit, *found))
    return out


def assemble(found: Sequence[tuple[Needle, Hit, int, str]], levels: Mapping[str, str],
             owners: Mapping[tuple[str, str], str]) -> list[Affected]:
    rows = [Affected(change=needle.address, kind=classify(needle.kind, snippet), repo=hit.repo, path=hit.path,
                     line=line, snippet=snippet, environment=environment(hit.path),
                     owner=owners.get((hit.repo, hit.path)) or "unknown",
                     risk_level=levels.get(needle.address, "low"))
            for needle, hit, line, snippet in found if needle.address]
    return dedupe(rank(rows))


def rank(rows: Sequence[Affected]) -> list[Affected]:
    return sorted(rows, key=lambda r: (LEVEL_ORDER.get(r.risk_level, 9), ENV_ORDER.get(r.environment, 9),
                                       KIND_ORDER.get(r.kind, 9), r.repo, r.path, r.line))


def dedupe(rows: Sequence[Affected]) -> list[Affected]:
    seen: set[tuple[str, str]] = set()
    out = []
    for row in rows:
        if (row.repo, row.path) not in seen:
            seen.add((row.repo, row.path))
            out.append(row)
    return out
