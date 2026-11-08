"""The one model step: a <= 3-sentence narrative and, per critical/high change, <= 25 words on what could break,
written from facts computed in code. Checked in code: a sentence or line naming a number, path, address, owner
or other code-like token that is not in the facts is dropped; unknown addresses are dropped."""

from __future__ import annotations

import logging
import re
from typing import Any

import llm
import prompts
from analysis import Analysis
from deps import TurnDeps

log = logging.getLogger("terraform_blast_radius.narrate")

MAX_SENTENCES = 3
MAX_BREAK_WORDS = 25
MAX_FACT_ROWS = 15
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CODEY = re.compile(r"[^\s,;:()]*[._/@][^\s,;:()]*")


def facts(a: Analysis) -> dict:
    return {
        "plan_environment": a.env, "repository": a.repo,
        "changes": [{"address": c.address, "action": c.action, "risk_level": c.risk_level, "score": c.score,
                     "score_parts": list(c.parts), "changed_keys": list(c.keys)} for c in a.changes],
        "dependents": [{"depends_on": r.change, "kind": r.kind, "repo": r.repo, "path": r.path,
                        "environment": r.environment, "owner": r.owner} for r in a.affected[:MAX_FACT_ROWS]],
        "dependent_count": len(a.affected),
        "check_before_apply": list(a.checks),
    }


async def narrate(deps: TurnDeps, a: Analysis) -> tuple[str, tuple[tuple[str, str], ...]]:
    """(narrative, breaks); empty on any failure: the card renders without them."""
    data = facts(a)
    try:
        answer = await llm.complete_json(deps, prompts.narrate_messages(data), prompts.NARRATE_CONTRACT, _valid)
    except llm.LlmError as e:
        log.warning("narrate failed: %s", e)
        return "", ()
    return check(answer, data, a)


def _valid(data: Any) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("narrative"), str):
        return None
    breaks = data.get("breaks") if isinstance(data.get("breaks"), list) else []
    return {"narrative": data["narrative"], "breaks": [b for b in breaks if isinstance(b, dict)]}


def check(answer: dict, data: dict, a: Analysis) -> tuple[str, tuple[tuple[str, str], ...]]:
    seen = _flat(data)
    sentences = [s for s in _SENTENCE.split(answer["narrative"].strip()) if s and _grounded(s, seen)]
    narrative = " ".join(sentences[:MAX_SENTENCES])
    wanted = {c.address for c in a.changes if c.risk_level in ("critical", "high")}
    breaks: list[tuple[str, str]] = []
    for item in answer["breaks"]:
        address, text = str(item.get("address") or ""), str(item.get("text") or "").strip()
        if address in wanted and text and _grounded(text, seen) and address not in dict(breaks):
            breaks.append((address, " ".join(text.split()[:MAX_BREAK_WORDS])))
    return narrative, tuple(breaks)


def _grounded(text: str, seen: str) -> bool:
    numbers = _NUMBER.findall(text)
    tokens = [t.strip("`'\".") for t in _CODEY.findall(text)]
    return all(n in seen for n in numbers) and all(t in seen for t in tokens if any(c in t for c in "._/@"))


def _flat(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"{k}\n{_flat(v)}" for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(_flat(v) for v in value)
    return str(value)
