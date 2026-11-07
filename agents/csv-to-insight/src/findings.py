"""The findings step: one model call (plus one repair) that words findings from code-built facts, each
citing fact ids. Every finding is checked in code (checks.py); without the model, the facts' own
sentences are the findings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import llm
import prompts
from checks import Checked, check, fact_findings
from result import Fact, Result


def facts_block(results: Sequence[Result]) -> str:
    return "\n".join(f"{f.id} ({r.id} {r.kind}): {f.text}" for r in results for f in r.facts)


def lead_facts(results: Sequence[Result]) -> list[Fact]:
    """The first fact of each result, then the rest: the order findings fall back to."""
    firsts = [r.facts[0] for r in results if r.facts]
    return firsts + [f for r in results for f in r.facts[1:]]


def parse_findings(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("findings"), list):
        return None
    caveats = data.get("caveats", [])
    return {"findings": data["findings"], "caveats": caveats if isinstance(caveats, list) else []}


async def write_findings(deps: llm.TurnDeps, results: Sequence[Result], question: str,
                         context: Sequence[str] = (), source: str = "") -> Checked:
    facts = {f.id: f for r in results for f in r.facts}
    if not facts:
        return Checked((), ())
    try:
        data = await llm.complete_json(deps, prompts.findings_messages(facts_block(results), question, context),
                                       prompts.FINDINGS_CONTRACT, parse_findings)
    except llm.LlmError:
        fallback = fact_findings(lead_facts(results))
        return Checked(fallback, (), fell_back=len(fallback))
    checked = check(data, facts, source)
    if checked.findings:
        return checked
    fallback = fact_findings(lead_facts(results))
    return Checked(fallback, checked.caveats, checked.fell_back + len(fallback), checked.dropped)
