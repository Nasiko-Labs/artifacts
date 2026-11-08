"""Pure: the gate. Scope (new / existing / unknown_diff) from the changed-line map, de-duplication of
adjacent hits, ordering, the verdict, confidence and needs_human, and "ignore rule X". All of it is code:
the model never adds, drops or re-rates a finding.

Verdict over NEW findings only (existing ones never change it):
  fail            any critical or high; with `strict`, also any medium
  incomplete      (when not fail) the scanner failed, timed out or is missing, no scannable files,
                  or more than a third of the changed code files were skipped
  needs_attention any medium, five or more lows, or a critical/high finding whose diff is unknown
  pass            none of the above: no findings from these rules on the changed lines
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from records import Finding, GateReport

RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MERGE_GAP = 3
LOW_ATTENTION_COUNT = 5
SCANNER_PROBLEMS = frozenset({"timeout", "failed", "missing"})
_WORD = re.compile(r"[a-z0-9]+")


def classify(f: Finding, changed: frozenset[int] | None) -> Finding:
    """`new` when the finding's lines touch an added or modified line; `unknown_diff` without a patch."""
    if changed is None:
        return f.with_(scope="unknown_diff")
    lines = range(f.start_line, max(f.end_line, f.start_line) + 1)
    return f.with_(scope="new" if any(n in changed for n in lines) else "existing")


def _merged_scope(a: str, b: str) -> str:
    for scope in ("new", "existing"):
        if scope in (a, b):
            return scope
    return "unknown_diff"


def dedupe(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Same rule, same file, lines at most MERGE_GAP apart: one finding with a line range."""
    out: list[Finding] = []
    for f in sorted(findings, key=lambda x: (x.file, x.rule_id, x.start_line)):
        last = out[-1] if out else None
        if last and last.file == f.file and last.rule_id == f.rule_id and f.start_line <= last.end_line + MERGE_GAP:
            severity = min((last.severity, f.severity), key=lambda s: RANK.get(s, 2))
            out[-1] = last.with_(end_line=max(last.end_line, f.end_line), severity=severity,
                                 scope=_merged_scope(last.scope, f.scope), ignored=last.ignored and f.ignored)
        else:
            out.append(f)
    return tuple(out)


def ordered(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Severity descending, then path, then line; ids F1.. in that order."""
    ranked = sorted(findings, key=lambda f: (RANK.get(f.severity, 2), f.file, f.start_line, f.rule_id))
    return tuple(f.with_(id=f"F{i + 1}") for i, f in enumerate(ranked))


def is_incomplete(scanner_status: str, code_files: int, skipped_code: int, mode: str) -> bool:
    if mode == "findings":
        return False
    return scanner_status in SCANNER_PROBLEMS or code_files == 0 or skipped_code * 3 > code_files


def verdict(findings: Iterable[Finding], strict: bool, incomplete: bool) -> str:
    active = [f for f in findings if not f.ignored]
    new = [f for f in active if f.scope == "new"]
    blocking = {"critical", "high", "medium"} if strict else {"critical", "high"}
    if any(f.severity in blocking for f in new):
        return "fail"
    if incomplete:
        return "incomplete"
    mediums = any(f.severity == "medium" for f in new)
    lows = sum(1 for f in new if f.severity == "low")
    unknown_serious = any(f.scope == "unknown_diff" and f.severity in ("critical", "high") for f in active)
    return "needs_attention" if mediums or lows >= LOW_ATTENTION_COUNT or unknown_serious else "pass"


def confidence(verdict_: str, mode: str, skipped_code: int, fragments: bool) -> str:
    if verdict_ == "incomplete" or mode in ("diff", "code"):
        return "low"
    if mode == "findings" or skipped_code or fragments:
        return "medium"
    return "high"


def needs_human(verdict_: str, findings: Iterable[Finding]) -> bool:
    active = [f for f in findings if not f.ignored]
    return (verdict_ in ("fail", "incomplete") or any(f.severity == "critical" for f in active)
            or any(f.is_secret for f in active))


def finish(report: GateReport) -> GateReport:
    """Verdict, confidence and needs_human recomputed from the report's own findings and counts."""
    incomplete = is_incomplete(report.scanner_status, report.code_files, report.skipped_code, report.mode)
    v = verdict(report.findings, report.strict, incomplete)
    return replace(report, verdict=v, confidence=confidence(v, report.mode, report.skipped_code, report.fragments),
                   needs_human=needs_human(v, report.findings))


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    out = {s: 0 for s in RANK}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out


def matching_rules(report: GateReport, words: str) -> tuple[str, ...]:
    """Rule ids the user's words name: the whole id, or every word inside it ("the md5 rule")."""
    wanted = [w for w in _WORD.findall(words.casefold()) if w not in {"the", "rule", "rules", "for", "this", "review"}]
    if not wanted:
        return ()
    ids = dict.fromkeys(f.rule_id for f in report.findings)
    exact = tuple(i for i in ids if i.casefold() == words.strip().casefold())
    if exact:
        return exact
    return tuple(i for i in ids if all(w in i.casefold() for w in wanted))


def ignore(report: GateReport, rule_ids: Iterable[str]) -> GateReport:
    chosen = set(rule_ids)
    found = tuple(f.with_(ignored=True) if f.rule_id in chosen else f for f in report.findings)
    rules = tuple(dict.fromkeys((*report.ignored_rules, *sorted(chosen))))
    return finish(replace(report, findings=found, ignored_rules=rules))


def with_strict(report: GateReport, strict: bool) -> GateReport:
    return finish(replace(report, strict=strict))
