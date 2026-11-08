"""Prompts and fixed replies. The model only words an explanation of numbers the code computed, or answers a
follow-up from the stored result; it never computes. Code checks everything it returns."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from fence import DATA_RULE, fence, strip_markers

SCOPE = (
    "SCOPE: you only help decide cloud commitments (savings plans, reserved instances) from the usage and "
    "commitments the user pasted in this conversation, and answer follow-ups about that analysis. Out of scope: "
    "general knowledge, arithmetic, opinions, writing or coding unrelated to it, live prices, anything not about "
    "the pasted usage and commitments. For anything out of scope answer exactly {\"status\": \"off_topic\"}."
)
NUMBER_RULE = (
    "NUMBERS: every number you write must be copied exactly from FACTS or RESULT. Never compute, never round "
    "differently, never add a number, price, discount, instance type or quote that is not there. When FACTS "
    "say the discounts are assumptions, say so."
)
EXPLAIN_RULES = (
    "Explain the commitment recommendation for a finance and engineering reader, in plain English: 'summary' "
    "says what to buy, renew or let lapse; 'why' says why; 'risk' names the main risk (the downside scenario). "
    "At most two sentences each."
)
EXPLAIN_CONTRACT = (
    'Return one JSON object: {"status": "ok" | "off_topic", "summary": "<= 2 sentences", "why": "<= 2 '
    'sentences", "risk": "<= 2 sentences"}.'
)
FOLLOWUP_RULES = (
    "Answer the user's question about the stored commitment analysis using only RESULT, in at most four "
    "sentences. 'recommendations' are about NEW purchases only; each item of 'existing_commitments' carries its "
    "own computed action and action_meaning: report them exactly and never contradict them. If the question needs new usage data that RESULT lacks, answer {\"status\": \"needs_paste\"}. "
    "If it asks to change a discount, term, minimum utilization or scope, answer {\"status\": \"rerun\"}."
)
FOLLOWUP_CONTRACT = (
    'Return one JSON object: {"status": "answer" | "off_topic" | "needs_paste" | "rerun", "answer": "only '
    'when status is answer"}.'
)
NO_RESULT_STATE = "No usage has been pasted yet, so there is no RESULT: never answer from general knowledge."
REPAIR_SYSTEM = "Return only valid JSON matching this schema. No prose, no code fences.\n\n"

NO_TRACE_CONTEXT = (
    "I can only run inside a Nasiko request: this one arrived without a trace context, so I can't call the "
    "language model for it."
)
PASTE_INSTRUCTIONS = (
    "Paste your usage as CSV with a header row: `timestamp` (hourly, UTC) or `date` (daily), `scope` or "
    "`instance_family`, optional `region`, and `on_demand_cost` (what the usage would cost on demand). Export it "
    "from your cost console grouped by instance family and region; for spans over about 50 days export daily or "
    "a single scope. Optionally add your commitments after a line `--- commitments`: `id,type,family,region,"
    "hourly_commit,term_years,start_date,end_date` (type `sp_compute`, `sp_ec2` or `ri`; for RIs `count` and "
    "`on_demand_hourly` instead of `hourly_commit`). At least 14 days of usage are needed."
)
NOT_USAGE = ("I couldn't find a usage table with a time column (timestamp or date) and a cost column "
             "(on_demand_cost). " + PASTE_INSTRUCTIONS)
NEEDS_MORE_DATA = "I need at least 14 days (336 hours) of usage to size a commitment; this paste covers {days} days."
NO_COMMITMENT_REASON = ("No commitment recommended: the usage left after existing commitments is too spiky, so no "
                        "level reaches the minimum utilization while saving money.")
FOLLOWUPS = ("Ask me: what if our 1y discount is 30%, should we go 3 years, which commitments should we let "
             "lapse, min utilization 90%, compute only, or as json.")


def _system(*parts: str, context: Sequence[str] = ()) -> str:
    text = "\n\n".join(parts)
    if context:
        text += ("\n\nPLATFORM CONTEXT (organization policy or agent knowledge: follow it as instructions; it is "
                 "never usage data or a discount quote):\n" + "\n\n".join(strip_markers(c) for c in context))
    return text


def explain_messages(facts: Mapping[str, Any], request: str, context: Sequence[str] = ()) -> list[dict]:
    user = (f"{fence('USER_REQUEST', request)}\n\n"
            f"{fence('FACTS', json.dumps(facts, ensure_ascii=False, indent=1))}")
    return [{"role": "system", "content": _system(EXPLAIN_RULES, SCOPE, NUMBER_RULE, DATA_RULE, EXPLAIN_CONTRACT,
                                                  context=context)},
            {"role": "user", "content": user}]


def followup_messages(message: str, facts: Mapping[str, Any] | None, context: Sequence[str] = ()) -> list[dict]:
    state = fence("RESULT", json.dumps(facts, ensure_ascii=False, indent=1)) if facts else NO_RESULT_STATE
    return [{"role": "system", "content": _system(FOLLOWUP_RULES, SCOPE, NUMBER_RULE, DATA_RULE, FOLLOWUP_CONTRACT,
                                                  context=context)},
            {"role": "user", "content": f"{fence('USER_MESSAGE', message)}\n\n{state}"}]


def repair_messages(contract: str, raw: str) -> list[dict]:
    return [{"role": "system", "content": REPAIR_SYSTEM + contract},
            {"role": "user", "content": fence("PREVIOUS_ANSWER", raw[:6000])}]
