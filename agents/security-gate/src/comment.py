"""Pure: the PR comment body, rendered in code from the findings: no secret values (only the masked
location), at most 3 lines of suggested fix per finding, no @mentions, and the closing line. The edit
check: an edited body may only remove or shorten lines of the body it came from, never add text."""

from __future__ import annotations

import re

import gate
from records import GateReport
from render import title_of, verdict_line

CLOSING = "Automated advisory scan; not a security audit."
_MENTION = re.compile(r"@(?=[A-Za-z0-9_-])")
_SPACES = re.compile(r"\s+")
MAX_COMMENT_FINDINGS = 20


def strip_mentions(text: str) -> str:
    return _MENTION.sub("", text or "")


def body(r: GateReport) -> str:
    new = r.of_scope("new")
    lines = [f"Security gate: {r.pr.label if r.pr else 'pasted input'}", verdict_line(r).replace("**", "")]
    for i, f in enumerate(new[:MAX_COMMENT_FINDINGS], 1):
        cwe = f" ({f.cwe})" if f.cwe else ""
        lines.append(f"{i}. {f.severity.capitalize()}, {title_of(f)}{cwe}, {f.where} (rule {f.rule_id})")
        if f.is_secret:
            lines.append("   A secret value is committed here (masked); rotate it.")
        if f.why:
            lines.append(f"   Why: {f.why}")
        if f.fix:
            fix = f.fix.splitlines()[:3]
            lines += [f"   Fix: {fix[0]}", *(f"   {x}" for x in fix[1:])]
    if len(new) > MAX_COMMENT_FINDINGS:
        lines.append(f"... and {len(new) - MAX_COMMENT_FINDINGS} more findings.")
    existing = r.of_scope("existing")
    if existing:
        lines.append(f"Existing issues in touched files: {len(existing)} (not part of the verdict).")
    counts = gate.counts(new)
    lines.append("Counts on changed lines: " + ", ".join(f"{n} {s}" for s, n in counts.items()))
    lines.append(CLOSING)
    return strip_mentions("\n".join(lines))


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text).strip().casefold()


def only_removes(original: str, edited: str) -> bool:
    """Every non-empty line of `edited` is (part of) a line of `original`, in the same order."""
    source = [_norm(x) for x in original.splitlines() if x.strip()]
    at = 0
    for line in (x for x in edited.splitlines() if x.strip()):
        wanted = _norm(line).rstrip(".")
        while at < len(source) and wanted not in source[at]:
            at += 1
        if at == len(source):
            return False
        at += 1
    return True


def with_closing(text: str) -> str:
    body_text = strip_mentions(text.strip())
    return body_text if body_text.endswith(CLOSING) else f"{body_text}\n{CLOSING}"
