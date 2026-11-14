"""Pure: what earlier turns of this conversation still give a follow-up, read from the platform's history
block (this agent stores nothing). Three things can be recalled:

- the source: the most recent draft the user pasted (`inputs.has_draft`), or another agent's envelope,
  with the options its request words set, layered with any short option follow-ups after it;
- the most recent humanized version this agent replied with (its text, without the footer);
- for "show the changes" / "show the patterns" / "as json": an Outcome rebuilt in code from that pair (pattern
  detection and a word diff), with zero LLM calls.

The history is a budgeted slice, so any of these may be missing: callers then use what is left or ask for a
re-paste. Nothing here invents content: every text comes from the message or the history."""

from __future__ import annotations

import difflib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field

import envelope
import options as opts
import prompts
from history import ASSISTANT, USER, HistoryTurn
from inputs import word_count
from natural import has_draft, split_draft
from outcome import CLEAN, OK, Outcome, found_phrases, removed_counts
from patterns import detect

COMMAND_MAX_WORDS = 12
MAX_DIFF_ROWS = 30
FOOTER_START = "\n\n_AI-pattern density"
_FENCED_JSON = "```json\n"


@dataclass(frozen=True)
class Memory:
    draft: str = ""                       # the last pasted draft, as the user gave it
    envelope: dict | None = None          # or: the last input envelope
    options: opts.Options = field(default_factory=opts.Options)
    humanized: str = ""                   # the last humanized text this agent replied with

    @property
    def has_any(self) -> bool:
        return bool(self.draft or self.envelope or self.humanized)


def source_of_text(text: str) -> tuple[str, str]:
    """(request words, draft) of one message, fence markers stripped from both."""
    request, draft = split_draft(text)
    return prompts.strip_markers(request), prompts.strip_markers(draft).strip()


def memory(turns: Sequence[HistoryTurn]) -> Memory:
    reply_at, humanized = _last_humanized(turns)
    source_at = _last_source(turns, reply_at if reply_at >= 0 else len(turns))
    if source_at < 0:
        return Memory(humanized=humanized)
    text = turns[source_at].text
    parsed_env = envelope.parse(text)
    request, draft = (parsed_env[1], "") if parsed_env else source_of_text(text)
    base = opts.parse(request).options
    for turn in turns[source_at + 1:]:
        if turn.role == USER and word_count(turn.text) <= COMMAND_MAX_WORDS:
            base = opts.parse(turn.text, base).options
    return Memory(draft=draft, envelope=parsed_env[0] if parsed_env else None, options=base, humanized=humanized)


def humanized_text(reply: str) -> str:
    """The humanized draft in one of this agent's replies (text + footer, or a JSON envelope), else ""."""
    text = (reply or "").strip()
    if text.startswith(_FENCED_JSON):
        try:
            data = json.loads(text.removeprefix(_FENCED_JSON).split("\n```")[0])
        except ValueError:
            return ""
        return data.get("text", "") if isinstance(data, dict) and isinstance(data.get("text"), str) else ""
    at = text.find(FOOTER_START)
    return text[:at].strip() if at > 0 else ""


def rebuilt(original: str, humanized: str) -> Outcome:
    """The pass as code can see it from its two ends: densities, removed patterns and a word diff."""
    before, after = detect(original), detect(humanized)
    if original.strip() == humanized.strip():
        return Outcome(CLEAN, original, before.density, before.density, found=found_phrases(before))
    return Outcome(OK, humanized, before.density, after.density, tuple(word_diff(original, humanized)),
                   removed=removed_counts(before, after), found=found_phrases(before))


def word_diff(original: str, humanized: str) -> list[dict]:
    a, b = original.split(), humanized.split()
    rows = [{"category": "edit", "before": " ".join(a[i1:i2]), "after": " ".join(b[j1:j2])}
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
            if tag != "equal"]
    return rows[:MAX_DIFF_ROWS]


def _last_humanized(turns: Sequence[HistoryTurn]) -> tuple[int, str]:
    for at in range(len(turns) - 1, -1, -1):
        if turns[at].role == ASSISTANT and humanized_text(turns[at].text):
            return at, humanized_text(turns[at].text)
    return -1, ""


def _last_source(turns: Sequence[HistoryTurn], before: int) -> int:
    for at in range(min(before, len(turns)) - 1, -1, -1):
        turn = turns[at]
        if turn.role != USER:
            continue
        if envelope.parse(turn.text) is not None or has_draft(*source_of_text(turn.text)):
            return at
    return -1
