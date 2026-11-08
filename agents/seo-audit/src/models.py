"""The data model: the facts parsed from a page, one check's result, a finding, the proposals and the
envelope every reply carries (the catalog's summary/items/confidence/needs_human plus this agent's fields)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
Area = Literal["on_page", "geo"]
SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}


class Tagged(BaseModel):
    """A value with the raw HTML it was read from (verbatim evidence)."""

    text: str = ""
    raw: str = ""


class PageFacts(BaseModel):
    title: Tagged | None = None
    meta_description: Tagged | None = None
    robots_meta: Tagged | None = None
    canonical: Tagged | None = None
    viewport: Tagged | None = None
    lang: Tagged | None = None
    hreflang: list[str] = Field(default_factory=list)
    h1s: list[Tagged] = Field(default_factory=list)
    outline: list[tuple[int, str]] = Field(default_factory=list)
    skipped_heading: Tagged | None = None
    words: int = 0
    first_words: str = ""
    images_content: int = 0
    images_missing_alt: int = 0
    missing_alt_example: str = ""
    links_internal: int = 0
    links_external: int = 0
    links_nofollow: int = 0
    generic_anchors: int = 0
    generic_example: str = ""
    og: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    jsonld_types: list[str] = Field(default_factory=list)
    jsonld_errors: int = 0
    jsonld_dates: list[str] = Field(default_factory=list)
    jsonld_author: str = ""
    has_main: bool = False
    has_article: bool = False
    time_tag: str = ""
    meta_date: str = ""
    author_signal: str = ""
    question_headings: list[str] = Field(default_factory=list)
    faq_block: bool = False
    lists: int = 0
    tables: int = 0
    median_paragraph_words: int | None = None
    citable_facts: list[str] = Field(default_factory=list)
    product_signal: bool = False
    scripts: int = 0
    truncated: bool = False
    site_name: str = ""


class CheckResult(BaseModel):
    id: str
    area: Area
    severity: Severity
    weight: int
    outcome: Literal["pass", "fail", "warn", "na"]
    title: str = ""
    evidence: str = ""
    evidence_kind: Literal["quote", "absence", "fact"] = "fact"
    reason: str = ""          # why not applicable

    @property
    def passed(self) -> bool:
        return self.outcome == "pass"


class Finding(BaseModel):
    id: str
    check: str
    area: Area
    severity: Severity
    title: str
    evidence: str
    fix: str
    weight: int


class Proposals(BaseModel):
    title: str = ""
    title_chars: int = 0
    meta: str = ""
    meta_chars: int = 0
    json_ld: str = ""
    notes: list[str] = Field(default_factory=list)


class Envelope(BaseModel):
    summary: str
    items: list[Finding]
    confidence: Literal["high", "medium", "low"]
    needs_human: bool
    score: int
    on_page_score: int
    geo_score: int
    band: str
    not_applicable: list[str] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    primary_topic: str | None = None
    topic_inferred: bool = True
    keyword: str = ""
    page_url: str = "unknown"
    intent: str = ""
    proposals: Proposals = Field(default_factory=Proposals)
    page: PageFacts = Field(default_factory=PageFacts)
    checks: list[CheckResult] = Field(default_factory=list)
    ai_crawlers: dict[str, str] = Field(default_factory=dict)
    quality_dropped: int = 0
    diff: dict[str, list[str]] | None = None
