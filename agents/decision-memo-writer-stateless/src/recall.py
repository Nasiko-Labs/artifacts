"""Pure: the earlier brief, its changes and the last memo, re-read from the conversation history (this agent
keeps no state).

- The brief: the newest user message that is a brief on its own, or a reply to this agent's "what are the
  options?" combined with the user message before that question.
- Its changes: every later "recommend B instead" / "add option" / "add data" / "change the weights", in order
  (`amend.replay` applies them again in code).
- The last memo: the newest assistant reply after the brief and its last change, as rendered markdown (for
  "just the recommendation") or as its JSON envelope (for "as json"); both are served with no call.
Anything may be missing (history trimmed, compressed or absent); callers then rebuild or ask for a re-paste.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import amend
import brief as b
import followup as fu
import options as opts
import prompts
from history import ASSISTANT, USER, Turn
from inputs import with_envelope_as_data, without_json_words

MAX_BRIEF_CHARS = 8_000
NEW_BRIEF_MIN_CHARS = 200
RECOMMENDATION = "**Recommendation ("
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Prior:
    source: str = ""                          # the brief text, ready to parse
    changes: tuple[fu.Command, ...] = ()
    memo_text: str = ""
    envelope: dict | None = None


def source_of(text: str) -> str:
    return with_envelope_as_data(without_json_words(text[:MAX_BRIEF_CHARS]))


def looks_like_new_brief(text: str, parsed: b.Brief) -> bool:
    return len(parsed.options) >= 2 or bool(parsed.criteria) or len(text) >= NEW_BRIEF_MIN_CHARS


def is_full_brief(text: str, parsed: b.Brief) -> bool:
    """A brief that stands on its own (criteria, or long), not just a list of options."""
    return bool(parsed.criteria) or len(text) >= NEW_BRIEF_MIN_CHARS


def answering_options(turns: Sequence[Turn], text: str) -> str:
    """The decision asked about just before plus `text` as its options, when the last assistant turn was this
    agent asking for the options; else ""."""
    if len(turns) < 2 or turns[-1].role != ASSISTANT or not turns[-1].text.startswith(prompts.NEED_OPTIONS):
        return ""
    if turns[-2].role != USER:
        return ""
    found = opts.from_section(text)
    if len(found) < 2 and len(text.split()) < 2:  # a lone short word is not an option list
        return ""
    return turns[-2].text.strip() + "\n\nOptions:\n" + "\n".join(f"- {f}" for f in found)


def read(turns: Sequence[Turn]) -> Prior:
    at, source = _newest_brief(turns)
    if at < 0:
        return Prior()
    changes, last = [], at
    for index in range(at + 1, len(turns)):
        command = fu.parse(turns[index].text) if turns[index].role == USER else fu.Command("")
        if command.kind in amend.KINDS:
            changes.append(command)
            last = index
    memo_text, envelope = "", None
    for turn in reversed(turns[last + 1:]):
        if turn.role != ASSISTANT:
            continue
        envelope = _envelope(turn.text)
        memo_text = turn.text if envelope is None and RECOMMENDATION in turn.text else ""
        if envelope is not None or memo_text:
            break
    return Prior(source, tuple(changes), memo_text, envelope)


def _newest_brief(turns: Sequence[Turn]) -> tuple[int, str]:
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.role != USER or fu.parse(turn.text).kind:
            continue
        source = source_of(turn.text)
        parsed = b.parse(source)
        combined = answering_options(turns[:index], turn.text)
        if combined and not is_full_brief(turn.text, parsed) and b.parse(source_of(combined)).options:
            return index, source_of(combined)
        if parsed.options and looks_like_new_brief(turn.text, parsed):
            return index, source
    return -1, ""


def _envelope(text: str) -> dict | None:
    fenced = _FENCED_JSON.search(text or "")
    if fenced is None:
        return None
    try:
        data = json.loads(fenced.group(1))
    except ValueError:
        return None
    ok = isinstance(data, dict) and isinstance(data.get("memo_markdown"), str) and "recommendation" in data
    return data if ok else None


def recommendation(memo_text: str) -> str:
    """The recommendation line of a memo reply, or ""."""
    return next((line for line in memo_text.splitlines() if line.startswith(RECOMMENDATION)), "")
