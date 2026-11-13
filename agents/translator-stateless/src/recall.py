"""Pure: what earlier turns of this conversation still give a follow-up, read from the platform's history
block (this agent stores nothing).

- `replay` rebuilds the last job (source text, target, register, glossary) by reading the user turns
  oldest to newest with the same rules a live turn uses, without any LLM call. A user turn the agent
  declined (the next assistant turn is the off-topic reply) is skipped.
- `last_translation` finds the most recent plain-text translation reply and its target language (from the
  reply's own footer), for when the original text has been trimmed out of the history.

The history is a budgeted slice, so either may be missing; callers then say so and ask for a re-paste."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

import followup
import languages
import prompts
import request
import smalltalk
from history import ASSISTANT, USER, HistoryTurn
from request import Job

DECLINED_PREFIX = smalltalk.OFF_TOPIC_REPLY[:25]
ALREADY_PREFIX = prompts.ALREADY_IN.split("{")[0]
LANGUAGE_QUESTION = "Which language should I translate it into?"
_FOOTER = re.compile(r"^_(.+?) \((?:formal|neutral|casual)\) from [^.]*\.")


def carry(stored: Job | None, as_json: bool = False) -> Job:
    """A new job keeps the conversation's target, register and glossary, never its text."""
    if stored is None:
        return Job(as_json=as_json)
    return Job(target=stored.target, register=stored.register, keep=stored.keep, glossary=stored.glossary,
               as_json=as_json)


def replay(turns: Sequence[HistoryTurn]) -> Job | None:
    job: Job | None = None
    for n, turn in enumerate(turns):
        if turn.role != USER or _declined(turns, n):
            continue
        job = step(job, turn.text, _was_text(turns, n))
    return replace(job, as_json=False) if job is not None else None


def step(stored: Job | None, raw: str, was_text: bool = True) -> Job | None:
    """The job after one earlier user message, as a live turn would have left it. A bare message (no cue)
    counts as text to translate only when `was_text`: the agent's reply to it shows it was taken as text."""
    if smalltalk.reply_to(raw) is not None:
        return stored
    text = prompts.strip_markers(raw)
    instruction, body = request.split(text)
    envelope = request.envelope_from(body)
    if envelope or (instruction and body):
        base = replace(carry(stored), source="" if envelope else body, envelope=envelope)
        return request.apply_instruction(base, instruction)
    follow = followup.parse(text, stored)
    if follow is not None:
        return follow.job if follow.job is not None else stored
    if instruction:
        return request.apply_instruction(carry(stored), instruction)
    found = languages.from_answer(body)  # an answer to "which language?"
    if found is not None and stored is not None:
        return replace(stored, target=found.language.code, target_note=found.note, unknown="")
    return replace(carry(stored), source=body) if body.strip() and was_text else stored


def last_translation(turns: Sequence[HistoryTurn]) -> tuple[str, str] | None:
    """(translated text, its target language code) of the most recent plain translation reply, or None. The
    translation is everything before the reply's footer line ("_Spanish (formal) from English. ..._")."""
    for turn in reversed(turns):
        if turn.role == ASSISTANT and (found := _split_reply(turn.text)) is not None:
            return found
    return None


def _split_reply(reply: str) -> tuple[str, str] | None:
    paragraphs = reply.split("\n\n")
    for n in range(len(paragraphs) - 1, 0, -1):
        match = _FOOTER.match(paragraphs[n].strip())
        found = languages.from_answer(match.group(1)) if match else None
        body = "\n\n".join(paragraphs[:n]).strip()
        if found is not None and body and not body.startswith("```"):
            return body, found.language.code
    return None


def _was_text(turns: Sequence[HistoryTurn], n: int) -> bool:
    after = turns[n + 1] if n + 1 < len(turns) else None
    if after is None or after.role != ASSISTANT:
        return False
    reply = after.text
    return (_split_reply(reply) is not None or reply.startswith(("```", ALREADY_PREFIX))
            or LANGUAGE_QUESTION in reply)


def _declined(turns: Sequence[HistoryTurn], n: int) -> bool:
    after = turns[n + 1] if n + 1 < len(turns) else None
    return after is not None and after.role == ASSISTANT and after.text.startswith(DECLINED_PREFIX)
