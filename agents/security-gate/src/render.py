"""Pure: everything the user sees, built in code from the checked report: the chat card (markdown, no raw
HTML, no images, no links except the PR's own from tool output), the summary line, and the envelope
(`summary, items, confidence, needs_human, verdict, findings, scanned, pr, coverage`)."""

from __future__ import annotations

import json
from collections.abc import Sequence

import gate
from prompts import NOT_AN_AUDIT
from records import Finding, GateReport

OFFER = ("Say \"post it\" to put this summary on the PR as a comment (you approve the exact text first), or "
         "\"ignore rule X\" to drop a rule from this review.")
_LABEL = {"pass": "PASS", "needs_attention": "NEEDS ATTENTION", "fail": "FAIL", "incomplete": "INCOMPLETE"}
MAX_EXISTING_SHOWN = 10


def clean(text: str) -> str:
    return (text or "").replace("<", "\\<").replace(">", "\\>")


def title_of(f: Finding) -> str:
    first = (f.message or f.rule_id).split(". ")[0].split(", so ")[0].rstrip(".")
    return clean(first)


def verdict_line(r: GateReport) -> str:
    new = r.of_scope("new")
    c = gate.counts(new)
    label = f"**Verdict: {_LABEL.get(r.verdict, r.verdict.upper())}**"
    if r.verdict == "fail":
        n = c["critical"] + c["high"] + (c["medium"] if r.strict else 0)
        floor = "medium" if r.strict else "high"
        why = f" ({n} {floor} or above on changed lines{', strict mode' if r.strict else ''})."
    elif r.verdict == "needs_attention":
        why = f" ({c['medium']} medium, {c['low']} low on changed lines)."
        if not new:
            why = " (a serious finding in a file whose diff could not be read)."
    elif r.verdict == "incomplete":
        why = f": {_incomplete_reason(r)}."
    else:
        why = ": no findings from these rules on the changed lines."
    return f"{label}{why} {NOT_AN_AUDIT}"


def _incomplete_reason(r: GateReport) -> str:
    if r.scanner_status == "timeout":
        return "the scan timed out"
    if r.scanner_status in ("failed", "missing"):
        return "the scanner could not run"
    if r.code_files == 0:
        return "no scannable files"
    return f"{r.skipped_code} of {r.code_files} changed code files could not be scanned"


def headline(r: GateReport) -> str:
    skipped = ", ".join(f"{p}: {why}" for p, why in r.skipped[:6]) + (" ..." if len(r.skipped) > 6 else "")
    counts = f"{len(r.scanned)} file{'s' if len(r.scanned) != 1 else ''} scanned"
    if r.skipped:
        counts += f", {len(r.skipped)} skipped ({skipped})"
    if r.mode == "findings":
        counts = f"{len(r.findings)} pasted finding{'s' if len(r.findings) != 1 else ''} triaged"
    if r.pr:
        title = f" \"{clean(r.pr.title)}\"" if r.pr.title else ""
        return f"**Security gate**: {r.pr.label}{title} ({counts})"
    what = {"diff": "pasted diff", "code": "pasted code", "findings": "pasted findings"}.get(r.mode, "input")
    return f"**Security gate**: {what} ({counts})"


def finding_block(i: int, f: Finding) -> list[str]:
    cwe = f" ({f.cwe})" if f.cwe else ""
    lines = [f"{i}. **{f.severity.capitalize()}**, {title_of(f)}{cwe}, `{f.where}` (rule `{f.rule_id}`)"]
    if f.is_secret and f.snippet:
        lines.append(f"   Value masked: `{f.snippet.replace('`', '')[:120]}`. Rotate it.")
    if f.why:
        lines.append(f"   Why: {clean(f.why)}")
    if f.fix:
        fix = f.fix.replace("```", "")
        lines.append("   Fix (suggestion, not applied):" if "\n" in fix else f"   Fix: `{fix}` (suggestion, not applied)")
        if "\n" in fix:
            lines += ["   ```", *(f"   {x}" for x in fix.splitlines()), "   ```"]
    return lines


def summary(r: GateReport) -> str:
    new = r.of_scope("new")
    where = r.pr.label if r.pr else "the pasted input"
    c = gate.counts(new)
    parts = [f"{n} {s}" for s, n in c.items() if n]
    found = ", ".join(parts) if parts else "no findings"
    return (f"Security gate for {where}: {_LABEL.get(r.verdict, r.verdict)} with {found} on changed lines "
            f"({len(r.of_scope('existing'))} existing in touched files). {NOT_AN_AUDIT}")


def card(r: GateReport, include_existing: bool = False) -> str:
    out = [headline(r), verdict_line(r)]
    if r.mode == "findings":
        out.append("All pasted findings are treated as on changed lines; severities come from the findings' own "
                   "levels (unknown levels count as medium).")
    new, unknown = r.of_scope("new"), r.of_scope("unknown_diff")
    if new:
        out.append("\n".join(x for i, f in enumerate(new, 1) for x in finding_block(i, f)))
    if unknown:
        out.append("Findings in files whose diff could not be read (not gating unless critical or high):\n"
                   + "\n".join(x for i, f in enumerate(unknown, 1) for x in finding_block(i, f)))
    existing = r.of_scope("existing")
    if existing:
        out.append(f"Existing issues in touched files: {len(existing)} (not part of the verdict)."
                   + ("" if include_existing else " Say \"show existing\" to list them."))
        if include_existing:
            out.append(existing_list(existing))
    out += _tail(r)
    return "\n\n".join(out)


def existing_list(existing: Sequence[Finding]) -> str:
    rows = [f"- {f.severity}, {title_of(f)}, `{f.where}` (rule `{f.rule_id}`)" for f in existing[:MAX_EXISTING_SHOWN]]
    more = len(existing) - MAX_EXISTING_SHOWN
    return "\n".join(rows + ([f"- and {more} more"] if more > 0 else []))


def _tail(r: GateReport) -> list[str]:
    out = []
    ignored = [f for f in r.findings if f.ignored]
    if ignored:
        rules = ", ".join(f"`{x}`" for x in r.ignored_rules)
        out.append(f"Ignored by you: {rules} ({len(ignored)} finding{'s' if len(ignored) != 1 else ''}); "
                   "the verdict was recomputed without them.")
    out += [clean(n) for n in r.notes]
    out.append(f"Confidence: {r.confidence}. These are first-party rules only; findings mean \"these rules hit\", "
               "not \"all issues\".")
    if r.pr:
        out.append(OFFER)
    return out


def envelope(r: GateReport) -> dict:
    findings = [f.to_dict() for f in r.findings]
    return {"summary": summary(r), "items": findings, "confidence": r.confidence, "needs_human": r.needs_human,
            "verdict": r.verdict, "findings": findings,
            "scanned": {"files": list(r.scanned), "skipped": [{"path": p, "reason": w} for p, w in r.skipped]},
            "pr": r.pr.to_dict() if r.pr else None, "coverage": list(r.notes), "strict": r.strict}


def as_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=2) + "\n```"
