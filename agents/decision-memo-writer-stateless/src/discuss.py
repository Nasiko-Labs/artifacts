"""Follow-ups on the memo shown in history that change no input: a question ("is that safe?", "what if we
delay?") or a wording change ("make it punchier"). The options table, its scores and the recommendation are read
from that memo and kept exactly: nothing is re-analysed or re-scored. Only a change of inputs (weights, options,
data, constraints, "recommend X instead") rebuilds the memo (turns.py).

    a question       -> one ANSWER call: 1-4 sentences referencing the memo; the memo is unchanged
    a wording change -> one REWORD call: new context and "Why" text spliced into the memo; table, scores,
                        recommendation, risks and triggers stay as shown
Figures the brief and the memo do not hold are replaced by [figure?] in either."""

from __future__ import annotations

import re

import llm
import prompts
from text import clean, figures, replace_ungrounded

MAX_ANSWER_SENTENCES = 4
MAX_WHY = 3
UNCHANGED = ('_The memo is unchanged (same scores and recommendation). To change it, say "recommend A instead", '
             '"change the weights: ...", "add option C: ..." or "add data: ..."._')
REWORDED = "_Reworded only: the options, scores and recommendation are exactly as before._"
_QUESTION = re.compile(r"\?\s*$|^(?:what|why|how|is|are|was|would|should|could|can|does|do|did|will|which|who|"
                       r"when|where)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def is_question(text: str) -> bool:
    return bool(_QUESTION.search((text or "").strip()))


def memo_part(reply: str) -> str:
    """The memo blocks of a reply (its italic footer notes dropped, the (j) legend kept)."""
    blocks = [b for b in (reply or "").split("\n\n") if b.strip()]
    return "\n\n".join(b for b in blocks if not b.startswith("_") or b.startswith("_(j)"))


async def answer(deps: llm.TurnDeps, memo: str, source: str, question: str, policy) -> str:
    data = await llm.complete_json(deps, prompts.answer_messages(memo, question, policy), prompts.ANSWER_CONTRACT,
                                   _answer)
    sentences = _SENTENCES.split(clean(data["answer"]))[:MAX_ANSWER_SENTENCES]
    text = replace_ungrounded(" ".join(sentences), figures(f"{source}\n{memo}"), "[figure?]")
    return f"{text}\n\n{UNCHANGED}\n\n_{prompts.FOLLOWUP_HELP}_"


async def reword(deps: llm.TurnDeps, memo: str, source: str, instruction: str, policy) -> str:
    data = await llm.complete_json(deps, prompts.reword_messages(memo, instruction, policy), prompts.REWORD_CONTRACT,
                                   _reworded)
    allowed = figures(f"{source}\n{memo}")
    context = replace_ungrounded(clean(data["context"]), allowed, "[figure?]")
    why = [replace_ungrounded(clean(w), allowed, "[figure?]") for w in data["why"][:MAX_WHY] if clean(w)]
    return f"{splice(memo, context, why)}\n\n{REWORDED}\n\n_{prompts.FOLLOWUP_HELP}_"


def splice(memo: str, context: str, why: list[str]) -> str:
    """The memo with its Context body and "Why" list replaced; every other block exactly as shown."""
    out = []
    for block in memo.split("\n\n"):
        head = block.partition("\n")[0]
        if head == "**Context**" and context:
            block = f"{head}\n{context}"
        elif head == "**Why**" and why:
            block = head + "\n" + "\n".join(f"{i}. {w}" for i, w in enumerate(why, start=1))
        out.append(block)
    return "\n\n".join(out)


def _answer(data: object) -> dict | None:
    if not isinstance(data, dict) or data.get("status") != "ok" or not isinstance(data.get("answer"), str):
        return None
    return data if data["answer"].strip() else None


def _reworded(data: object) -> dict | None:
    if not isinstance(data, dict) or data.get("status") != "ok" or not isinstance(data.get("context"), str):
        return None
    why = data.get("why") if isinstance(data.get("why"), list) else []
    return {**data, "why": [w for w in why if isinstance(w, str)]}
