"""Pure: the answer-engine (GEO/AEO) readiness checks. Practice-based heuristics, labelled as such in the
reply. AI-crawler blocking is reported at info severity and never scored: it can be a deliberate choice."""

from __future__ import annotations

import robots
from checkbase import Context, na, ok, result
from models import CheckResult, PageFacts

QUESTION_HEADINGS_MIN = 2
CITABLE_FACTS_MIN = 3
PARAGRAPH_WORDS_MAX = 90
ANSWER_TYPES = {"FAQPage", "HowTo", "Article", "BlogPosting", "NewsArticle", "TechArticle", "Product"}


def run(facts: PageFacts, ctx: Context, path: str = "/") -> tuple[list[CheckResult], dict[str, str]]:
    crawlers, g1 = _crawlers(ctx, path)
    checks = [*g1, _llms(ctx), _answer_first(ctx), _questions(facts), _schema(facts), _fresh(facts),
              _author(facts), _facts(facts), _scannable(facts)]
    return checks, crawlers


def _crawlers(ctx: Context, path: str) -> tuple[dict[str, str], list[CheckResult]]:
    if ctx.robots_txt is None:
        return {}, [na("G1", "robots.txt not read")]
    status: dict[str, str] = {}
    blocked: list[CheckResult] = []
    for name, verdict in robots.ai_crawlers(ctx.robots_txt, path).items():
        status[name] = "allowed" if verdict.allowed else "blocked (intentional?)"
        if not verdict.allowed:
            blocked.append(result("G1", "fail", f"{name} is blocked by robots.txt (intentional?)",
                                  verdict.rule.line, "quote"))
    return status, blocked or [ok("G1", "no AI crawler is blocked")]


def _llms(ctx: Context) -> CheckResult:
    if ctx.llms_found is None:
        return na("G2", "llms.txt not read (pasted HTML)")
    return ok("G2", "/llms.txt found") if ctx.llms_found else \
        result("G2", "fail", "No llms.txt", "/llms.txt not found on the site", "absence")


def _answer_first(ctx: Context) -> CheckResult:
    if ctx.answer_first is None:
        return na("G3", "content read unavailable")
    return ok("G3", "the opening answers the page's question") if ctx.answer_first else \
        result("G3", "fail", "The page does not answer first", "the first 150 words do not answer the title's question")


def _questions(facts: PageFacts) -> CheckResult:
    if len(facts.question_headings) >= QUESTION_HEADINGS_MIN or facts.faq_block:
        return ok("G4", f"{len(facts.question_headings)} question headings" + (", FAQ block" if facts.faq_block else ""))
    return result("G4", "fail", "Few question-style headings",
                  f"{len(facts.question_headings)} question-style H2/H3 headings, no FAQ block")


def _schema(facts: PageFacts) -> CheckResult:
    if not facts.jsonld_types:
        return result("G5", "fail", "No structured data for answer engines", "no valid JSON-LD block", "absence")
    fitting = ANSWER_TYPES & set(facts.jsonld_types)
    needs_faq = facts.faq_block and facts.question_headings and "FAQPage" not in facts.jsonld_types
    if fitting and not needs_faq:
        return ok("G5", "JSON-LD: " + ", ".join(sorted(fitting)))
    why = "the page has Q&A but no FAQPage schema" if needs_faq else \
        "JSON-LD has no FAQ, HowTo, Article or Product type: " + ", ".join(facts.jsonld_types)
    return result("G5", "fail", "Structured data does not describe the answers", why)


def _fresh(facts: PageFacts) -> CheckResult:
    if facts.time_tag:
        return ok("G6", facts.time_tag, "quote")
    if facts.meta_date:
        return ok("G6", facts.meta_date, "quote")
    if facts.jsonld_dates:
        return ok("G6", "JSON-LD date: " + facts.jsonld_dates[0])
    return result("G6", "fail", "No date on the page", "no <time>, article date meta or JSON-LD date", "absence")


def _author(facts: PageFacts) -> CheckResult:
    if facts.author_signal:
        kind = "fact" if facts.author_signal.startswith("JSON-LD") else "quote"
        return ok("G7", facts.author_signal, kind)
    return result("G7", "fail", "No author or organisation signal",
                  "no byline, author meta, About link or Organization schema", "absence")


def _facts(facts: PageFacts) -> CheckResult:
    found = facts.citable_facts
    if len(found) >= CITABLE_FACTS_MIN:
        return ok("G8", f"{len(found)} specific figures/dates/sources, e.g. " + "; ".join(found[:3]))
    return result("G8", "fail", f"Only {len(found)} citable facts",
                  f"{len(found)} specific figures, dates or named sources in the text")


def _scannable(facts: PageFacts) -> CheckResult:
    median = facts.median_paragraph_words
    structured = facts.lists + facts.tables
    short = median is None or median <= PARAGRAPH_WORDS_MAX
    if structured and short:
        return ok("G9", f"{facts.lists} lists, {facts.tables} tables, median paragraph {median or 0} words")
    if not structured and median is None:
        return na("G9", "no paragraphs, lists or tables")
    return result("G9", "fail", "Hard to scan",
                  f"{facts.lists} lists, {facts.tables} tables, median paragraph {median or 0} words")
