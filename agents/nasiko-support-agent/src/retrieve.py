"""Pure: BM25 retrieval over the knowledge-base sections. The model only ever sees what this returns.

Flows (hand-written task answers with the exact on-screen labels) get a small boost over docs chunks,
and a flow's own "asked as" phrasings are indexed twice, so a question asked the usual way lands on
its flow.

Multi-word product terms ("MCP gateway", "LLM router", "agent card") are also indexed as one term, so a
question naming one matches the sections about that thing far more strongly than sections that only share
its words, and generic verbs ("talk", "connect", "work") count for less than other words. A section's score
is scaled by how much of the question it covers, and a docs chunk's title counts more than its body. A developer-
integration question (`prefer_developer_docs`) turns the flow boost into a boost for the build and
integration docs, and keeps the best setup flow in the top results next to them."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from kb import Section

K1 = 1.4
B = 0.75
FLOW_BOOST = 1.35
DEVELOPER_DOCS_BOOST = 1.35   # replaces the flow boost for a developer-integration question
DEVELOPER_PAGES = ("doc:build__", "doc:governance__", "doc:reference__", "doc:quickstart")
COMPANION_FLOW_SHARE = 0.2    # the best flow, scoring at least this share of the top hit, joins the top k
PRODUCT_TERM_WEIGHT = 1.5
GENERIC_TERM_WEIGHT = 0.4
COVERAGE_FLOOR = 0.5          # a section with none of the question's words keeps this share of its score
DOC_TITLE_REPEAT = 3
TOP_K = 3

_PHRASES = ((re.compile(r"\b(?:log|sign)[ -]?in\b", re.IGNORECASE), "login"),
            (re.compile(r"\b(?:log|sign)[ -]?out\b", re.IGNORECASE), "logout"))
# Product terms written as two words (or run together), each indexed as one extra term.
_PRODUCT_TERMS = (
    (re.compile(r"\bmcp[ -]?gateway\b", re.IGNORECASE), "mcp-gateway"),
    (re.compile(r"\b(?:llm|model)[ -]?router\b", re.IGNORECASE), "llm-router"),
    (re.compile(r"\bagent[ -]?cards?\b", re.IGNORECASE), "agent-card"),
    (re.compile(r"\bstate[ -]?api\b", re.IGNORECASE), "state-api"),
    (re.compile(r"\bmcp[ -]?servers?\b", re.IGNORECASE), "mcp-server"),
    (re.compile(r"\bhuman[ -]in[ -]the[ -]loop\b|\bhitl\b", re.IGNORECASE), "human-in-the-loop"),
)
PRODUCT_TERMS = frozenset(term for _, term in _PRODUCT_TERMS)
# Verbs that say little about which feature a question is about.
GENERIC_TERMS = frozenset(["talk", "connect", "work", "make", "develop", "help", "call", "reach", "access", "code"])
_POSSESSIVE = re.compile(r"'s$")   # "agent's" is "agent"
_TOKEN = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
STOPWORDS = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "then", "so", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "onto", "about", "as", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "doing", "have", "has", "had", "having", "i", "me", "my", "mine", "we", "our", "you", "your", "it", "its", "this", "that", "these", "those", "there", "here", "what", "which", "who", "whom", "how", "why", "when", "where", "can", "could", "should", "would", "will", "shall", "may", "might", "must", "not", "no", "yes", "please", "just", "get", "got", "want", "need", "let", "lets", "im", "it's", "i'm", "dont", "don't", "isnt", "isn't", "doesnt", "doesn't", "any", "some", "all", "also", "up", "out", "over", "than", "too", "very", "via", "use", "using", "one"]
)
# A few spellings people use for the same thing.
SYNONYMS = {"llm": "model", "models": "model", "key": "secret", "keys": "secret", "token": "secret",
            "remove": "delete", "uninstall": "delete", "logs": "log", "zipped": "zip", "uploading": "upload",
            "uploaded": "upload", "deploying": "deploy", "deployed": "deploy", "env": "environment",
            "variables": "variable", "permissions": "permission", "approval": "approve", "approvals": "approve",
            "costs": "cost", "spend": "cost", "spending": "cost", "budgets": "budget", "workflows": "workflow",
            "sessions": "session", "traces": "trace", "agents": "agent", "secrets": "secret", "builds": "build",
            "versions": "version", "var": "variable", "vars": "variable", "connectors": "connector", "servers": "server", "tools": "tool"}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    """Plain words (stopwords dropped, synonyms folded, stemmed), then one term per product term found."""
    out = []
    text = text or ""
    for pattern, word in _PHRASES:
        text = pattern.sub(word, text)
    for raw in _TOKEN.findall(text.lower()):
        raw = _POSSESSIVE.sub("", raw)
        if raw in STOPWORDS:
            continue
        word = SYNONYMS.get(raw, raw)
        out.append(_stem(word))
    out += [term for pattern, term in _PRODUCT_TERMS for _ in pattern.finditer(text)]
    return out


def term_weight(term: str) -> float:
    if term in PRODUCT_TERMS:
        return PRODUCT_TERM_WEIGHT
    return GENERIC_TERM_WEIGHT if term in GENERIC_TERMS else 1.0


@dataclass(frozen=True)
class Hit:
    section: Section
    score: float
    coverage: float = 0.0   # share of the query's terms this section contains


class Index:
    def __init__(self, sections: Sequence[Section]):
        self._sections = tuple(sections)
        self._docs = [Counter(tokens(self._index_text(s))) for s in self._sections]
        self._lengths = [sum(d.values()) for d in self._docs]
        self._avg = (sum(self._lengths) / len(self._lengths)) if self._lengths else 1.0
        df: Counter = Counter()
        for doc in self._docs:
            df.update(doc.keys())
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    @staticmethod
    def _index_text(section: Section) -> str:
        if section.kind == "flow":
            return "\n".join([section.title, *section.asked_as, *section.asked_as, section.text])
        # A docs chunk's text starts with its title; the title counts DOC_TITLE_REPEAT times in all.
        return "\n".join([*[section.title] * (DOC_TITLE_REPEAT - 1), section.text])

    def vocabulary(self) -> frozenset[str]:
        return frozenset(self._idf)

    @staticmethod
    def _boost(section: Section, prefer_developer_docs: bool) -> float:
        if not prefer_developer_docs:
            return FLOW_BOOST if section.kind == "flow" else 1.0
        return DEVELOPER_DOCS_BOOST if section.id.startswith(DEVELOPER_PAGES) else 1.0

    def score(self, query: str, prefer_developer_docs: bool = False) -> list[Hit]:
        terms = set(tokens(query))
        # Coverage is the weighted share of the question's words a section contains (a generic verb counts
        # for less; a product term is a bonus on top, not a word the section must have).
        words = terms - PRODUCT_TERMS
        wanted = sum(term_weight(t) for t in words)
        hits = []
        for section, doc, length in zip(self._sections, self._docs, self._lengths):
            total, matched = 0.0, 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if not tf:
                    continue
                matched += term_weight(term) if term in words else 0.0
                norm = tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self._avg))
                total += self._idf.get(term, 0.0) * norm * term_weight(term)
            if total > 0:
                coverage = matched / wanted if wanted else 1.0
                fit = COVERAGE_FLOOR + (1 - COVERAGE_FLOOR) * coverage
                hits.append(Hit(section, total * fit * self._boost(section, prefer_developer_docs), coverage))
        return sorted(hits, key=lambda h: (-h.score, h.section.id))

    def search(self, query: str, k: int = TOP_K, prefer_developer_docs: bool = False) -> list[Hit]:
        ranked = self.score(query, prefer_developer_docs)
        top = ranked[:k]
        if not prefer_developer_docs or not top or any(h.section.kind == "flow" for h in top):
            return top
        return _with_companion_flow(top, ranked[k:])


def _with_companion_flow(top: list[Hit], rest: Sequence[Hit]) -> list[Hit]:
    """An integration answer needs the code side and the setup side: the best flow close enough to the top
    hit takes the last slot."""
    flow = next((h for h in rest if h.section.kind == "flow"), None)
    if flow is None or flow.score < COMPANION_FLOW_SHARE * top[0].score:
        return top
    return [*top[:-1], flow]
