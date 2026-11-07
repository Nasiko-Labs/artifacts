"""Pure: markdown for people (no raw HTML, no images, links only as plain text from the input) or the
envelope as one fenced JSON block for workflow steps."""

from __future__ import annotations

import json
import re

import severity as sev
from deps import NO_ADVISORY

_TAG = re.compile(r"<(?!<SECRET)[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACES = re.compile(r"[ \t]+")
DISCLAIMER = ("_Pasted code is sent to the model provider configured for this workspace; secrets were masked first. "
              "Nothing was run or fetched. Having no findings in the checks I run is not a statement that the code "
              f"is secure. {NO_ADVISORY}_")


def clean(text) -> str:
    out = _LINK.sub(r"\1", _IMAGE.sub("", str(text or "")))
    return _SPACES.sub(" ", _TAG.sub("", out)).strip()


def code_span(text: str) -> str:
    body = " ".join(str(text or "").split()).replace("`", "'")
    return f"`{body}`" if body else ""


def _fence(code: str) -> str:
    return "```\n" + str(code).replace("```", "'''").strip() + "\n```"


def _head(f: dict) -> str:
    label = " (test/example file)" if f.get("test_file") else ""
    return f"**{f['number']}. {f['severity'].capitalize()} · {f['cwe']} · `{clean(f['location'])}`**{label}"


def finding_block(f: dict) -> str:
    lines = [f"{_head(f)} {clean(f['issue'])}", f"Impact: {clean(f['impact'])}", f"Fix: {clean(f['fix'])}"]
    if f.get("evidence"):
        lines.append(f"Evidence: {code_span(f['evidence'])}")
    if f.get("reachable_from_input") in ("yes", "no"):
        lines[-1] += f" · reachable from input: {f['reachable_from_input']}"
    block = "\n".join(lines)
    return block + ("\n" + _fence(f["fix_code"]) if f.get("fix_code") else "")


def render(envelope: dict, floor: str | None = None, notes: tuple[str, ...] = ()) -> str:
    findings = [f for f in envelope["findings"] if floor is None or sev.at_or_above(f["severity"], floor)]
    confirmed = [f for f in findings if f["confidence"] != "needs_validation" and f["severity"] != "info"]
    unverified = [f for f in findings if f["confidence"] == "needs_validation" and f["severity"] != "info"]
    info = [f for f in findings if f["severity"] == "info"]
    out = [_title(envelope, findings, floor)]
    out += [finding_block(f) for f in confirmed]
    if unverified:
        out.append("**Needs validation** (I could not confirm these from the code shown):\n\n"
                   + "\n\n".join(finding_block(f) for f in unverified))
    if info:
        out.append("**For review:** " + "; ".join(f"{clean(f['issue'])} (`{clean(f['location'])}`)" for f in info))
    out += [*notes, _coverage(envelope), DISCLAIMER]
    return "\n\n".join(p for p in out if p)


def _title(envelope: dict, findings: list[dict], floor: str | None) -> str:
    view = f" (showing {floor} and above: {len(findings)} of {len(envelope['findings'])})" if floor else ""
    head = f"**Security review**{view} — {clean(envelope['summary'])}"
    if envelope.get("secrets_found"):
        head += " Every secret found was masked before the model saw the code: rotate it, it has been exposed to this chat."
    if not envelope["findings"]:
        head += " That is not a statement that the code is secure."
    return head + f" Confidence: {envelope['confidence']}."


def _coverage(envelope: dict) -> str:
    cov = envelope["coverage"]
    parts = [f"{cov['files_reviewed']} file{'s' if cov['files_reviewed'] != 1 else ''} reviewed"]
    if cov.get("focus"):
        parts.append(f"focus: {cov['focus']}")
    for s in cov["files_skipped"]:
        what = "" if s.get("lines") == "all" else f" lines {s['lines']}"
        parts.append(f"skipped {clean(s['file'])}{what} ({s['reason']})")
    if cov["no_rule_family"]:
        parts.append("no rule family for " + ", ".join(clean(p) for p in cov["no_rule_family"])
                     + " (only generic rules and the model's sweep)")
    parts.append(f"dependencies: {cov['dependencies_checked']}")
    dropped = envelope.get("dropped") or {}
    if dropped:
        names = {"rejected": "rejected on triage", "unverified": "dropped (quote or impact not verified)",
                 "fix_code": "fix code removed", "not_triaged": "not triaged", "over_cap": "over the 25 cap"}
        parts.append("candidates " + ", ".join(f"{n} {names.get(k, k)}" for k, n in dropped.items()))
    return "_Coverage: " + "; ".join(parts) + "._"


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
