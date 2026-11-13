"""Pure: the customer-impact backstop. When the model labelled no fact as impact, a clause of the thread that
states one ("our ops team is blind", "can't work", "40 agents are blocked", "losing revenue") becomes the impact
fact, quoted verbatim and checked like any model fact, so a stated impact is never reported as "Not stated"."""

from __future__ import annotations

import re
from collections.abc import Sequence

import facts as fx
from messages import Message, own

MAX_BACKSTOP_FACTS = 2
_WHO = r"(?:users?|agents?|team|teams|staff|employees|people|customers?|reps|everyone|nobody|ops|\d[\d,]*\s+\w+)"
_CUE = re.compile(
    r"\b(?:blind|dead in the water|at a standstill"
    r"|can(?:not|'t|’t)\s+(?:work|operate|function|do (?:their|our|any) (?:job|work)|log ?in|access|export|ship|"
    r"bill|invoice|use|close|process|sell|serve)"
    r"|unable to\s+(?:work|operate|function|log ?in|access|export|ship|bill|invoice|use|close|process|sell|serve)"
    r"|losing (?:revenue|money|sales|deals|customers|data)|lost (?:revenue|sales|deals|data)|revenue (?:loss|at risk)"
    rf"|{_WHO}\b[^.;!?\n]{{0,20}}\b(?:are|is|were|was|been|being|stay|remain)\s+(?:all\s+)?(?:blocked|stuck|down|idle))\b",
    re.IGNORECASE)
_CLAUSE = re.compile(r"(?<=[.;!?])\s+|,\s*(?:and|but|so)\s+"
                     r"|\s+(?:and|but|so)\s+(?=(?:our|their|the|we|they|my|his|her|its)\b)", re.IGNORECASE)


def _clauses(body: str) -> list[str]:
    return [c.strip(" .;!?") for c in _CLAUSE.split(body or "") if c.strip(" .;!?")]


def backstop(facts: Sequence[fx.Fact], thread: str, messages: tuple[Message, ...]) -> tuple[fx.Fact, ...]:
    """`facts` unchanged when one is already an impact; else with up to two impact clauses added (thread order)."""
    if fx.of_kind(facts, "impact"):
        return tuple(facts)
    found = []
    for message in own(messages):
        for clause in _clauses(message.body):
            if _CUE.search(clause):
                found.append({"id": f"impact{len(found) + 1}", "kind": "impact", "text": clause, "snippet": clause,
                              "message": message.id})
    checked = fx.verify(found[:MAX_BACKSTOP_FACTS], thread, messages).facts
    return fx.merge([tuple(facts), checked]) if checked else tuple(facts)
