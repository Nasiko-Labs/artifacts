"""Follow-ups on a stored postmortem: re-render commands and edits parsed in code (zero LLM calls), or one
quote-checked question about the stored log (one call, plus one repair)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import checks
import grounding as g
import llm
import prompts
import smalltalk
from assemble import with_actions
from records import TBD, Action, Postmortem

ASK_STATUSES = ("ok", "not_in_input", "off_topic")
COMMAND_MAX_WORDS = 10
KEYWORD_REST_MAX_WORDS = 2
OWNER_MAX_CHARS = 60
ACTION_MAX_CHARS = 200

_FILLER = re.compile(r"\b(?:please|pls|can you|could you|show me|give me|show|just|only|me|now|again|the|with|"
                     r"list|and|it|as a|a|all|of|what are)\b")
_COMMANDS = (
    ("json", re.compile(r"\b(?:json)\b")),
    ("sources", re.compile(r"\b(?:sources?|quotes?|evidence|citations?)\b")),
    ("actions", re.compile(r"^(?:action items?|actions?|todos?|follow ups?)$")),
    ("timeline", re.compile(r"^(?:timeline|events)$")),
    ("factors", re.compile(r"^(?:contributing factors?|factors?|causes?|root causes?)$")),
    ("questions", re.compile(r"^(?:open questions?|questions?)$")),
    ("shorter", re.compile(r"^(?:shorter|brief(?:er)?|tl ?;? ?dr|short(?:er)? version|summary)$")),
    ("default", re.compile(r"^(?:full|full version|(?:write )?postmortem|everything|whole thing)$")),
)
_ADD = re.compile(r"^\s*add (?:an? )?(?:action(?: item)?|ai|todo)\s*[:\-]\s*(?P<t>\S.*)$", re.IGNORECASE | re.DOTALL)
_OWNER = (re.compile(r"^\s*(?:set )?(?:the )?owner (?:of|for) (?P<id>a\d+) (?:is|to|=|:)\s*(?P<o>\S.*)$", re.IGNORECASE),
          re.compile(r"^\s*(?P<id>a\d+)(?:'s)? owner (?:is|=|:)\s*(?P<o>\S.*)$", re.IGNORECASE),
          re.compile(r"^\s*assign (?P<id>a\d+) to (?P<o>\S.*)$", re.IGNORECASE))
_WORDS = re.compile(r"[^a-z0-9;' ]+")


@dataclass(frozen=True)
class Edit:
    kind: str            # add | owner
    value: str
    action_id: str = ""


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str) -> str | None:
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS:
        return None
    core = " ".join(_FILLER.sub(" ", plain).split())
    for view, pattern in _COMMANDS:
        if view in ("json", "sources"):
            rest = pattern.sub(" ", core).split()
            if pattern.search(core) and len(rest) <= KEYWORD_REST_MAX_WORDS:
                return view
        elif pattern.search(core):
            return view
    return None


def parse_edit(text: str) -> Edit | None:
    added = _ADD.match(text or "")
    if added:
        return Edit("add", prompts.strip_markers(added["t"]).strip()[:ACTION_MAX_CHARS])
    for pattern in _OWNER:
        match = pattern.match(text or "")
        if match:
            return Edit("owner", match["o"].strip().rstrip(".!")[:OWNER_MAX_CHARS], match["id"].casefold())
    return None


def apply_edit(pm: Postmortem, edit: Edit) -> tuple[Postmortem | None, str]:
    """(new postmortem, note) or (None, reply) when the edit names an unknown action."""
    value = checks.clean_text(edit.value, edit.value)
    if not value:
        return None, "I need some text for that."
    if edit.kind == "add":
        action = Action(id=f"a{len(pm.actions) + 1}", title=value, owner=TBD, due=TBD, type="process", source="you")
        return with_actions(pm, (*pm.actions, action)), f"Added {action.id} (added by you)."
    if edit.action_id not in {a.id for a in pm.actions}:
        return None, f"There's no action item {edit.action_id}. The action items are {_ids(pm.actions)}."
    actions = tuple(replace(a, owner=value, owner_set_by_you=True) if a.id == edit.action_id else a
                    for a in pm.actions)
    return with_actions(pm, actions), f"Updated {edit.action_id}: owner {value} (set by you)."


def _ids(actions: Sequence[Action]) -> str:
    return ", ".join(a.id for a in actions) or "none yet"


async def ask(deps: llm.TurnDeps, question: str, log: str, context: Sequence[str] = ()) -> str:
    """One quote-checked answer from the stored log; with no log, the call still enforces the scope."""
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages(log or "(no log has been provided yet)",
                                                                    question, context),
                                         prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if not log:
        return smalltalk.NO_LOG_YET
    return answer_text(parsed, log)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""), "snippet": str(data.get("snippet") or "")}


def answer_text(parsed: Mapping[str, str], log: str) -> str:
    """`ok` stands only when its quote is in the log and the answer adds no number or time of its own."""
    norm = g.normalise(log)
    answer = checks.clean_text(parsed["answer"], log)
    snippet = " ".join(parsed["snippet"].split())
    if parsed["status"] != "ok" or not answer or g.locate(snippet, norm) is None or g.ungrounded_tokens(answer, norm):
        return prompts.NOT_IN_INPUT
    quote = checks.clean_text(checks.cap_snippet(snippet), snippet)
    return f'{checks.cut_words(answer, 80)}\n\n> "{quote}"'
