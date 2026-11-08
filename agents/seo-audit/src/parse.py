"""Pure: the structured facts of one page (BeautifulSoup, `html.parser`), each with the raw HTML it came
from where a finding may quote it. Malformed HTML is tolerated (the parser never raises on markup)."""

from __future__ import annotations

import re
import statistics
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

import jsonld
from models import PageFacts, Tagged
from pagetext import citable_facts, is_question, raw_tag, visible_text

GENERIC_ANCHORS = frozenset({"click here", "here", "read more", "more", "learn more", "this link", "link",
                             "this page", "go", "details", "continue"})
DECORATIVE_ROLES = frozenset({"presentation", "none"})
_HEADING = re.compile(r"^h[1-6]$")
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "#", "data:")
_PRODUCT = re.compile(r"\b(?:add to cart|add to bag|buy now)\b", re.IGNORECASE)
_FAQ_HEADING = re.compile(r"\b(?:faq|frequently asked)", re.IGNORECASE)


def parse_page(html: str, page_url: str = "") -> tuple[PageFacts, str]:
    """(facts, visible text). `html` must already have \\r\\n normalised to \\n (offsets are line based)."""
    soup = BeautifulSoup(html, "html.parser")
    text = visible_text(html)
    facts = PageFacts(words=len(text.split()), first_words=" ".join(text.split()[:100]))
    _head(soup, html, facts)
    _headings(soup, html, facts)
    _images(soup, html, facts)
    _links(soup, html, page_url, facts)
    _structured(soup, facts)
    _signals(soup, html, text, facts)
    return facts, text


def _tagged(tag: Tag | None, html: str, value: str | None) -> Tagged | None:
    if tag is None or value is None:
        return None
    return Tagged(text=" ".join(value.split()), raw=raw_tag(html, tag))


def _meta(soup: BeautifulSoup, attr: str, name: str) -> Tag | None:
    return soup.find("meta", attrs={attr: re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)})


def _head(soup: BeautifulSoup, html: str, facts: PageFacts) -> None:
    title = soup.find("title")
    facts.title = _tagged(title, html, title.get_text() if title else None)
    for field, name in (("meta_description", "description"), ("robots_meta", "robots"), ("viewport", "viewport")):
        tag = _meta(soup, "name", name)
        setattr(facts, field, _tagged(tag, html, str(tag.get("content") or "") if tag else None))
    canonical = soup.find("link", rel=lambda v: bool(v) and "canonical" in str(v).casefold().split())
    facts.canonical = _tagged(canonical, html, str(canonical.get("href") or "") if canonical else None)
    root = soup.find("html")
    facts.lang = _tagged(root, html, str(root.get("lang")) if root is not None and root.get("lang") else None)
    facts.hreflang = [str(t.get("hreflang")) for t in soup.find_all("link", hreflang=True)]
    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or "").casefold()
        if key.startswith(("og:", "twitter:")):
            target = facts.og if key.startswith("og:") else facts.twitter
            target[key.split(":", 1)[1]] = str(tag.get("content") or "")
    facts.site_name = facts.og.get("site_name", "")


def _headings(soup: BeautifulSoup, html: str, facts: PageFacts) -> None:
    previous = None
    for tag in soup.find_all(_HEADING):
        level, text = int(tag.name[1]), " ".join(tag.get_text(" ").split())
        facts.outline.append((level, text[:120]))
        if level == 1:
            facts.h1s.append(Tagged(text=text, raw=raw_tag(html, tag)))
        if previous is not None and level > previous + 1 and facts.skipped_heading is None:
            facts.skipped_heading = Tagged(text=f"h{previous} -> h{level}", raw=raw_tag(html, tag))
        if level in (2, 3) and is_question(text):
            facts.question_headings.append(text[:120])
        if _FAQ_HEADING.search(text):
            facts.faq_block = True
        previous = level


