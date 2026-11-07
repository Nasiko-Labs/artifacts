"""Pure: the pattern rules and the scan that turns code lines into candidates.

A rule fires on one line (added or plain lines; removed lines for the diff rules), so its location and
quote are facts from code. The model never decides whether a rule exists: it only triages candidates.
The families live in rules_py / rules_js / rules_go_java / rules_config; this module owns the scan."""

from __future__ import annotations

import re
from dataclasses import dataclass

from diff_parse import ADD, CTX, DEL, PLAIN, CodeFile


@dataclass(frozen=True)
class Rule:
    id: str
    cwe: str
    severity: str
    pattern: re.Pattern
    langs: frozenset[str]          # empty = every language
    issue: str
    fix: str
    family: str
    unless: re.Pattern | None = None
    on: str = ADD                  # ADD: plain and added lines; DEL: removed lines


def rule(rule_id: str, cwe: str, severity: str, pattern: str, langs: str, issue: str, fix: str, family: str,
         unless: str | None = None, on: str = ADD) -> Rule:
    return Rule(rule_id, cwe, severity, re.compile(pattern), frozenset(langs.split()), issue, fix, family,
                re.compile(unless) if unless else None, on)


@dataclass(frozen=True)
class Candidate:
    id: str
    rule_id: str
    cwe: str
    file: str
    line: int
    quote: str
    severity: str
    issue: str
    fix: str
    family: str
    removed: bool = False

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}" + (" (removed)" if self.removed else "")


def all_rules() -> tuple[Rule, ...]:
    import rules_config
    import rules_go_java
    import rules_js
    import rules_py
    return (*rules_py.RULES, *rules_js.RULES, *rules_go_java.RULES, *rules_config.RULES)


def applies(r: Rule, lang: str) -> bool:
    """A file of unknown language gets every family; a language without a family gets only generic rules."""
    if not r.langs:
        return True
    if lang == "generic":
        return True
    return lang in r.langs


def scan_file(code: CodeFile, lang: str, start: int = 1, rules: tuple[Rule, ...] | None = None) -> list[Candidate]:
    """Candidates for one file, numbered from `start` (c1, c2, ...)."""
    found: list[Candidate] = []
    added = {" ".join(ln.text.split()) for ln in code.lines if ln.kind == ADD}
    for ln in code.lines:
        if not ln.text.strip() or ln.kind == CTX:
            continue
        for r in rules or all_rules():
            if not applies(r, lang) or not _fires(r, ln.text, ln.kind, added):
                continue
            found.append(Candidate(f"c{start + len(found)}", r.id, r.cwe, code.path, ln.no, ln.text.strip()[:300],
                                   r.severity, r.issue, r.fix, r.family, ln.kind == DEL))
    found += _file_level(code, lang, start + len(found))
    return found


def _fires(r: Rule, text: str, kind: str, added: set[str]) -> bool:
    if (r.on == DEL) != (kind == DEL):
        return False
    if kind == DEL and " ".join(text.split()) in added:  # a moved line, not a removed check
        return False
    if kind not in (PLAIN, ADD, DEL) or not r.pattern.search(text):
        return False
    return not (r.unless and r.unless.search(text))


def _file_level(code: CodeFile, lang: str, start: int) -> list[Candidate]:
    """A Dockerfile with no USER line runs as root: the finding sits on its FROM line."""
    if lang != "docker" or code.is_diff:
        return []
    if any(re.match(r"\s*USER\s+(?!root\b|0\b)\S", ln.text) for ln in code.lines):
        return []
    first = next((ln for ln in code.lines if re.match(r"\s*FROM\s", ln.text, re.IGNORECASE)), None)
    if first is None:
        return []
    return [Candidate(f"c{start}", "DOCKER-ROOT", "CWE-250", code.path, first.no, first.text.strip()[:300], "medium",
                      "The image never switches to a non-root USER, so the process runs as root.",
                      "Add a non-root user and a USER line before CMD.", "config")]
