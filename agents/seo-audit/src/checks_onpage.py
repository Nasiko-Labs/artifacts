"""Pure: the on-page checks (ids, severities and weights as in the plan's table). Every result carries its
evidence: a verbatim excerpt of the page (`quote`), a code-made statement of absence, or a computed count."""

from __future__ import annotations

from urllib.parse import urlsplit

from checkbase import Context, na, ok, result
from models import CheckResult, PageFacts

TITLE_OK, TITLE_WARN_MAX, TITLE_FAIL_MIN = (30, 60), 70, 20
META_OK = (70, 160)
ALT_COVERAGE_MIN = 0.9
INTERNAL_LINKS_MIN = 3
GENERIC_SHARE_MAX = 0.10
INFORMATIONAL_WORDS_MIN = 300
ARTICLE_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle"}


def run(facts: PageFacts, ctx: Context) -> list[CheckResult]:
    return [*_title(facts), *_meta(facts), *_headings(facts), *_canonical(facts, ctx), _robots(facts),
            _present("V1", facts.viewport, "no <meta name=\"viewport\">"),
            _present("L1", facts.lang, "<html> has no lang attribute"),
            _images(facts), *_links(facts), _og(facts), _words(facts, ctx), *_schema(facts, ctx),
            _duplicate(facts, ctx), *_keyword(facts, ctx)]


def _present(check_id: str, tagged, missing: str) -> CheckResult:
    if tagged is None:
        return result(check_id, "fail", evidence=missing, kind="absence")
    return ok(check_id, tagged.raw, "quote")


def _title(facts: PageFacts) -> list[CheckResult]:
    if facts.title is None or not facts.title.text:
        return [result("T1", "fail", "No page title", "<head> has no <title> (or it is empty)", "absence"),
                na("T2", "no title")]
    n = len(facts.title.text)
    t2 = ok("T2", facts.title.raw, "quote")
    if not TITLE_OK[0] <= n <= TITLE_OK[1]:
        warn = TITLE_FAIL_MIN <= n < TITLE_OK[0] or TITLE_OK[1] < n <= TITLE_WARN_MAX
        t2 = result("T2", "warn" if warn else "fail", f"Title is {n} characters", facts.title.raw, "quote",
                    severity="medium" if warn else None)
    return [ok("T1", facts.title.raw, "quote"), t2]


def _meta(facts: PageFacts) -> list[CheckResult]:
    meta = facts.meta_description
    if meta is None or not meta.text:
        return [result("M1", "fail", "No meta description", "<head> has no <meta name=\"description\">", "absence"),
                na("M2", "no meta description")]
    n = len(meta.text)
    m2 = ok("M2", meta.raw, "quote") if META_OK[0] <= n <= META_OK[1] else \
        result("M2", "fail", f"Meta description is {n} characters", meta.raw, "quote")
    return [ok("M1", meta.raw, "quote"), m2]


def _headings(facts: PageFacts) -> list[CheckResult]:
    count = len(facts.h1s)
    if count == 1:
        h1 = ok("H1", facts.h1s[0].raw, "quote")
    elif count == 0:
        h1 = result("H1", "fail", "No H1 heading", "the page has no <h1>", "absence")
    else:
        h1 = result("H1", "fail", f"{count} H1 headings", facts.h1s[1].raw, "quote")
    if facts.skipped_heading is not None:
        h2 = result("H2", "fail", f"Heading level skipped ({facts.skipped_heading.text})", facts.skipped_heading.raw,
                    "quote")
    else:
        h2 = ok("H2", "heading levels in order") if facts.outline else na("H2", "no headings")
    return [h1, h2]


def _canonical(facts: PageFacts, ctx: Context) -> list[CheckResult]:
    if facts.canonical is None or not facts.canonical.text:
        return [result("C1", "fail", "No canonical link", "no <link rel=\"canonical\">", "absence"),
                na("C2", "no canonical")]
    c1 = ok("C1", facts.canonical.raw, "quote")
    if not ctx.page_url:
        return [c1, na("C2", "page URL unknown")]
    href, page = urlsplit(facts.canonical.text), urlsplit(ctx.page_url)
    same = (not href.hostname or href.hostname.casefold() == (page.hostname or "").casefold()) and \
        (href.path or "/").rstrip("/") == (page.path or "/").rstrip("/")
    if same:
        return [c1, ok("C2", facts.canonical.raw, "quote")]
    return [c1, result("C2", "fail", "Canonical points to a different URL", facts.canonical.raw, "quote")]


