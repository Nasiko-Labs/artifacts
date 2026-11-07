"""Pure: the Finding record (a smell or an assumption candidate) and the per-statement rule context."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from sqlglot import exp

from ast_util import alias_map, from_source, qualifier
from schema import Schema

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class Finding:
    rule: str                 # S01..S24, A01..A10, or M (the model's own assumption)
    kind: str                 # smell | assumption
    text: str
    evidence: str
    severity: str = ""        # smells only
    suggestion: str = ""
    checked: bool = False     # an assumption the pasted schema confirms
    note: str = ""
    source: str = "rule"
    cid: str = ""             # unique per statement, e.g. S07-1; assigned after detection
    so_what: str = ""         # the model's one sentence, checked
    hint: str = ""            # the model's rewrite hint, checked and labelled untested

    def with_id(self, cid: str) -> Finding:
        return replace(self, cid=cid)

    def to_dict(self) -> dict:
        return {"rule": self.rule, "kind": self.kind, "text": self.text, "evidence": self.evidence,
                "severity": self.severity, "suggestion": self.suggestion, "checked": self.checked,
                "note": self.note, "source": self.source, "cid": self.cid, "so_what": self.so_what,
                "hint": self.hint}

    @staticmethod
    def from_dict(data: dict) -> Finding:
        return Finding(**{k: data[k] for k in Finding.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class Ctx:
    statement: exp.Expression
    dialect: str = ""
    schema: Schema = field(default_factory=Schema)
    all_statements: tuple[exp.Expression, ...] = ()


def smell(rule: str, severity: str, text: str, evidence: str, suggestion: str) -> Finding:
    return Finding(rule, "smell", text, evidence, severity, suggestion)


def assumption(rule: str, text: str, evidence: str, checked: bool = False, note: str = "") -> Finding:
    return Finding(rule, "assumption", text, evidence, checked=checked, note=note)


def table_for(column: exp.Column, select: exp.Select | None) -> str:
    """The table a column belongs to: by its qualifier, or the only source when unqualified."""
    if select is None:
        return ""
    mapping = alias_map(select)
    q = qualifier(column)
    if q:
        return mapping.get(q, q)
    if len(mapping) == 1:
        first = from_source(select)
        return first[0] if first else ""
    return ""


def number(findings: list[Finding]) -> list[Finding]:
    """Gives each finding a unique id (rule-n) and drops exact duplicates (same rule and evidence)."""
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    out = []
    for f in findings:
        key = (f.rule, f.evidence)
        if key in seen:
            continue
        seen.add(key)
        counts[f.rule] = counts.get(f.rule, 0) + 1
        out.append(f.with_id(f"{f.rule}-{counts[f.rule]}"))
    return out


def by_severity(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
