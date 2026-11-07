"""The assess step (one call): each commitment from the notes against the updates. Code keeps `done` or
`in_progress` only with an evidence quote found verbatim in the updates; anything else is `no_evidence`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import prompts
from grounding import clean_quote, in_source
from llm import TurnDeps, complete_json

STATUSES = ("done", "in_progress", "no_evidence")
UNASSESSED = "unassessed"


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("assessments"), list):
        return None
    return data


def commitments_block(commitments: Sequence[dict]) -> str:
    return "\n".join(f"{c['id']} ({c['who']}): {c['text']}" for c in commitments)


def apply(commitments: Sequence[dict], data: dict, updates: str) -> list[dict]:
    """New commitment dicts with `status` and, when verified, `evidence`."""
    by_id: dict[str, dict] = {}
    for raw in data.get("assessments") or ():
        if isinstance(raw, dict) and isinstance(raw.get("id"), str):
            by_id.setdefault(raw["id"], raw)
    out: list[dict] = []
    for c in commitments:
        raw = by_id.get(c["id"], {})
        status = raw.get("status") if raw.get("status") in STATUSES else "no_evidence"
        quote = clean_quote(str(raw.get("evidence_quote") or ""))
        if status != "no_evidence" and not in_source(quote, updates):
            status, quote = "no_evidence", ""
        out.append({**c, "status": status, **({"evidence": quote} if status != "no_evidence" else {})})
    return out


def unassessed(commitments: Sequence[dict]) -> list[dict]:
    return [{**c, "status": UNASSESSED} for c in commitments]


async def run(deps: TurnDeps, commitments: Sequence[dict], updates: str, policy: Sequence[str] = ()) -> list[dict]:
    """Raises LlmError; the caller falls back to unassessed carry-overs."""
    if not commitments:
        return []
    if not updates.strip():
        return [{**c, "status": "no_evidence"} for c in commitments]
    messages = prompts.assess_messages(commitments_block(commitments), updates, policy)
    data = await complete_json(deps, messages, prompts.ASSESS_CONTRACT, _validate)
    return apply(commitments, data, updates)
