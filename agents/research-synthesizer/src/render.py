"""Pure: the markdown brief, rendered in code from the stored brief. No HTML, no images; text from the sources
is shown only as quotes. Views (zero LLM calls): default, conflicts, quotes, high, sources, unknowns."""

from __future__ import annotations

import json
import re

import envelope
from model import Claim, Finding, Source

VIEWS = ("default", "conflicts", "quotes", "high", "sources", "unknowns")
_HTML = re.compile(r"<[^>\n]{1,200}>")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")


def clean(text: str) -> str:
    """Single-line, no HTML tags, no image embeds."""
    return " ".join(_IMAGE.sub(r"\1", _HTML.sub("", text or "")).split())


def render(brief: dict, view: str = "default") -> str:
    sources = [Source.from_dict(s) for s in brief["sources"]]
    findings = [Finding.from_dict(f) for f in brief["findings"]]
    claims = {c["id"]: Claim.from_dict(c) for c in brief["claims"]}
    if view == "conflicts":
        return _disagreements(findings, claims) or "No conflicts: no two sources were found to disagree."
    if view == "sources":
        return _sources(sources, brief)
    if view == "unknowns":
        return "## Unknowns\n" + ("\n".join(f"- {clean(u)}" for u in brief["unknowns"]) or "None found.")
    if view == "high":
        high = [f for f in findings if f.confidence == "high"]
        return _findings(high, claims, all_quotes=True) if high else "No finding has high confidence."
    body = [_header(brief, sources)]
    if brief["bottom_line"] and view == "default":
        body.append(f"**Bottom line** {clean(brief['bottom_line'])}")
    body += [_findings(findings, claims, all_quotes=view == "quotes"), _disagreements(findings, claims),
             "## Unknowns\n" + ("\n".join(f"- {clean(u)}" for u in brief["unknowns"]) or "None found."),
             _sources(sources, brief), _footer(brief)]
    return "\n\n".join(b for b in body if b)


def render_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, ensure_ascii=False, indent=2) + "\n```"


def _header(brief: dict, sources: list[Source]) -> str:
    question = clean(brief["question"]) or "what these sources say"
    words = sum(s.words for s in sources if s.read != "not_read")
    count = "single source" if len(sources) == 1 else f"{len(sources)} sources"
    line = f"# Research brief: {question}\n_({count}, ~{words:,} words; overall confidence: {brief['confidence']})_"
    if not brief["question"]:
        line += "\n_No research question was given, so this reports what the sources say, by topic._"
    return line


def _who(finding: Finding) -> str:
    ids = list(finding.source_ids)
    return ids[0] + " says" if len(ids) == 1 else ", ".join(ids[:-1]) + f" and {ids[-1]} say"


def _findings(findings: list[Finding], claims: dict[str, Claim], all_quotes: bool) -> str:
    if not findings:
        return "## Findings\nNo finding could be backed by the sources."
    lines = ["## Findings"]
    for n, f in enumerate(findings, start=1):
        statement = f"{_who(f)}: {clean(f.statement)}" if f.is_view else clean(f.statement)
        lines.append(f"{n}. {statement} [{', '.join(f.source_ids)}] ({f.confidence})")
        shown = [claims[i] for i in f.claim_ids if i in claims]
        if not all_quotes:  # the first quote of each of the first two sources
            firsts = {}
            for c in shown:
                firsts.setdefault(c.source_id, c)
            shown = list(firsts.values())[:2]
        for c in shown:
            tag = f" ({c.kind})" if c.kind in ("opinion", "prediction", "recommendation") else ""
            lines.append(f'   - {c.source_id}{tag}: "{clean(c.quote)}"')
    return "\n".join(lines)


def _disagreements(findings: list[Finding], claims: dict[str, Claim]) -> str:
    rows = []
    for f in findings:
        if not f.conflict:
            continue
        a, b = claims.get(f.conflict["a"]), claims.get(f.conflict["b"])
        if a is None or b is None:
            continue
        rows.append(f"- {clean(f.statement)}\n   - {a.source_id}: \"{clean(a.quote)}\"\n   - {b.source_id}: "
                    f"\"{clean(b.quote)}\"\n   - Difference: {clean(f.conflict['difference'])}")
    return "## Where sources disagree\n" + "\n".join(rows) if rows else ""


def _sources(sources: list[Source], brief: dict) -> str:
    lines = ["## Sources read"]
    for s in sources:
        meta = ", ".join(x for x in (s.type, s.date, s.author) if x)
        status = "" if s.read == "full" else f" ({s.read.replace('_', ' ')})"
        lines.append(f"- {s.id} {clean(s.label)}" + (f" ({meta})" if meta else "") + status)
    not_read = brief.get("not_read") or []
    lines.append("Not read: " + (", ".join(f"{sid} ({why})" for sid, why in not_read) or "none"))
    lines.append(envelope.scope(sources))
    return "\n".join(lines)


def _footer(brief: dict) -> str:
    d = brief["drops"]
    bits = []
    if d.get("quotes"):
        bits.append(f"{_claims(d['quotes'])} left out: the quote or a number isn't in the source")
    if d.get("irrelevant"):
        bits.append(f"{_claims(d['irrelevant'])} off the question")
    if d.get("unclustered"):
        bits.append(f"{_claims(d['unclustered'])} not grouped into a finding")
    if d.get("relations"):
        bits.append(f"{d['relations']} agree/conflict labels rejected by the checks")
    meta = f"_({'; '.join(bits)}.)_" if bits else ""
    return "\n".join(x for x in (meta, *brief.get("notes", [])) if x)


def _claims(n: int) -> str:
    return "1 claim" if n == 1 else f"{n} claims"
