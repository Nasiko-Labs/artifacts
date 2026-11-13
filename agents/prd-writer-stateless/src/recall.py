"""Pure: what earlier turns left behind, recovered from the platform's chat history (no checkpointer).

- `brief`: the most recent brief the user pasted (`enough_brief` once the instruction is split off), with
  the instruction that came with it (word cap, audience, title).
- `draft`: the most recent PRD we rendered (`# PRD: ...`), used as the source when the brief was trimmed.
- `shown`: the most recent full PRD the user saw (our latest reply holding one, markdown or JSON), read back by
  `shown.read`: a revision starts from it, so changes the editor made before it are kept.
- `short_brief`: when our last reply asked for more detail, the short request that prompted it, so the
  detail the user sends next is appended to it (what the paused agent did with an answer).
Only turns after the user's last "start over" count. Any field may be empty: the history is budgeted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import followup
import prompts
import shown as shown_mod
from history import ASSISTANT, USER, Turn
from inputs import MAX_BRIEF_CHARS, enough_brief, split_request
from prd import Prd
from shown import DRAFT_MARK


@dataclass(frozen=True)
class Prior:
    brief: str = ""
    instruction: str = ""
    draft: str = ""
    short_brief: str = ""
    shown: Prd | None = None

    @property
    def has_material(self) -> bool:
        return bool(self.brief or self.draft)


def recall(turns: Sequence[Turn]) -> Prior:
    turns = _since_start_over(turns)
    brief, instruction = _last_brief(turns)
    return Prior(brief=brief, instruction=instruction, draft=_last_draft(turns), short_brief=_short_brief(turns),
                 shown=_last_shown(turns))


def _since_start_over(turns: Sequence[Turn]) -> Sequence[Turn]:
    for i in range(len(turns) - 1, -1, -1):
        command = followup.parse_command(turns[i].text) if turns[i].role == USER else None
        if command is not None and command.kind == "start_over":
            return turns[i + 1:]
    return turns


def _last_brief(turns: Sequence[Turn]) -> tuple[str, str]:
    for turn in reversed(turns):
        if turn.role != USER:
            continue
        request = split_request(prompts.strip_markers(turn.text))
        if enough_brief(request.brief):
            return request.brief[:MAX_BRIEF_CHARS], request.instruction
    return "", ""


def _last_draft(turns: Sequence[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == ASSISTANT and turn.text.startswith(DRAFT_MARK):
            return turn.text[:MAX_BRIEF_CHARS]
    return ""


def _last_shown(turns: Sequence[Turn]) -> Prd | None:
    for turn in reversed(turns):
        if turn.role == ASSISTANT:
            prd = shown_mod.read(shown_mod.markdown_of(turn.text))
            if prd is not None:
                return prd
    return None


def _short_brief(turns: Sequence[Turn]) -> str:
    if len(turns) < 2 or turns[-1].role != ASSISTANT or not turns[-1].text.startswith(prompts.NEED_MORE_LEAD):
        return ""
    before = turns[-2]
    return prompts.strip_markers(before.text).strip() if before.role == USER else ""
