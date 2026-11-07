"""Pure: open questions computed in code. Vague words, missing categories, uncovered and unverifiable
requirements, conflicting pairs and every [TBD] the grounding introduced. Questions are deduped by key,
ordered by how many requirement ids they block, and keep their number Qn across turns."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from grounding import normalise
from vague_words import conflicts, vague_hits

MAX_MISSING = 8
_PER = r"\s*(?:/|per |an? |each )\s*(?:s|sec|second|min|minute|hour|day|week|month)\b"
CATEGORIES = (  # most design-critical first; scale and latency count only with a quantity
    ("scale", (rf"\d[\d,.]*\s*k?\s*(?:users?|requests?|rps|qps|records?|gb|tb|events?|messages?|orders?|"
               rf"transactions?|customers?)|\d[\d,.]*\s*k?\s*[a-z]*{_PER}|rps|qps|throughput|concurrent")),
    ("latency", r"\d[\d,.]*\s*(?:ms|milliseconds?|s|secs?|seconds?|minutes?)\b|p9\d|response time"),
    ("availability", r"uptime|sla|slo|99(?:\.\d+)?\s*%|downtime|availability"),
    ("security", r"auth\w*|login|sso|permissions?|roles?|encrypt\w*|oauth|access control"),
    ("data retention", r"retention|retain\w*|delet\w*|gdpr|pii|privacy|personal data"),
    ("integrations", r"api|webhooks?|integrat\w*|providers?|third[- ]party|external"),
    ("cost", r"cost|budget|spend|price|pricing|\$"),
    ("environment", r"cloud|on[- ]prem\w*|kubernetes|regions?|aws|gcp|azure|deploy\w*|hosted"),
)
_CATEGORY_RX = tuple((name, re.compile(rf"(?<![a-z]){rx}(?![a-z])")) for name, rx in CATEGORIES)


def missing_categories(ground_text: str) -> list[str]:
    norm = normalise(ground_text)
    return [name for name, rx in _CATEGORY_RX if not rx.search(norm)][:MAX_MISSING]


def candidates(reqs: Sequence[dict], ground_text: str, uncovered: Sequence[str], unverified: Sequence[str],
               tbd_units: Sequence[str]) -> list[dict]:
    """Every question the draft raises, deduped by key, in category order (before sorting)."""
    qs: list[dict] = []
    for word, ids in vague_hits(reqs).items():
        qs.append(_q(f"vague:{word}", f"What does '{word}' mean as a number or testable condition?", ids))
    for a, b, ida, idb in conflicts(reqs):
        qs.append(_q(f"conflict:{a}|{b}", f"{ida} says '{a}' and {idb} says '{b}': which one wins, or how do "
                                         "they fit together?", [ida, idb]))
    for rid in uncovered:
        qs.append(_q(f"uncovered:{rid}", f"{rid} is not covered by any component: which component owns it?", [rid]))
    for rid in unverified:
        qs.append(_q(f"verify:{rid}", f"How will {rid} be verified?", [rid]))
    for unit in dict.fromkeys(u.casefold() for u in tbd_units):
        qs.append(_q(f"tbd:{unit}", f"The draft needs a value for '{unit}' that your requirements don't give: "
                                    "what is it?", []))
    for name in missing_categories(ground_text):
        qs.append(_q(f"missing:{name}", f"Not specified: {name}. What is required?", []))
    return list({q["key"]: q for q in reversed(qs)}.values())[::-1]


def _q(key: str, text: str, blocks: Sequence[str]) -> dict:
    return {"key": key, "text": text, "blocks": list(blocks)}


def number(questions: Sequence[dict], qnums: Mapping[str, int], answered: Sequence[str]) -> tuple[list[dict], dict]:
    """(open questions with stable ids Qn, the updated key -> number map). Answered keys stay closed."""
    closed = set(answered)
    open_qs = [q for q in questions if q["key"] not in closed]
    ordered = sorted(open_qs, key=lambda q: -len(q["blocks"]))  # stable: category order breaks ties
    nums = dict(qnums)
    for q in ordered:
        if q["key"] not in nums:
            nums[q["key"]] = max(nums.values(), default=0) + 1
    return [{"id": f"Q{nums[q['key']]}", **q} for q in ordered], nums


def public(questions: Sequence[dict]) -> list[dict]:
    return [{"id": q["id"], "text": q["text"], "blocks": list(q["blocks"])} for q in questions]
