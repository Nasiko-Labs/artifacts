"""Compose: the bottom-line paragraph (<= 90 words), written from validated findings only. Code drops every
sentence lacking an [S#] tag, citing a source no finding cites, or carrying a number / date that no finding
or quote contains; when no sentence survives the answer gets one repair, then the brief has no paragraph."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import grounding as g
import llm
import prompts
from extract import cut_words
from model import Claim, Finding

log = logging.getLogger("research-synthesizer.compose")

BOTTOM_LINE_MAX_WORDS = 90
_TAG = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


async def bottom_line(deps: llm.TurnDeps, question: str, findings: Sequence[Finding], claims: Sequence[Claim],
                      context: Sequence[str] = ()) -> str:
    if not findings:
        return ""
    by_id = {c.id: c for c in claims}
    block = "\n".join(_line(f, by_id) for f in findings)
    msgs = prompts.messages("COMPOSE", "Write the bottom line of the brief from the FINDINGS block.",
                            prompts.COMPOSE_CONTRACT, [prompts.question_block(question), prompts.fence("FINDINGS", block)],
                            context)
    evidence = g.normalise(block)
    known = {s for f in findings for s in f.source_ids}

    def validate(data: Any) -> dict | None:
        return check(data, evidence, known)

    try:
        parsed = await llm.complete_json(deps, msgs, prompts.COMPOSE_CONTRACT, validate)
    except llm.LlmError:
        log.warning("compose failed its checks; rendering findings only")
        return ""
    return parsed["bottom_line"]


def _line(finding: Finding, by_id: Mapping[str, Claim]) -> str:
    kinds = sorted({by_id[i].kind for i in finding.claim_ids if i in by_id})
    quotes = " | ".join(f'{by_id[i].source_id}: "{by_id[i].quote}"' for i in finding.claim_ids if i in by_id)
    return (f"{finding.id} [{', '.join(finding.source_ids)}] ({finding.confidence}; {'/'.join(kinds)}; "
            f"{finding.agreement}) {finding.statement} -- {quotes}")


def check(data: Any, norm_evidence: str, known_sources: set[str]) -> dict | None:
    """Keeps only the sentences that cite known sources and add no number the findings lack; None when no
    sentence survives (so the caller repairs once, then renders findings only)."""
    if not isinstance(data, Mapping):
        return None
    text = prompts.strip_markers(" ".join(str(data.get("bottom_line") or "").split()))
    kept = [s for s in _SENTENCE.split(text) if s and _sentence_ok(s, norm_evidence, known_sources)]
    if not kept:
        return None
    return {"bottom_line": cut_words(" ".join(kept), BOTTOM_LINE_MAX_WORDS)}


def _sentence_ok(sentence: str, norm_evidence: str, known_sources: set[str]) -> bool:
    tags = _TAG.findall(sentence)
    cited = {s.strip() for tag in tags for s in tag.split(",")}
    if not cited or not cited <= known_sources:
        return False
    return not g.ungrounded_tokens(_TAG.sub(" ", sentence), norm_evidence)
