"""Pure: the markdown replies (code-rendered: no raw HTML, no images; snippets and evidence in code spans or
fences) and the json block for workflow steps."""

from __future__ import annotations

import json

import prompts
import scoring
from checktext import SPECS
from models import Envelope, Finding

EVIDENCE_SHOWN = 110


def _cell(text: str) -> str:
    clean = " ".join(text.split()).replace("|", "\\|").replace("`", "'")
    return clean if len(clean) <= EVIDENCE_SHOWN else clean[:EVIDENCE_SHOWN] + "…"


def header(env: Envelope) -> str:
    where = env.page_url if env.page_url != "unknown" else "pasted page"
    return (f"**SEO audit: {where}** · score **{env.score}/100** ({env.band}) · on-page {env.on_page_score} · "
            f"AI-readiness {env.geo_score}")


def table(items: list[Finding]) -> str:
    if not items:
        return "No issues found by the checks."
    rows = [f"| {f.id} | {f.severity} | {_cell(f.title)} | `{_cell(f.evidence)}` |" for f in items]
    return "\n".join(["| # | Severity | Issue | Evidence |", "|---|---|---|---|", *rows])


def crawler_line(env: Envelope) -> str:
    if not env.ai_crawlers:
        return "AI crawlers: robots.txt not read."
    return "AI crawlers: " + ", ".join(f"{k} {v}" for k, v in env.ai_crawlers.items()) + "."


def proposals(env: Envelope) -> str:
    p = env.proposals
    lines = []
    if p.title:
        lines.append(f"**Proposed title ({p.title_chars} chars):**\n```text\n{p.title}\n```")
    if p.meta:
        lines.append(f"**Proposed meta description ({p.meta_chars} chars):**\n```text\n{p.meta}\n```")
    if p.json_ld:
        lines.append(f"**Proposed JSON-LD (built only from facts on the page):**\n```json\n{p.json_ld}\n```")
    lines += [f"_{note}_" for note in p.notes]
    return "\n\n".join(lines)


def audit(env: Envelope) -> str:
    fixes = scoring.fix_list(env.items)
    fix_line = "**Fix first:** " + " ".join(f"{i}) {f.title} ({f.id})" for i, f in enumerate(fixes, 1)) if fixes else ""
    topic = f"Topic: {env.primary_topic} ({'inferred' if env.topic_inferred else 'your keyword'})." \
        if env.primary_topic else ""
    diff = diff_line(env.diff) if env.diff else ""
    parts = [header(env), env.summary, diff, " ".join(env.coverage) + " " + prompts.JS_NOTE, topic, table(env.items),
             fix_line, proposals(env), crawler_line(env), prompts.GEO_LABEL, prompts.NOT_MEASURED,
             f"Confidence: {env.confidence}."]
    return "\n\n".join(p for p in parts if p)


def diff_line(diff: dict[str, list[str]]) -> str:
    fixed, new, same = diff.get("fixed", []), diff.get("new", []), diff.get("unchanged", [])
    detail = "; ".join(f"{label}: {', '.join(ids)}" for label, ids in (("fixed", fixed), ("new", new)) if ids)
    return f"**Compared with the previous audit:** {len(fixed)} fixed, {len(new)} new, {len(same)} unchanged." + \
        (f" ({detail})" if detail else "")


def filtered(env: Envelope, severities: tuple[str, ...]) -> str:
    items = [f for f in env.items if f.severity in severities]
    label = " / ".join(severities)
    return "\n\n".join([header(env), f"Findings at {label} severity: {len(items)}.", table(items)])


def explain(env: Envelope, finding_id: str) -> str:
    found = next((f for f in env.items if f.id.casefold() == finding_id.casefold()), None)
    if found is None:
        ids = ", ".join(f.id for f in env.items) or "none"
        return f"There is no finding {finding_id.upper()} in the last audit. Findings: {ids}."
    spec = SPECS.get(found.check)
    why = spec.why if spec else "The content read flagged this passage; the quote is checked to be on the page."
    return (f"**{found.id} · {found.title}** ({found.severity}, check {found.check})\n\n**Why it matters:** {why}\n\n"
            f"**Evidence:** `{_cell(found.evidence)}`\n\n**Fix:** {found.fix}")


def breakdown(env: Envelope, area: str | None = None) -> str:
    rows = [f"| {c.id} | {SPECS[c.id].name} | {c.weight} | {c.outcome}{' (' + c.reason + ')' if c.reason else ''} |"
            for c in env.checks if area is None or c.area == area]
    lead = f"{header(env)}\n\nscore = 100 x passed weight / applicable weight; not-applicable checks are excluded."
    if area == "geo":
        lead += f"\n\n{crawler_line(env)}\n\n{prompts.GEO_LABEL}"
    return "\n\n".join([lead, "\n".join(["| Check | What | Weight | Result |", "|---|---|---|---|", *rows])])


def robots_view(env: Envelope) -> str:
    blocked = [f for f in env.items if f.check == "G1"]
    lines = [crawler_line(env), *[f"- {f.title}: `{_cell(f.evidence)}`" for f in blocked]]
    return "\n\n".join(lines)


def json_block(env: Envelope) -> str:
    return "```json\n" + json.dumps(env.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n```"
