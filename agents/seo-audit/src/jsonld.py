"""Pure: JSON-LD on the page (parsed with json.loads, types listed, parse errors flagged) and the cleaning of a
proposed JSON-LD block: only known types and fields, every value found verbatim on the page, the result
re-parsed before it is shown. A field the page does not support is removed, never invented."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "Organization": frozenset({"name", "url", "logo", "email", "telephone"}),
    "LocalBusiness": frozenset({"name", "url", "telephone", "address", "email"}),
    "Article": frozenset({"headline", "datePublished", "dateModified", "author", "publisher", "description"}),
    "BlogPosting": frozenset({"headline", "datePublished", "dateModified", "author", "publisher", "description"}),
    "Product": frozenset({"name", "description", "brand", "sku"}),
    "FAQPage": frozenset({"mainEntity"}),
    "WebPage": frozenset({"name", "description", "url"}),
}
NESTED_TYPES = {"author": ("Person", "Organization"), "publisher": ("Organization",), "brand": ("Brand", "Organization")}
ARTICLE_TYPES = frozenset({"Article", "BlogPosting", "NewsArticle", "TechArticle"})


def parse_blocks(raw_blocks: list[str]) -> tuple[list[dict], int]:
    """(parsed objects, number of blocks that failed to parse). @graph members are flattened."""
    objects: list[dict] = []
    errors = 0
    for raw in raw_blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            errors += 1
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                objects.extend(g for g in item.get("@graph", []) if isinstance(g, dict))
                objects.append(item)
    return objects, errors


def types_of(objects: list[dict]) -> list[str]:
    found: list[str] = []
    for obj in objects:
        kind = obj.get("@type")
        for name in kind if isinstance(kind, list) else [kind]:
            if isinstance(name, str) and name not in found:
                found.append(name)
    return found


def field_values(objects: list[dict], key: str) -> list[Any]:
    return [obj[key] for obj in objects if key in obj]


def clean_proposal(proposal: Any, grounded: Callable[[str], bool]) -> tuple[str | None, int]:
    """(pretty JSON or None, number of fields removed)."""
    if isinstance(proposal, str):
        try:
            proposal = json.loads(proposal)
        except ValueError:
            return None, 0
    if not isinstance(proposal, dict) or proposal.get("@type") not in ALLOWED_FIELDS:
        return None, 0
    kind = proposal["@type"]
    removed = 0
    out: dict[str, Any] = {"@context": "https://schema.org", "@type": kind}
    for key, value in proposal.items():
        if key in ("@context", "@type"):
            continue
        kept = _clean_value(kind, key, value, grounded) if key in ALLOWED_FIELDS[kind] else None
        if kept is None:
            removed += 1
        else:
            out[key] = kept
    if len(out) <= 2:
        return None, removed
    text = json.dumps(out, indent=2, ensure_ascii=False)
    json.loads(text)  # displayed only if it parses
    return text, removed


def _clean_value(kind: str, key: str, value: Any, grounded: Callable[[str], bool]) -> Any:
    if kind == "FAQPage":
        return _faq(value, grounded)
    if isinstance(value, str):
        return value if value.strip() and grounded(value) else None
    if isinstance(value, dict) and key in NESTED_TYPES:
        name = value.get("name")
        sub = value.get("@type") if value.get("@type") in NESTED_TYPES[key] else NESTED_TYPES[key][0]
        return {"@type": sub, "name": name} if isinstance(name, str) and grounded(name) else None
    return None


def _faq(value: Any, grounded: Callable[[str], bool]) -> list[dict] | None:
    entries = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        question = item.get("name")
        answer = (item.get("acceptedAnswer") or {}).get("text") if isinstance(item.get("acceptedAnswer"), dict) else None
        if isinstance(question, str) and isinstance(answer, str) and grounded(question) and grounded(answer):
            entries.append({"@type": "Question", "name": question,
                            "acceptedAnswer": {"@type": "Answer", "text": answer}})
    return entries or None