def _images(soup: BeautifulSoup, html: str, facts: PageFacts) -> None:
    for img in soup.find_all("img"):
        role = str(img.get("role") or "").casefold()
        if role in DECORATIVE_ROLES or str(img.get("aria-hidden") or "").casefold() == "true":
            continue
        facts.images_content += 1
        if not str(img.get("alt") or "").strip():
            facts.images_missing_alt += 1
            facts.missing_alt_example = facts.missing_alt_example or raw_tag(html, img)


def _links(soup: BeautifulSoup, html: str, page_url: str, facts: PageFacts) -> None:
    host = (urlsplit(page_url).hostname or "").casefold()
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.casefold().startswith(_SKIP_SCHEMES):
            continue
        link_host = (urlsplit(href).hostname or "").casefold()
        internal = not link_host or (bool(host) and link_host.removeprefix("www.") == host.removeprefix("www."))
        facts.links_internal += internal
        facts.links_external += not internal
        facts.links_nofollow += "nofollow" in [r.casefold() for r in a.get("rel") or []]
        anchor = " ".join(a.get_text(" ").split()).casefold().strip(".!:>» ")
        if anchor in GENERIC_ANCHORS:
            facts.generic_anchors += 1
            facts.generic_example = facts.generic_example or raw_tag(html, a)


def _structured(soup: BeautifulSoup, facts: PageFacts) -> None:
    raw = [s.string or s.get_text() for s in soup.find_all("script", type=re.compile("ld\\+json", re.IGNORECASE))]
    objects, facts.jsonld_errors = jsonld.parse_blocks(raw)
    facts.jsonld_types = jsonld.types_of(objects)
    facts.jsonld_dates = [str(v) for k in ("datePublished", "dateModified") for v in jsonld.field_values(objects, k)]
    authors = jsonld.field_values(objects, "author")
    first = authors[0] if authors else None
    facts.jsonld_author = str(first.get("name") or "") if isinstance(first, dict) else str(first or "")
    facts.faq_block = facts.faq_block or "FAQPage" in facts.jsonld_types
    facts.product_signal = "Product" in facts.jsonld_types or facts.og.get("type", "").casefold() == "product"


def _signals(soup: BeautifulSoup, html: str, text: str, facts: PageFacts) -> None:
    facts.has_main = soup.find("main") is not None
    facts.has_article = soup.find("article") is not None
    time_tag = soup.find("time")
    facts.time_tag = raw_tag(html, time_tag) if time_tag is not None else ""
    date_meta = _meta(soup, "property", "article:published_time") or _meta(soup, "property", "article:modified_time")
    facts.meta_date = raw_tag(html, date_meta) if date_meta is not None else ""
    facts.author_signal = _author(soup, html, facts)
    facts.faq_block = facts.faq_block or len(soup.find_all("details")) >= 2
    facts.lists = len(soup.find_all(["ul", "ol"]))
    facts.tables = len(soup.find_all("table"))
    lengths = [len(p.get_text(" ").split()) for p in soup.find_all("p") if p.get_text(strip=True)]
    facts.median_paragraph_words = int(statistics.median(lengths)) if lengths else None
    facts.citable_facts = citable_facts(text)
    facts.product_signal = facts.product_signal or bool(_PRODUCT.search(text))
    facts.scripts = len([s for s in soup.find_all("script") if "ld+json" not in str(s.get("type") or "")])
    facts.truncated = "<html" in html.casefold() and "</html>" not in html.casefold()


def _author(soup: BeautifulSoup, html: str, facts: PageFacts) -> str:
    candidates = (_meta(soup, "name", "author"), soup.find(rel="author"), soup.find(itemprop="author"),
                  soup.find(class_=re.compile(r"\b(?:author|byline)\b", re.IGNORECASE)),
                  soup.find("a", href=re.compile(r"/about(?:[/-]|$)", re.IGNORECASE)))
    for tag in candidates:
        if tag is not None:
            return raw_tag(html, tag)
    if facts.jsonld_author:
        return f"JSON-LD author: {facts.jsonld_author}"
    return "JSON-LD Organization" if "Organization" in facts.jsonld_types else ""
