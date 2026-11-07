"""Step 3, the one model call per analysis (plus at most one repair): word hypotheses around the computed
evidence. The model sees the metric summary, the ranked dimensions, the evidence list and the events (all
fenced); its answer is validated here for shape and then checked in checks.py."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import analysis as an
import llm
import prompts
from candidates import Candidate
from events import Event
from evidence import Evidence

STATUSES = ("ok", "off_topic")


@dataclass(frozen=True)
class Drafted:
    status: str                    # ok | off_topic | failed
    hypotheses: tuple = ()


def summary_block(result: an.Analysis) -> str:
    lines = [(f"metric: {result.metric} ({result.kind}); segments: {result.segments}; "
              f"periods: {result.frame.labels[0]} -> {result.frame.labels[1]}"),
             "dimensions ranked (most concentrated change first): "
             + ", ".join(f"{b.dim} ({b.conc.label})" for b in result.dims)]
    if result.kind == "rate":
        lines.append(f"overall significance: {result.significance}")
    return "\n".join(lines)


def evidence_block(evidence: Sequence[Evidence]) -> str:
    return "\n".join(f"{e.id}: {e.text}" for e in evidence)


def events_block(events: Sequence[Event]) -> str:
    return "\n".join(f"{e.id}: {e.text}" for e in events)


def validate(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    items = data.get("hypotheses")
    if data["status"] == "ok" and not isinstance(items, list):
        return None
    return {"status": data["status"], "hypotheses": items if isinstance(items, list) else []}


async def draft(deps: llm.TurnDeps, result: an.Analysis, evidence: Sequence[Evidence], events: Sequence[Event],
                candidates: Sequence[Candidate], request: str, context: Sequence[str] = ()) -> Drafted:
    allowed = sorted({c.type for c in candidates})
    messages = prompts.hypotheses_messages(summary_block(result), evidence_block(evidence), events_block(events),
                                           allowed, request, context)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.HYPOTHESES_CONTRACT, validate)
    except llm.LlmError:
        return Drafted("failed")
    return Drafted(parsed["status"], tuple(parsed["hypotheses"]))
