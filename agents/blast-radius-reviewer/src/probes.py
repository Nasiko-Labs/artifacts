"""Pure: the 12 risk probes as data, and which of them fire on the census. Firing, severity and the search
token are decided here, in code; the model only judges a fired probe."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import commands
import excerpt
from diff_parse import DiffFile
from facts import Fact


@dataclass(frozen=True)
class Probe:
    id: str
    kind: str
    question: str
    outside: str


PROBES = {p.id: p for p in (
    Probe("R1", "callers", "Does code outside this diff still call the old name or signature?",
          "Callers in other modules, other repos or docs may still use the old name or signature."),
    Probe("R2", "wire format", "Could another client or consumer still send or read the old field, route or code?",
          "Other clients (web, mobile builds still on the old version, partners), webhooks and queue consumers."),
    Probe("R3", "stored state", "Could existing rows, replicas, backfills or a rollback break on this change?",
          "Existing rows, backfills, replicas and a rollback to the previous release."),
    Probe("R4", "config and env", "Is the old key or default still set or read somewhere outside this diff?",
          "Deploy config, CI, Dockerfile/compose/helm, and a second service that reads the same key."),
    Probe("R5", "rule living twice", "Does the old value live on in another copy of this rule?",
          "Other copies of the rule: fallback paths, tests, fixtures, docs and prompts."),
    Probe("R6", "behaviour default", "Do callers rely on the old default, ordering, null handling or error type?",
          "Callers that relied on the old behaviour."),
    Probe("R7", "dependency", "Does the new version change behaviour this code or its users rely on?",
          "The library's changelog and source at the pinned version, and transitive users."),
    Probe("R8", "timing and rollout", "Could processes that restart separately, or concurrent runs, break?",
          "Processes that restart separately (old workers on new data) and duplicate execution."),
    Probe("R9", "tests and fixtures", "Do tests or fixtures outside this diff encode the old behaviour?",
          "Tests and fixtures that encode the old behaviour or shape."),
    Probe("R10", "observability", "Do dashboards, alerts or log parsers match on the old name?",
          "Dashboards, alerts and log parsers."),
    Probe("R11", "generated code", "Is generated or vendored code now out of step with its source?",
          "Generators, committed clients and bundles."),
    Probe("R12", "deletion", "Does anything still import, build or link the deleted or renamed file?",
          "Imports, build config, docs and CI paths."),
)}
PRIORITY = ["R3", "R2", "R4", "R1", "R12", "R6", "R8", "R10", "R5", "R11", "R7", "R9"]
LEVELS = ["low", "medium", "high"]
_ENTRY = re.compile(r"(^|/)(__main__\.py|main\.\w+|index\.\w+|app\.\w+|server\.\w+|manage\.py|wsgi\.py|asgi\.py|"
                    r"dockerfile|setup\.py|package\.json)$", re.IGNORECASE)
_PROMPT_NAMES = re.compile(r"\.prompt\b|system[\w-]*\.md|AGENTS\.md|CLAUDE\.md|skill|runbook", re.IGNORECASE)


@dataclass(frozen=True)
class Fired:
    id: str
    probe: str
    kind: str
    file: str
    line: int | None
    token: str
    change: str
    severity: str
    detail: str
    commands: tuple[str, ...]
    destructive: bool
    excerpt: str

    def to_dict(self) -> dict:
        return {**asdict(self), "commands": list(self.commands)}


def probe_for(fact: Fact, files: dict[str, DiffFile]) -> tuple[str, str] | None:
    """(probe id, default severity) for one census fact, or None when no probe fires on it."""
    flags = set(fact.flags)
    kind = fact.kind
    if kind == "symbol":
        return ("R2", "high") if fact.token.startswith("/") else ("R1", "high" if "public" in flags else "medium")
    if kind == "wire":
        return "R2", "high"
    if kind == "sql":
        return "R3", "high" if "destructive" in flags else "medium"
    if kind in ("env", "config_key"):
        return "R4", "high" if flags & {"secret", "no_fallback"} else "medium"
    if kind == "literal":
        return ("R10", "low") if "observability" in flags else ("R5", "medium")
    if kind == "behaviour":
        return "R6", "medium"
    if kind in ("dep", "lock"):
        return "R7", "medium" if "major" in flags else "low"
    if kind == "timing":
        return "R8", "medium"
    if kind == "test_expected":
        return "R9", "medium"
    if kind in ("generated", "schema_no_gen"):
        return "R11", "medium"
    if kind == "deleted":
        f = files.get(fact.file)
        entry = bool(_ENTRY.search(fact.file)) or (f is not None and f.kind in ("config", "ci", "build"))
        return "R12", "high" if entry else "medium"
    return None


def fire(facts: list[Fact], files: list[DiffFile], diff_text: str, context_text: str = "") -> list[Fired]:
    by_path = {f.path: f for f in files} | {f.old_path: f for f in files if f.old_path}
    prompt_in_view = any(f.kind == "prompt" for f in files) or bool(_PROMPT_NAMES.search(context_text))
    candidates = []
    for fact in facts:
        hit = probe_for(fact, by_path)
        if hit and fact.token and fact.token in diff_text:
            candidates.append((fact, *hit))
    candidates += _untested(facts, candidates, diff_text)
    candidates.sort(key=lambda c: (PRIORITY.index(c[1]), -LEVELS.index(c[2])))
    fired, seen, counts = [], set(), {}
    for fact, probe, severity in candidates:
        key = fact.token if probe not in ("R7", "R9", "R11") else (probe, fact.token)
        if key in seen:
            continue
        seen.add(key)
        counts[probe] = counts.get(probe, 0) + 1
        checks = commands.for_probe(probe, fact.token, diff_text, fact.detail, prompt_in_view)
        if fact.kind == "lock":
            checks = [f"diff the resolved versions in {fact.file}"]
        fired.append(Fired(f"{probe}-{counts[probe]}", probe, PROBES[probe].kind, fact.file, fact.line, fact.token,
                           fact.change, severity, fact.detail, tuple(checks), "destructive" in fact.flags,
                           excerpt.around(by_path.get(fact.file), fact.line)))
    return fired


def _untested(facts: list[Fact], candidates: list, diff_text: str) -> list:
    """R9 fires for a source change with no test in the diff only when that change also fired a probe, so a
    purely local edit (a renamed local variable) fires nothing."""
    untested = [f for f in facts if f.kind == "untested"]
    if not untested or not candidates:
        return []
    fired_files = {c[0].file for c in candidates if c[1] not in ("R7", "R11")}
    if not fired_files:
        return []
    fact = untested[0]
    token = next((c[0].token for c in candidates if c[0].file in fired_files and c[1] in ("R1", "R6")), fact.token)
    first = next(iter(sorted(fired_files)))
    return [(Fact("untested", first, None, token, "changed", fact.detail), "R9", "low")] \
        if token in diff_text else []

