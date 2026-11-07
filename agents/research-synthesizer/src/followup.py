"""Follow-ups on a stored brief.

- Zero calls: "only conflicts", "show quotes", "findings with high confidence", "list sources", "unknowns",
  "as json" -> a re-render of the stored brief.
- One call: "what do the sources say about X?" -> answered from the stored claims only; the answer must cite
  known claim ids and add no number the cited claims lack. Not covered -> "The sources don't cover X", and X
  joins this conversation's unknowns. Off-topic -> the fixed decline.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding as g
import llm
import prompts
import smalltalk
from extract import cut_words
from model import Claim

COMMAND_MAX_WORDS = 10
ANSWER_MAX_WORDS = 80
TOPIC_MAX_CHARS = 120

_COMMANDS = (
    ("conflicts", re.compile(r"\b(?:conflicts?|conflicting|disagree(?:ments?)?|contradictions?)\b")),
    ("high", re.compile(r"\bhigh(?:[ -]confidence)?\b|\bstrongest\b")),
    ("quotes", re.compile(r"\b(?:quotes?|evidence|citations?)\b")),
    ("sources", re.compile(r"\b(?:list|show|which) (?:the |my |all )?sources\b|^sources(?: read)?$")),
    ("unknowns", re.compile(r"\bunknowns?\b|\bwhat(?:'s| is) (?:missing|unknown)\b|\bgaps\b")),
    ("default", re.compile(r"^(?:(?:show|give)(?: me)? (?:the |full )?brief|(?:full )?brief|as json|json only|in json)$")),
)
_ABOUT = re.compile(r"\babout (.+?)\s*\??$", re.IGNORECASE)
_WORDS = re.compile(r"[^a-z0-9' -]+")


@dataclass(frozen=True)
class Answer:
    reply: str
    not_covered: str = ""      # the topic to add to unknowns


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str) -> str | None:
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS or plain.startswith(("what do", "what does", "does ", "do ")):
        return None
    for view, pattern in _COMMANDS:
        if pattern.search(plain):
            return view
    return None


def topic_of(question: str) -> str:
    match = _ABOUT.search(question.strip())
    topic = match.group(1) if match else question.strip().rstrip("?")
    return " ".join(prompts.strip_markers(topic).split())[:TOPIC_MAX_CHARS]


async def ask(deps: llm.TurnDeps, question: str, claims: Sequence[Claim], context: Sequence[str] = ()) -> Answer:
    block = "\n".join(f'{c.id} ({c.source_id}, {c.kind}) {c.text} | quote: "{c.quote}"' for c in claims)
    msgs = prompts.messages("ASK", "Answer the user's question only from the CLAIMS block (checked claims from the "
                            "sources the user pasted earlier).", prompts.ASK_CONTRACT,
                            [prompts.fence("CLAIMS", block), prompts.fence("USER_REQUEST", question)], context)
    try:
        parsed = await llm.complete_json(deps, msgs, prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return Answer(prompts.MODEL_FAILED)
    if parsed["status"] == "off_topic":
        return Answer(smalltalk.OFF_TOPIC_REPLY)
    return answer_text(parsed, question, claims)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", "not_covered", "off_topic"):
        return None
    ids = data.get("claim_ids") if isinstance(data.get("claim_ids"), list) else []
    return {"status": data["status"], "answer": str(data.get("answer") or ""), "claim_ids": [str(i) for i in ids]}


def answer_text(parsed: Mapping[str, Any], question: str, claims: Sequence[Claim]) -> Answer:
    by_id = {c.id: c for c in claims}
    cited = [by_id[i] for i in dict.fromkeys(parsed["claim_ids"]) if i in by_id]
    answer = " ".join(prompts.strip_markers(parsed["answer"]).split())
    evidence = g.normalise(" ".join(f"{c.text} {c.quote}" for c in cited))
    if parsed["status"] != "ok" or not cited or not answer or g.ungrounded_tokens(answer, evidence):
        topic = topic_of(question)
        return Answer(f"The sources don't cover {topic}. I've added it to the unknowns for this brief.", topic)
    tags = ", ".join(dict.fromkeys(c.source_id for c in cited))
    quotes = "\n".join(f'- {c.source_id}: "{" ".join(c.quote.split())}"' for c in cited)
    return Answer(f"{cut_words(answer, ANSWER_MAX_WORDS)} [{tags}]\n\n{quotes}")
