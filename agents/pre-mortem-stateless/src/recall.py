"""Pure: the earlier plan and pre-mortem, re-read from the conversation history (this agent keeps no state).

- The plan: the newest user message whose plan part has 80+ words (cut to the first ~5k words when longer),
  plus any "add context: ..." the user sent after it.
- The pre-mortem: the newest assistant reply newer than that plan holding a full pre-mortem
  ("**Pre-mortem: N ways this plan fails** (read M lines)") or its JSON envelope, read back into the envelope
  the renderer uses. A partial view ("the top 3 of 7") is skipped.
Either may be missing (history trimmed, compressed or absent); callers then run again or ask for a re-paste.
Nothing is ever guessed: a story is read back only when every one of its fields parses.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import plan as plan_text
import prompts
from history import ASSISTANT, USER, Turn
from inputs import split_request, word_count
from rank import LEVEL

MIN_PLAN_WORDS = prompts.MIN_PLAN_WORDS
MAX_PLAN_CHARS = 30_000
HIGH_MIN_STORIES = 6
HIGH_MIN_LENSES = 3

_TITLE = re.compile(r"^\*\*Pre-mortem: (\d+) ways this plan fails\*\* \(read (\d+) lines\)", re.MULTILINE)
_STORY = re.compile(
    r"^\*\*(\d+)\. (.+?)\*\* \(likelihood (low|medium|high), impact (low|medium|high); raised by ([A-Za-z, ]+)\)\n"
    r"Story: (.*)\n(?:Breaks: \"(.*)\" )?\(L(\d+): \"(.*)\"\)\nEarly warning: (.*?) \| Mitigation: (.*)$",
    re.MULTILINE)
_ASSUMPTION = re.compile(r"^- (.+) \(L(\d+)\)$")
_QUESTION = re.compile(r"^\d+\. (.+)$")
_LEFT_OUT = re.compile(r"_\((\d+) candidates? left out")
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_RERUN = re.compile(r"pre-?mortem|failure modes?|what could go wrong", re.IGNORECASE)
_ADD_CONTEXT = re.compile(r"^\W*add context\s*[:\-]?\s*(.+)$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Plan:
    text: str                      # the plan body, within the character cap
    context: tuple[str, ...] = ()
    words: int = 0


@dataclass(frozen=True)
class Prior:
    plan: Plan | None = None
    envelope: dict | None = None


def read(turns: Sequence[Turn]) -> Prior:
    plan_at, plan = _newest_plan(turns)
    envelope = None
    after_plan = turns[plan_at + 1:] if plan_at >= 0 else turns
    for index in range(len(after_plan) - 1, -1, -1):
        turn = after_plan[index]
        envelope = from_reply(turn.text) if turn.role == ASSISTANT else None
        if envelope is not None:
            if _rerun_asked(after_plan[index + 1:]):  # its successor's reply was dropped: this one is stale
                envelope = None
            break
    if plan is not None:
        added = tuple(m.group(1).strip() for t in turns[plan_at + 1:] if t.role == USER
                      for m in [_ADD_CONTEXT.match(t.text)] if m)
        plan = Plan(plan.text, (*plan.context, *added), plan.words)
    return Prior(plan, envelope)


def _rerun_asked(later: Sequence[Turn]) -> bool:
    """A later user turn asked for a new pre-mortem ("add context: ...", "pre-mortem it again"), so a reply
    older than it no longer holds the latest result."""
    return any(t.role == USER and (_ADD_CONTEXT.match(t.text) or _RERUN.search(t.text)) for t in later)


def _newest_plan(turns: Sequence[Turn]) -> tuple[int, Plan | None]:
    for index in range(len(turns) - 1, -1, -1):
        if turns[index].role != USER:
            continue
        found = from_paste(turns[index].text)
        if found is not None:
            return index, found
    return -1, None


def from_paste(text: str) -> Plan | None:
    request = split_request(text)
    body = prompts.strip_markers(request.plan)
    if word_count(body) < MIN_PLAN_WORDS:
        return None
    kept = plan_text.cut_first(body, MAX_PLAN_CHARS) if len(body) > MAX_PLAN_CHARS else body
    return Plan(kept, tuple(prompts.strip_markers(c) for c in request.context), word_count(kept))


def from_reply(text: str) -> dict | None:
    return _from_json(text) or _from_render(text)


def _from_json(text: str) -> dict | None:
    fenced = _FENCED_JSON.search(text or "")
    if fenced is None:
        return None
    try:
        data = json.loads(fenced.group(1))
    except ValueError:
        return None
    modes = data.get("failure_modes") if isinstance(data, dict) else None
    if not isinstance(modes, list) or not all(isinstance(m, dict) and "title" in m for m in modes):
        return None
    total = re.match(r"(\d+) failure modes", str(data.get("summary") or ""))
    if not modes or (total is not None and int(total.group(1)) != len(modes)):  # a "top N" view
        return None
    return data


def _from_render(text: str) -> dict | None:
    title = _TITLE.search(text or "")
    if title is None:
        return None
    modes = [_mode(m) for m in _STORY.finditer(text)]
    if not modes or len(modes) != int(title.group(1)):
        return None
    assumptions = [{"text": a.group(1), "plan_line": int(a.group(2))}
                   for a in (_ASSUMPTION.match(line) for line in _section(text, "**Unstated assumptions**")) if a]
    questions = [q.group(1) for q in (_QUESTION.match(line) for line in _section(text, "**Questions for the team**"))
                 if q]
    left = _LEFT_OUT.search(text)
    lenses = {lens for m in modes for lens in m["raised_by"]}
    level = ("low" if "confidence is low" in text else
             "high" if len(modes) >= HIGH_MIN_STORIES and len(lenses) >= HIGH_MIN_LENSES else "medium")
    return {"summary": f"{len(modes)} failure modes; top risk: {modes[0]['title']}.", "items": modes,
            "failure_modes": modes, "unstated_assumptions": assumptions, "questions_for_the_team": questions,
            "confidence": level, "needs_human": "Someone should review this" in text,
            "plan_lines": int(title.group(2)), "left_out": int(left.group(1)) if left else 0}


def _mode(match: re.Match) -> dict:
    rank, title, likelihood, impact, raised, story, root, line, ref, warning, mitigation = match.groups()
    return {"rank": int(rank), "title": title, "story": story, "root_assumption": root or "", "plan_line": int(line),
            "plan_ref": ref, "likelihood": likelihood, "impact": impact, "score": LEVEL[likelihood] * LEVEL[impact],
            "early_warning": warning, "mitigation": mitigation,
            "raised_by": [lens.strip().casefold() for lens in raised.split(",") if lens.strip()]}


def _section(text: str, heading: str) -> list[str]:
    start = text.find(heading + "\n")
    if start == -1:
        return []
    block = text[start + len(heading) + 1:].split("\n\n", 1)[0]
    return block.splitlines()
