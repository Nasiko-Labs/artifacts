"""A question about the stored memo ("is that safe?", "what if we delay?"): one ANSWER call over the memo as last
rendered, in 1-4 sentences. The stored brief, analysis, scores and recommendation are not touched, so the memo is
unchanged. Figures that neither the brief nor the memo hold are replaced by [figure?]. A wording change is not
handled here: it is a revision that re-writes the prose over the stored analysis (turns.py), which keeps the
scores and recommendation because both are decided in code from that analysis."""

from __future__ import annotations

import re

import llm
import prompts
from text import clean, figures, replace_ungrounded

MAX_ANSWER_SENTENCES = 4
UNCHANGED = ('_The memo is unchanged (same scores and recommendation). To change it, say "recommend A instead", '
             '"change the weights: ...", "add option C: ..." or "add data: ..."._')
_QUESTION = re.compile(r"\?\s*$|^(?:what|why|how|is|are|was|would|should|could|can|does|do|did|will|which|who|"
                       r"when|where)\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def is_question(text: str) -> bool:
    return bool(_QUESTION.search((text or "").strip()))


async def answer(deps: llm.TurnDeps, memo: str, source: str, question: str, policy) -> str:
    data = await llm.complete_json(deps, prompts.answer_messages(memo, question, policy), prompts.ANSWER_CONTRACT,
                                   _answer)
    sentences = _SENTENCES.split(clean(data["answer"]))[:MAX_ANSWER_SENTENCES]
    text = replace_ungrounded(" ".join(sentences), figures(f"{source}\n{memo}"), "[figure?]")
    return f"{text}\n\n{UNCHANGED}\n\n_{prompts.FOLLOWUP_HELP}_"


def _answer(data: object) -> dict | None:
    if not isinstance(data, dict) or data.get("status") != "ok" or not isinstance(data.get("answer"), str):
        return None
    return data if data["answer"].strip() else None
