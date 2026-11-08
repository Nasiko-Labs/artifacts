"""Pure: the markdown reply, rendered in code (no raw HTML, no images, no links the input did not carry),
and the fenced json form for workflow steps."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from facts import Facts
from narrate import word_count

_CONFIDENCE_WHY = {"low": "too few periods or no comparison period", "medium": "see the notes above",
                   "high": "clean table, enough history, checked first time"}


def draft(narrative: str, facts: Facts, confidence: str, coverage: Sequence[str] = (), hint: str = "") -> str:
    lines = [f"**Narrative (draft, {word_count(narrative)} words)**", _escape(narrative), ""]
    compared = "the same week last month" if facts.compare == "month" else "the previous period"
    movers = ", ".join(f"{m.metric} {m.word} {m.pct}" for m in facts.movers) or "none above the 5% bar"
    lines.append(f"Compared {facts.period_current} with {facts.period_previous} ({compared}). Movers: {movers}.")
    if facts.flat:
        lines.append(f"Flat (under 1%): {', '.join(facts.flat)}.")
    if facts.excluded_partial:
        lines.append(f"Left out the partial period {facts.excluded_partial} (more than half its metric cells are "
                     "blank).")
    if facts.few_points:
        lines.append("Some metrics have fewer than four earlier periods, so the noise check was skipped for them.")
    lines.extend(coverage)
    lines.append(f"Confidence: {confidence} ({_CONFIDENCE_WHY[confidence]}).")
    if hint:
        lines.extend(["", hint])
    return "\n".join(lines)


def copyable(text: str) -> str:
    return f"```\n{text}\n```"


def json_block(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"


def _escape(text: str) -> str:
    """No raw HTML reaches the chat: angle brackets are shown as text."""
    return text.replace("<", "&lt;").replace(">", "&gt;")
