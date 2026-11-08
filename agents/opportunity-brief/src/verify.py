"""Verification in code: every cited id must exist and every quote must appear verbatim (whitespace and case
normalised) in that item's stored text; numbers in prose must appear in the theme's FACTS, metrics or
evidence; hype words are flagged. One repair call lists the exact violations; whatever is still wrong after
it is dropped (quotes), replaced by "see evidence" (sentences) or removed (hype words)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import metrics
import prompts
from cluster import Cluster
from deps import TurnDeps
from llm import LlmError, call_json
from store import Item
from write import drafts_by_index

log = logging.getLogger("opportunity_brief.verify")

HYPE = ("huge", "explosive", "untapped", "massive", "revolutionary", "game-changing", "game changing",
        "skyrocketing", "booming", "insane", "unprecedented", "guaranteed")
MAX_QUOTE_CHARS = 300
MIN_QUOTE_CHARS = 3
MAX_RISKS = 3
MAX_EVIDENCE = 6
SEE_EVIDENCE = "see evidence"
_NUMBER = re.compile(r"(?<![A-Za-z_.\d])\d[\d,]*(?:\.\d+)?\s?(?:%|k\b|x\b)?", re.IGNORECASE)
_HYPE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in HYPE) + r")\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_UNSAFE = re.compile(r"https?://\S+|www\.\S+|<[^>]*>|!\[[^\]]*\]\([^)]*\)")
_PROSE = ("why_now", "opportunity")


@dataclass(frozen=True)
class Checked:
    index: int
    why_now: str
    opportunity: str
    risks: tuple[str, ...]
    next_step: str
    evidence: tuple[tuple[str, str], ...]
    cited: int                       # quotes the model gave
    violations: tuple[str, ...] = ()


def norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").replace(" ", "").casefold() for m in _NUMBER.finditer(text or "")}


def clean(text: object) -> str:
    return " ".join(_UNSAFE.sub(" ", prompts.strip_markers(str(text or ""))).split())


def grounded_numbers(items: Sequence[Item], now: datetime) -> set[str]:
    texts = (*metrics.facts(items, now), *(metrics.metric(i, now) for i in items), *(i.text for i in items))
    return set().union(*(numbers(t) for t in texts))


def check(n: int, draft: Mapping, items: Sequence[Item], by_id: Mapping[str, Item], now: datetime) -> Checked:
    allowed = grounded_numbers(items, now)
    violations: list[str] = []
    evidence: list[tuple[str, str]] = []
    raw_evidence = draft.get("evidence") if isinstance(draft.get("evidence"), list) else []
    for entry in raw_evidence:
        if not isinstance(entry, Mapping):
            continue
        item_id, quote = str(entry.get("id") or "").strip(), " ".join(str(entry.get("quote") or "").split())
        item = by_id.get(item_id)
        ok = item is not None and MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS and norm(quote) in norm(item.text)
        if ok and all(e[0] != item_id for e in evidence):
            evidence.append((item_id, quote))
        elif not ok:
            violations.append(f"theme {n}: quote for {item_id or '?'} is not verbatim in that item: {quote[:120]!r}")
    fields = {k: clean(draft.get(k)) for k in (*_PROSE, "next_step")}
    risks = tuple(clean(r) for r in (draft.get("risks") or [])[:MAX_RISKS] if clean(r))
    for name, text in [*((k, fields[k]) for k in _PROSE), *(("risk", r) for r in risks)]:
        stray = sorted(numbers(text) - allowed)
        if stray:
            violations.append(f"theme {n}: {name} uses numbers not in the facts or evidence: {', '.join(stray)}")
    for name, text in [*fields.items(), *(("risk", r) for r in risks)]:
        if _HYPE.search(text):
            violations.append(f"theme {n}: {name} uses hype words: {', '.join(set(_HYPE.findall(text)))}")
    return Checked(n, fields["why_now"], fields["opportunity"], risks, fields["next_step"],
                   tuple(evidence[:MAX_EVIDENCE]), len(raw_evidence), tuple(violations))


def scrub(checked: Checked, items: Sequence[Item], now: datetime) -> Checked:
    """Final pass after the one repair: ungrounded sentences replaced, hype words removed, risks labelled."""
    allowed = grounded_numbers(items, now)

    def fix(text: str) -> str:
        sentences = [SEE_EVIDENCE if numbers(s) - allowed else s for s in _SENTENCE.split(text) if s]
        return " ".join(_HYPE.sub("", " ".join(dict.fromkeys(sentences))).split())

    risks = tuple(_label_risk(fix(r)) for r in checked.risks)
    return Checked(checked.index, fix(checked.why_now), fix(checked.opportunity), risks,
                   " ".join(_HYPE.sub("", checked.next_step).split()), checked.evidence, checked.cited)


def _label_risk(risk: str) -> str:
    if risk.casefold().startswith("assumption") or re.search(r"\d", risk):
        return risk
    return f"assumption: {risk}"


async def verify(deps: TurnDeps, drafts: Mapping[int, dict], themes: Sequence[tuple[int, Cluster, Sequence[Item]]],
                 by_id: Mapping[str, Item], write_prompt: str) -> tuple[list[Checked], bool]:
    """(checked themes in input order, whether a repair call was made)."""
    checked = [check(n, drafts.get(n, {}), items, by_id, deps.now) for n, _, items in themes]
    problems = [v for c in checked for v in c.violations]
    repaired = False
    if problems and deps.budget.repairs_left > 0:
        deps.budget.repairs_left -= 1
        previous = json.dumps({"themes": [drafts.get(n, {}) for n, _, _ in themes]}, default=str)[:12_000]
        user = (f"{write_prompt}\n\nPROBLEMS:\n" + "\n".join(f"- {p}" for p in problems[:30])
                + f"\n\nPREVIOUS JSON:\n{prompts.fence('previous answer', previous)}")
        try:
            answer = await call_json(deps, prompts.REPAIR_SYSTEM, user)
            fixed = drafts_by_index(answer)
            checked = [check(n, fixed.get(n, drafts.get(n, {})), items, by_id, deps.now) for n, _, items in themes]
            repaired = True
        except LlmError as e:
            log.warning("repair call failed (%s); scrubbing the first answer", e)
    return [scrub(c, items, deps.now) for c, (_, _, items) in zip(checked, themes, strict=True)], repaired
