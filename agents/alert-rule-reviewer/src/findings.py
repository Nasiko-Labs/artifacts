"""Pure: the `Finding` record, how a rule builds one (evidence = the verbatim config line), and the final
checks: evidence must be a substring of the paste, one finding per (rule, target, severity), sorted by
severity, then cross-alert rules first, then file line."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import catalog
from alerts import Alert, Parsed

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
CROSS_RULES = frozenset({"AR007", "AR014", "AR015", "AM001", "AM002", "AM004", "DD008"})
NOISY_RULES = frozenset({"AR001", "AR002", "AR009", "AR010", "AR013", "DD006", "CW001", "CW002"})


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    target: str
    file: str
    line: int
    message: str
    evidence: str
    fix_hint: str
    alert_id: str = ""
    related: str = ""      # another alert id the finding points at (AR007: the first of a duplicate pair)
    id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Finding:
        return Finding(**{k: data.get(k, 0 if k == "line" else "") for k in Finding.__dataclass_fields__})


@dataclass(frozen=True)
class Change:
    target: str
    finding_ids: tuple[str, ...]
    change_type: str
    before: str
    after: str
    note: str

    def to_dict(self) -> dict:
        return {**asdict(self), "finding_ids": list(self.finding_ids)}


def for_alert(parsed: Parsed, rule_id: str, severity: str, alert: Alert, key: str, message: str) -> Finding:
    line = alert.line_of(key)
    return Finding(rule_id=rule_id, severity=severity, target=alert.name, file=alert.file, line=line,
                   message=message, evidence=parsed.line_text(alert.file, line), fix_hint=catalog.RULES[rule_id].fix,
                   alert_id=alert.id)


def at_line(parsed: Parsed, rule_id: str, severity: str, target: str, file: str, line: int, message: str) -> Finding:
    return Finding(rule_id=rule_id, severity=severity, target=target, file=file, line=line, message=message,
                   evidence=parsed.line_text(file, line), fix_hint=catalog.RULES[rule_id].fix)


def finalize(found: list[Finding], paste: str) -> tuple[list[Finding], int]:
    """(checked findings with ids F1.., dropped count). Dropped: evidence not verbatim in the paste."""
    kept: dict[tuple[str, str, str], Finding] = {}
    dropped = 0
    for finding in found:
        if not finding.evidence or finding.evidence not in paste:
            dropped += 1
            continue
        kept.setdefault((finding.rule_id, finding.target, finding.severity), finding)
    ordered = sorted(kept.values(), key=lambda f: (SEVERITY_ORDER.get(f.severity, 3), f.rule_id not in CROSS_RULES,
                                                   f.file, f.line, f.rule_id))
    return [replace(f, id=f"F{n}") for n, f in enumerate(ordered, start=1)], dropped


def noisy(found: list[Finding]) -> list[dict]:
    """Alerts flap-prone by construction: name + the noisy rule ids that fired on them."""
    by_target: dict[str, list[str]] = {}
    for finding in found:
        if finding.rule_id in NOISY_RULES:
            ids = by_target.setdefault(finding.target, [])
            if finding.rule_id not in ids:
                ids.append(finding.rule_id)
    return [{"alert": name, "rules": ids} for name, ids in by_target.items()]


def counts(found: list[Finding]) -> dict[str, int]:
    return {level: sum(1 for f in found if f.severity == level) for level in SEVERITY_ORDER}
