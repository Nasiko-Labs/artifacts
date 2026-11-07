"""Pure: code checks on what the model wrote, and safe text for the markdown reply.

A finding stands only when it cites known fact ids, every number in it appears in the cited facts, and it
uses no causal wording; otherwise the cited fact's own code sentence replaces it. A finding citing no
known fact is dropped (counted). Model caveats follow the same rules but are dropped, not replaced.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fmt import numbers_in
from result import Fact

MAX_FINDINGS = 6
MAX_MODEL_CAVEATS = 2
MAX_TEXT_CHARS = 400
CAUSAL = re.compile(r"\b(?:because|caus(?:e|es|ed|ing)|due to|leads? to|led to|drives?|driven by|driving|"
                    r"results? in|resulted in|thanks to|owing to)\b", re.IGNORECASE)
_TAG = re.compile(r"<[^>]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
log = logging.getLogger(__name__)
_URL = re.compile(r"\bhttps?://[^\s)>\]]+", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    text: str
    fact_ids: tuple[str, ...]
    values: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"text": self.text, "fact_ids": list(self.fact_ids), "values": list(self.values)}


@dataclass(frozen=True)
class Checked:
    findings: tuple[Finding, ...]
    caveats: tuple[Finding, ...]
    fell_back: int = 0
    dropped: int = 0


def clean_text(text: str, source: str = "") -> str:
    """No raw HTML, no images, links only when their URL is in the user's input."""
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in source else m.group(1), out)
    out = _URL.sub(lambda m: m.group(0) if m.group(0) in source else "", out)
    out = _TAG.sub("", out).replace("<", "‹").replace(">", "›")
    return " ".join(out.split())[:MAX_TEXT_CHARS]


def cell(text: object) -> str:
    """A markdown table cell from data: never a link, an image or HTML; pipes escaped."""
    plain = _TAG.sub("", str(text)).replace("<", "‹").replace(">", "›").replace("|", "\\|")
    plain = plain.replace("](", "] (").replace("![", "! [")
    return " ".join(plain.split())[:80]


def traced(text: str, facts: Sequence[Fact]) -> bool:
    allowed = set()
    for fact in facts:
        allowed |= numbers_in(fact.text)
        for value in fact.values:
            allowed |= numbers_in(value)
    allowed |= {abs(x) for x in allowed}
    return all(x in allowed or abs(x) in allowed for x in numbers_in(text))


def check(raw: Any, facts: Mapping[str, Fact], source: str = "") -> Checked:
    data = raw if isinstance(raw, Mapping) else {}
    findings, fell_back, dropped = _check_list(data.get("findings"), facts, source, replace=True)
    caveats, _, cdropped = _check_list(data.get("caveats"), facts, source, replace=False)
    return Checked(tuple(findings[:MAX_FINDINGS]), tuple(caveats[:MAX_MODEL_CAVEATS]), fell_back,
                   dropped + cdropped)


def _check_list(items: Any, facts: Mapping[str, Fact], source: str, replace: bool) -> tuple[list[Finding], int, int]:
    out: list[Finding] = []
    fell_back = dropped = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            dropped += 1
            continue
        ids = tuple(dict.fromkeys(i for i in _ids(item.get("fact_ids")) if i in facts))
        if not ids:
            log.info("model item dropped: no known fact id (fact_ids type %s)", type(item.get("fact_ids")).__name__)
            dropped += 1
            continue
        cited = [facts[i] for i in ids]
        text = clean_text(str(item.get("text") or ""), source)
        if not text or CAUSAL.search(text) or not traced(text, cited):
            log.info("model item replaced or dropped: empty=%s causal=%s untraced=%s", not text,
                     bool(CAUSAL.search(text)), not traced(text, cited))
            if not replace:
                dropped += 1
                continue
            text, ids, cited = cited[0].text, ids[:1], cited[:1]
            fell_back += 1
        if any(f.text == text for f in out):
            continue
        out.append(Finding(text, ids, tuple(v for f in cited for v in f.values)))
    return out, fell_back, dropped


def _ids(raw: Any) -> list[str]:
    """Fact ids as cited, tolerating the model's slips: one bare string, numbers (2 → f2), case and spaces."""
    items = [raw] if isinstance(raw, str | int) else raw if isinstance(raw, list) else []
    out = []
    for item in items:
        text = str(item).strip().casefold()
        out.append(f"f{text}" if text.isdigit() else text)
    return out


def fact_findings(facts: Sequence[Fact], limit: int = MAX_FINDINGS) -> tuple[Finding, ...]:
    """Findings straight from code sentences (no model wording)."""
    return tuple(Finding(f.text, (f.id,), f.values) for f in facts[:limit])
