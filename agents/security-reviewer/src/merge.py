"""Pure: candidates + triage, free-form findings, secrets and dependency results -> one finding list.

Severity comes from the rule table (or the category table for free-form findings); triage can lower it by
one level, never raise it. Then dedupe by (file, line, cwe), order critical -> info, cap at 25."""

from __future__ import annotations

from collections import Counter

import severity as sev
from grounding import reason_quotes_code
from langs import is_test_path
from rules import Candidate
from secret_scan import CRITICAL_LABELS

MAX_FINDINGS = 25
FAMILY_IMPACT = {
    "injection": "An attacker who controls the input can read or change data, or run commands on the server.",
    "deserialization": "An attacker who controls the data can run code on the server.",
    "parsing": "An attacker can read server files or reach internal services with crafted XML.",
    "auth": "Attackers can act as other users or reach data and actions they are not allowed to.",
    "crypto": "Attackers can read or forge data and credentials this code protects.",
    "files": "An attacker can read or overwrite files on the server outside the intended directory.",
    "network": "An attacker can make the server call internal services, or send users to a hostile site.",
    "config": "The deployed service or build system is more exposed to attackers than it needs to be.",
    "secrets": "Anyone with access to this code can use the credential against the system it unlocks.",
}
ROTATE = "Rotate it, it has been exposed to this chat; then load it from the environment or a secret manager."


def from_candidate(c: Candidate, triage: dict | None, norm_corpus: str, dropped: Counter) -> dict | None:
    if triage is not None and triage["verdict"] == "rejected":
        dropped["rejected"] += 1
        return None
    level = c.severity
    reachable = triage["reachable_from_input"] if triage else "unclear"
    if reachable == "no" and reason_quotes_code(triage["reason"], norm_corpus):
        level = sev.lower(level)
    confidence = {"confirmed": "confirmed", "needs_validation": "needs_validation"}.get(
        triage["verdict"] if triage else "", "likely")
    return _finding(level, c.file, c.line, c.removed, f"{c.issue} ({c.cwe})", c.cwe, confidence, c.quote, c.rule_id,
                    reachable, (triage or {}).get("impact") or FAMILY_IMPACT.get(c.family, FAMILY_IMPACT["config"]),
                    (triage or {}).get("fix") or c.fix, (triage or {}).get("fix_code"), "code", c.family)


def from_extra(e: dict) -> dict:
    level = sev.CATEGORY_SEVERITY[e["category"]]
    cwe = sev.CATEGORY_CWE[e["category"]]
    return _finding(level, e["file"], e["line"], e["removed"], e["issue"] or e["category"], cwe, "needs_validation",
                    e["quote"], f"LOGIC-{e['category'].upper()}", "unclear", e["impact"], e["fix"], e["fix_code"],
                    "logic", e["category"])


def from_secret(placeholder: str, label: str, display: str, file: str, line: int, evidence: str) -> dict:
    level = "critical" if label in CRITICAL_LABELS else "high"
    return _finding(level, file, line, False, f"Hardcoded {label.replace('-', ' ')} `{display}` (CWE-798).", "CWE-798",
                    "confirmed", evidence, f"SECRET-{label.upper()}", "n/a", FAMILY_IMPACT["secrets"], ROTATE, None,
                    "secret", "secrets", test_downgrade=False) | {"placeholder": placeholder}


def _finding(level, file, line, removed, issue, cwe, confidence, evidence, rule_id, reachable, impact, fix, fix_code,
             kind, family, test_downgrade: bool = True) -> dict:
    location = f"{file}:{line}" + (" (removed)" if removed else "") if line else file
    in_test = test_downgrade and is_test_path(file)
    return {"severity": sev.lower(level) if in_test else level, "location": location, "file": file, "line": line,
            "issue": issue, "impact": impact, "fix": fix, "fix_code": fix_code, "cwe": cwe, "confidence": confidence,
            "evidence": evidence, "rule": rule_id, "reachable_from_input": reachable, "kind": kind, "family": family,
            "test_file": in_test}


def dedupe(findings: list[dict]) -> list[dict]:
    """One finding per (file, line, cwe): the most severe, the earliest on a tie."""
    best: dict[tuple, dict] = {}
    for f in sev.ordered(findings):
        key = f["rule"] if f["kind"] == "dependency" else f["cwe"]
        best.setdefault((f["file"], f["line"], key), f)
    return sev.ordered(list(best.values()))


def finalize(findings: list[dict], dropped: Counter) -> list[dict]:
    unique = dedupe(findings)
    if len(unique) > MAX_FINDINGS:
        dropped["over_cap"] += len(unique) - MAX_FINDINGS
    return [{**f, "number": n} for n, f in enumerate(unique[:MAX_FINDINGS], start=1)]
