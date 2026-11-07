"""The write step: one JSON call for the memo's words, then code checks, at most one repair (shared
with the turn), and code overrides when the model still disagrees with the decided option.

Checks: the recommended option must be the decided one; a rationale line must reference a known pro/con
id or criterion and carry no invented figure; a reversal trigger must reference a criterion or a verbatim
phrase of the brief, generic ones are dropped, an invented figure becomes [threshold?]; fewer than 3
triggers is a problem. Invented figures in the context or recommendation become [figure?]."""

from __future__ import annotations

import re
from typing import Any

import criteria as crit
import llm
import options as opts
import prompts
from analyse import option_key
from brief import Brief
from grounding import Ground
from text import clean, first_words

CONTEXT_MAX_WORDS = 90
MAX_RATIONALE = 3
MIN_TRIGGERS, MAX_TRIGGERS = 3, 5
_GENERIC = re.compile(r"circumstances change|things (?:go wrong|change)|situation changes|new information|"
                      r"priorities (?:shift|change)|market conditions|unforeseen|if needed|something changes|"
                      r"anything changes|requirements change", re.IGNORECASE)


def validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "ok" and not str(data.get("recommended_option") or "").strip():
        return None
    return data


def _ids(analysis: dict) -> set[str]:
    return {p["id"] for side in ("pros", "cons") for rows in analysis[side].values() for p in rows}


def check(data: dict, brief: Brief, analysis: dict, basis: dict) -> tuple[dict, list[str]]:
    computed = [*map(str, basis["scores"].values()), *(str(round(c.weight)) for c in brief.criteria)]
    ground, problems, tally = Ground(brief, computed), [], [0, 0]
    said = str(data.get("recommended_option") or "").strip()
    chosen = option_key(said) or opts.match(said, brief.options) or said
    if chosen != basis["option"]:
        problems.append(f'recommended_option must be "{basis["option"]}" (decided by code), not "{chosen}".')
    context = first_words(ground.fill(clean(str(data.get("context") or "")), "[figure?]"), CONTEXT_MAX_WORDS)
    if not context:
        problems.append("context is empty.")
    rationale = _rationale(data.get("rationale"), brief, analysis, ground, tally)
    triggers = _triggers(data.get("reversal_triggers"), brief, basis, ground, tally)
    if len(triggers) < MIN_TRIGGERS:
        problems.append(f"only {len(triggers)} usable reversal_triggers; give 3-5, each an observable condition "
                        "whose ref is a criterion name or a phrase copied from the brief.")
    memo = {"context": context, "recommendation": ground.fill(clean(str(data.get("recommendation") or "")),
                                                              "[figure?]"),
            "rationale": rationale, "triggers": triggers, "chosen": chosen, "dropped": tally[0], "total": tally[1]}
    return memo, problems


def _rationale(rows: Any, brief: Brief, analysis: dict, ground: Ground, tally: list[int]) -> list[dict]:
    ids, kept = _ids(analysis), []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tally[1] += 1
        text, ref = clean(str(row.get("text") or "")), str(row.get("ref") or "").strip()
        ref = ref if ref in ids else crit.key_of(ref, brief.criteria)
        if not text or not ref or ground.ungrounded(text) or len(kept) >= MAX_RATIONALE:
            tally[0] += 1
            continue
        kept.append({"text": first_words(text, 25), "ref": ref})
    return kept


def _triggers(rows: Any, brief: Brief, basis: dict, ground: Ground, tally: list[int]) -> list[dict]:
    keys = {o.key for o in brief.options}
    kept = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tally[1] += 1
        text, ref = clean(str(row.get("trigger") or "")), str(row.get("ref") or "").strip()
        grounded_ref = crit.key_of(ref, brief.criteria) or (ref if ground.snippet_ok(ref) else "")
        if not text or _GENERIC.search(text) or not grounded_ref or len(kept) >= MAX_TRIGGERS:
            tally[0] += 1
            continue
        said = str(row.get("would_switch_to") or "")
        switch = option_key(said) or opts.match(said, brief.options)
        switch = switch if switch in keys and switch != basis["option"] else basis["runner_up"]
        kept.append({"trigger": first_words(ground.fill(text, "[threshold?]"), 20), "ref": grounded_ref,
                     "would_switch_to": switch})
    return kept


async def run(deps: llm.TurnDeps, messages: list[dict], brief: Brief, analysis: dict, basis: dict) -> dict:
    raw = await llm.call(deps, messages)
    data = validate(llm.load(raw))
    if data is None:
        if not deps.has_time() or not deps.budget.take_repair():
            raise llm.LlmError("the write answer did not match the contract and no repair is left")
        raw = await llm.call(deps, prompts.repair_json_messages(prompts.WRITE_CONTRACT, raw))
        data = validate(llm.load(raw))
        if data is None:
            raise llm.LlmError("the write answer did not match the contract after one repair")
    if data["status"] == "off_topic":
        return {"status": "off_topic"}
    memo, problems = check(data, brief, analysis, basis)
    if problems and deps.has_time() and deps.budget.take_repair():
        raw2 = await llm.call(deps, prompts.repair_messages(messages, raw, problems))
        again = validate(llm.load(raw2))
        if again is not None and again["status"] == "ok":
            memo, problems = check(again, brief, analysis, basis)
    return {"status": "ok", **_settle(memo, brief, basis), "repair_failed": bool(problems)}


def _settle(memo: dict, brief: Brief, basis: dict) -> dict:
    """Code wins on the option: a model that still disagrees is overwritten, and rationale that argues
    for another option is dropped."""
    if memo["chosen"] == basis["option"]:
        return {**memo, "overridden": False}
    rec = basis["option"]
    kept = [r for r in memo["rationale"] if not (r["ref"].endswith(tuple(f"pro{i}" for i in range(1, 9)))
                                                 and not r["ref"].startswith(f"{rec}-"))]
    name = next((o.name for o in brief.options if o.key == rec), rec)
    return {**memo, "rationale": kept, "recommendation": f"{name} is the recommended option on the basis below.",
            "overridden": True, "dropped": memo["dropped"] + len(memo["rationale"]) - len(kept)}
