"""Pure: what the platform's chat history still holds, read back for a follow-up. Nothing is stored between
messages, so this is the agent's only memory.

- The diff: the newest user message in the history with a diff in it (with its context, template and style
  words), plus the style words and "why:" lines the user sent after it, replayed in order.
- The current draft: the newest reply that rendered one (it starts with `**Title:**`), as markdown. A
  follow-up restyles it against facts rebuilt from the diff in code, so it costs the same one call as before.
- Whether the user's message answers this agent's own "paste the diff" reply."""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import history
import prompts
from inputs import Split, Style, split_input, style_of

DRAFT_MARK = "**Title:**"
_STYLE_WORDS = re.compile(
    r"\b(?:please|make|it|the|this|a|title|description|pr|use|more|be|and|shorter|short|brief(?:er)?|concise|"
    r"tighter|plain|no|not|without|conventional|commits?|emojis?|version)\b|[\s,.;:!?'\"-]+", re.IGNORECASE)


@dataclass(frozen=True)
class Earlier:
    split: Split          # the earlier message with the diff, split as on its own turn
    style: Style          # its style words, then every later message's
    context: str          # its context plus every later "why:" line
    draft: str            # the newest rendered draft still in the history ("" when trimmed)


def earlier_diff(turns: tuple[history.Turn, ...]) -> Earlier | None:
    users = [t.text for t in turns if t.role == history.USER]
    for index in range(len(users) - 1, -1, -1):
        split = split_input(users[index])
        if not split.parsed.found:
            continue
        style, context = style_of(users[index]), split.context
        for later in users[index + 1:]:
            style = style_of(later, style)
            if followup.adds_why(later):
                context = f"{context}\n{later}".strip()
        drafts = [t.text for t in turns if t.role == history.ASSISTANT and t.text.startswith(DRAFT_MARK)]
        return Earlier(split, style, context, drafts[-1] if drafts else "")
    return None


def style_only(text: str) -> bool:
    """A follow-up that is only style words ("shorter, plain title") or a "why:" line: a fresh draft with the
    merged style and context answers it, no restyle call needed."""
    return followup.adds_why(text) or not _STYLE_WORDS.sub("", text or "").strip()


def answering_ask(turns: tuple[history.Turn, ...]) -> bool:
    return history.last_assistant(turns).startswith(prompts.NEED_DIFF[:40])


def ask_context(turns: tuple[history.Turn, ...]) -> str:
    """The user's words that led to the "paste the diff" reply: kept as context for the diff that answers it."""
    asked = history.user_before_last_assistant(turns)
    return "" if split_input(asked).parsed.found else asked.strip()