def _robots(facts: PageFacts) -> CheckResult:
    meta = facts.robots_meta
    if meta is not None and "noindex" in meta.text.casefold():
        return result("R1", "fail", "Page is set to noindex", meta.raw, "quote")
    return ok("R1", meta.raw if meta else "no noindex robots meta", "quote" if meta else "fact")


def _images(facts: PageFacts) -> CheckResult:
    if facts.images_content == 0:
        return na("I1", "no content images")
    covered = facts.images_content - facts.images_missing_alt
    if covered / facts.images_content >= ALT_COVERAGE_MIN:
        return ok("I1", f"{covered} of {facts.images_content} content images have alt text")
    return result("I1", "fail", f"{facts.images_missing_alt} of {facts.images_content} images lack alt text",
                  facts.missing_alt_example, "quote")


def _links(facts: PageFacts) -> list[CheckResult]:
    n = facts.links_internal
    k1 = ok("K1", f"{n} internal links") if n >= INTERNAL_LINKS_MIN else \
        result("K1", "fail", f"Only {n} internal links", f"{n} internal links found", "fact")
    total = facts.links_internal + facts.links_external
    if total == 0:
        return [k1, na("K2", "no links")]
    if facts.generic_anchors / total <= GENERIC_SHARE_MAX:
        return [k1, ok("K2", f"{facts.generic_anchors} of {total} anchors are generic")]
    return [k1, result("K2", "fail", f"{facts.generic_anchors} of {total} links use generic anchor text",
                       facts.generic_example, "quote")]


def _og(facts: PageFacts) -> CheckResult:
    missing = [k for k in ("title", "description", "image") if not facts.og.get(k)]
    if not missing:
        return ok("O1", "og:title, og:description and og:image present")
    return result("O1", "fail", "Open Graph tags incomplete", "missing " + ", ".join(f"og:{k}" for k in missing),
                  "absence")


def _words(facts: PageFacts, ctx: Context) -> CheckResult:
    if ctx.intent != "informational":
        return na("W1", "not an informational page" if ctx.intent else "content read unavailable")
    if facts.words >= INFORMATIONAL_WORDS_MIN:
        return ok("W1", f"{facts.words} words")
    return result("W1", "fail", f"Thin content: {facts.words} words", f"{facts.words} words of visible text")


def _schema(facts: PageFacts, ctx: Context) -> list[CheckResult]:
    if not facts.jsonld_types:
        why = f"{facts.jsonld_errors} JSON-LD block(s) failed to parse" if facts.jsonld_errors else "no JSON-LD block"
        return [result("S1", "fail", "No valid structured data", why, "absence"), na("S2", "no valid JSON-LD")]
    s1 = ok("S1", "JSON-LD types: " + ", ".join(facts.jsonld_types))
    expected = _expected_types(facts, ctx)
    if not expected or expected & set(facts.jsonld_types):
        return [s1, ok("S2", "JSON-LD types: " + ", ".join(facts.jsonld_types))]
    return [s1, result("S2", "fail", "Structured data type does not fit the page",
                       f"page looks like {'/'.join(sorted(expected))}; JSON-LD has {', '.join(facts.jsonld_types)}")]


def _expected_types(facts: PageFacts, ctx: Context) -> set[str]:
    if facts.product_signal:
        return {"Product"}
    if facts.faq_block and facts.question_headings:
        return {"FAQPage", *ARTICLE_TYPES}
    if facts.has_article or facts.jsonld_dates or facts.time_tag:
        return set(ARTICLE_TYPES)
    return set()


def _duplicate(facts: PageFacts, ctx: Context) -> CheckResult:
    if ctx.intent != "informational" or facts.title is None or len(facts.h1s) != 1:
        return na("D1", "applies to informational pages with a title and one H1")
    if facts.title.text.casefold().strip() == facts.h1s[0].text.casefold().strip():
        return result("D1", "fail", "Title and H1 are identical", facts.h1s[0].raw, "quote")
    return ok("D1", "title and H1 differ")


def _keyword(facts: PageFacts, ctx: Context) -> list[CheckResult]:
    ids = ("P1-title", "P1-h1", "P1-intro")
    if not ctx.keyword:
        return [na(i, "no target keyword given") for i in ids]
    key = ctx.keyword.casefold()
    places = (facts.title.text if facts.title else "", " ".join(h.text for h in facts.h1s), facts.first_words)
    labels = ("title", "H1", "first 100 words")
    return [ok(i, f"\"{ctx.keyword}\" found in the {label}") if key in text.casefold() else
            result(i, "fail", f"Keyword missing from the {label}", f"\"{ctx.keyword}\" not found in the {label}",
                   "absence") for i, label, text in zip(ids, labels, places)]
