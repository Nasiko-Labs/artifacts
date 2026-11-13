"""Pure: what this conversation is working on, recovered from the platform's history slice (this agent
stores nothing between messages).

- The pasted sections and the brief instruction are rebuilt by replaying the user turns with the same rules
  a fresh paste follows (a paste starts a new brief unless it says "add this too"; "start over" clears;
  over the size cap keeps the first sections that fit), so the S-numbers come out as they were shown.
- The brief is read back from the newest assistant reply that shows one (brief_text.py).
- The two are used together only when the brief's "Sources:" line equals the key of the rebuilt sections:
  anything else means part of the material was trimmed from the history, and the agent asks for a re-paste
  instead of briefing from a partial or stale set.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import brief_text
import commands
import render
import sections as sec
from brief import Brief, Cite, Line
from grounding import MIN_SNIPPET_CHARS as MIN_QUOTE_CHARS
from grounding import normalise
from history import ASSISTANT, USER, Turn
from inputs import MAX_TOTAL_CHARS, split_instruction

_CLAUSE_BREAK = re.compile(r"(?<=[.;!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9$%]+")


@dataclass(frozen=True)
class Recalled:
    sections: tuple[sec.Section, ...] = ()   # verified against the shown brief; () when not
    instruction: str = ""
    shown: brief_text.Shown | None = None    # the newest brief reply, read back
    lost: bool = False                       # a brief (or its material) is in the history but not whole


def _after_start_over(turns: Sequence[Turn]) -> tuple[Turn, ...]:
    cut = max((i for i, t in enumerate(turns) if t.role == USER and commands.parse(t.text).kind == "start_over"),
              default=-1)
    return tuple(turns[cut + 1:])


def _joined(earlier: str, latest: str) -> str:
    return "\n".join(p.strip() for p in (earlier, latest) if p and p.strip())


def apply_paste(stored: Sequence[sec.Section], instruction: str, pasted: Sequence[sec.Section],
                new_instruction: str) -> tuple[tuple[sec.Section, ...], str, bool]:
    """(sections, instruction, cut) after a paste: added only on an explicit "add this too", else a new brief."""
    if stored and commands.wants_add(new_instruction) and not commands.wants_new(new_instruction):
        final, instruction = sec.append(stored, pasted), _joined(instruction, new_instruction)
    else:
        final, instruction = sec.number(pasted), new_instruction
    if sec.total_chars(final) > MAX_TOTAL_CHARS:
        return sec.first_that_fit(final, MAX_TOTAL_CHARS), instruction, True
    return tuple(final), instruction, False


def _replay(turns: Sequence[Turn]) -> tuple[tuple[sec.Section, ...], str, bool]:
    stored: tuple[sec.Section, ...] = ()
    instruction, broken = "", False
    for turn in turns:
        if turn.role != USER or commands.parse(turn.text).kind:
            continue
        said, source = split_instruction(turn.text)
        if not source:
            continue
        if turn.compressed:  # a paste the platform shortened: its sections can't be rebuilt
            stored, broken = (), True
            continue
        stored, instruction, _ = apply_paste(stored, instruction, sec.split(source), said)
        broken = False
    return stored, instruction, broken


def from_history(turns: Sequence[Turn]) -> Recalled:
    live = _after_start_over(turns)
    index = next((i for i in range(len(live) - 1, -1, -1)
                  if live[i].role == ASSISTANT and brief_text.looks_like_brief(live[i].text)), None)
    if index is None:
        return Recalled()
    shown = None if live[index].compressed else brief_text.read(live[index].text)
    if shown is None:
        return Recalled(lost=True)
    stored, instruction, broken = _replay(live[:index])
    if broken or not stored or render.source_key(stored) != shown.key_line:
        return Recalled(shown=shown, lost=True)
    return Recalled(stored, instruction, shown)


def with_quotes(brief: Brief, sections: Sequence[sec.Section]) -> Brief:
    """Fill each cite that has no snippet with the clause of its section that shares most words with the line
    (verbatim from the paste; the model's own snippet was not kept)."""
    by_id = {s.id: s.text for s in sections}

    def line(row: Line | None) -> Line | None:
        if row is None:
            return None
        cites = tuple(c if c.snippet else Cite(c.section, _best_clause(row.text, by_id.get(c.section, "")))
                      for c in row.cites)
        return replace(row, cites=tuple(c for c in cites if c.snippet))

    return replace(brief, headline=line(brief.headline), risks=tuple(map(line, brief.risks)),
                   decisions=tuple(map(line, brief.decisions)))


def _best_clause(text: str, section: str) -> str:
    wanted = set(_WORD.findall(normalise(text)))
    clauses = [c.strip() for c in _CLAUSE_BREAK.split(section) if len(c.strip()) >= MIN_QUOTE_CHARS]
    if not clauses:
        return ""
    return max(clauses, key=lambda c: len(wanted & set(_WORD.findall(normalise(c)))))
