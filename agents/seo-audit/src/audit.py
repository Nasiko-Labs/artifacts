"""One audit, end to end: parse -> content read (model) -> checks -> score -> rewrites (model) -> envelope.
The checks and the score never see model output except intent/answer-first, which only decide W1/G3/D1."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import checks_geo
import checks_onpage
import quality
import rewrite
import scoring
from checkbase import Context
from llm import TurnDeps
from models import Envelope, Finding
from parse import parse_page

JS_SHELL_WORDS = 100
JS_SHELL_SCRIPTS = 3


@dataclass(frozen=True)
class Source:
    html: str
    page_url: str = ""
    keyword: str = ""
    robots_txt: str | None = None
    llms_found: bool | None = None
    fetched: bool = False
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


async def run(deps: TurnDeps, src: Source, context: tuple[str, ...]) -> tuple[Envelope, str]:
    """(envelope, visible text kept for follow-ups)."""
    html = src.html.replace("\r\n", "\n")
    facts, text = parse_page(html, src.page_url)
    read = await quality.read(deps, facts, text, context)
    ctx = Context(page_url=src.page_url, keyword=src.keyword, intent=read.intent, answer_first=read.answer_first,
                  robots_txt=src.robots_txt, llms_found=src.llms_found)
    path = urlsplit(src.page_url).path or "/"
    geo, crawlers = checks_geo.run(facts, ctx, path)
    onpage = checks_onpage.run(facts, ctx)
    checks = [*onpage, *geo]
    items, _ = scoring.findings(checks, html, src.robots_txt or "")
    items = items + _quality_items(read, len(items))
    score = scoring.score_of(checks)
    band = scoring.band_of(score, checks)
    want_json_ld = not facts.jsonld_types
    topic = read.primary_topic or (facts.h1s[0].text if facts.h1s else (facts.title.text if facts.title else ""))
    proposals = await rewrite.propose(deps, facts, text, html, src.keyword, topic, want_json_ld, context)
    js_shell = facts.words < JS_SHELL_WORDS and facts.scripts >= JS_SHELL_SCRIPTS
    confidence = _confidence(src, facts.truncated, js_shell, read)
    critical = any(f.severity == "critical" for f in items)
    envelope = Envelope(
        summary=scoring.summary(score, band, items, crawlers), items=items, confidence=confidence,
        needs_human=confidence == "low" or critical, score=score, on_page_score=scoring.score_of(onpage),
        geo_score=scoring.score_of(geo), band=band, not_applicable=scoring.not_applicable(checks),
        coverage=_coverage(src, facts.truncated, js_shell, read), primary_topic=src.keyword or topic or None,
        topic_inferred=not src.keyword, keyword=src.keyword, page_url=src.page_url or "unknown", intent=read.intent,
        proposals=proposals, page=facts, checks=checks, ai_crawlers=crawlers, quality_dropped=read.dropped)
    return envelope, text


def _quality_items(read: quality.QualityRead, offset: int) -> list[Finding]:
    return [Finding(id=f"F{offset + i}", check="Q", area="on_page", severity="low", title=label,
                    evidence=quote, fix="Rewrite this passage to be specific and to answer the page's question.",
                    weight=0) for i, (label, quote) in enumerate(read.issues, 1)]


def _confidence(src: Source, truncated: bool, js_shell: bool, read: quality.QualityRead) -> str:
    if truncated or src.truncated or js_shell:
        return "low"
    if src.robots_txt is None or src.llms_found is None or not src.keyword or read.dropped or read.failed:
        return "medium"
    return "high"


def _coverage(src: Source, truncated: bool, js_shell: bool, read: quality.QualityRead) -> list[str]:
    if src.fetched:
        robots = "robots.txt" if src.robots_txt is not None else "robots.txt: not found"
        llms = "llms.txt" if src.llms_found else "llms.txt: not found"
        lines = [f"Read: the page, {robots}, {llms}."]
    else:
        lines = ["Read: the pasted HTML (robots.txt and llms.txt not read)."]
    lines += list(src.notes)
    if truncated or src.truncated:
        lines.append("The HTML looks truncated, so some findings may be artifacts of the cut.")
    if js_shell:
        lines.append("The page has very little text and several scripts: it is probably built by JavaScript, "
                     "which isn't seen here.")
    if read.failed:
        lines.append("The content read failed, so W1, G3 and D1 are not applicable.")
    if read.dropped:
        lines.append(f"{read.dropped} content issue(s) were dropped: their quotes are not on the page.")
    return lines
