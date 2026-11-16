"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         -> canned intro (no call)
    "put this in my tracker"                      -> fixed answer (no call)
    an envelope from a previous step              -> load it as the plan (no call), or revise it
    a plan is stored ─┬─ a new goal                -> plan (replaces the old one)
                      ├─ a question / team / rate  -> query (no call)
                      └─ anything else             -> revise (the model may still say off-topic)
    no plan ─┬─ a question about a plan            -> "no plan yet" (no call)
             ├─ vague goal that asks for a plan    -> pause: what should the end result be? (no call)
             └─ anything else                      -> plan (the model may say off-topic or needs info)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import envelope_in
import hitl
import prompts
import queries
import smalltalk
from inputs import MAX_GOAL_CHARS, Brief, Incoming, asks_for_plan, is_vague, parse_brief
from models import Plan

_NEW_GOAL = re.compile(r"^\s*(?:please\s+)?(?:break\s+(?:down|up)|plan\b|decompose|create (?:a |an )?(?:\w+ )?plan|"
                       r"make (?:a |an )?(?:\w+ ){0,2}plan|new (?:goal|plan)|here(?:'s| is) (?:our|the|my|a)\b|"
                       r"i need (?:a )?plan)", re.IGNORECASE)
_TASK_ID = re.compile(r"\bT\d+\b")


@dataclass(frozen=True)
class Route:
    kind: str                           # reply | pause | plan | revise | query | loaded | offer_tools
    reply: str = ""
    pending: hitl.Pending | None = None
    brief: Brief | None = None
    command: queries.Command | None = None
    plan: Plan | None = None            # for revise/query/loaded: the plan to work from
    instruction: str = ""
    vague_used: bool = False
    replaced: bool = False


def is_new_goal(text: str) -> bool:
    return len(text) > MAX_GOAL_CHARS or (bool(_NEW_GOAL.match(text)) and not _TASK_ID.search(text))


def plan_turn(incoming: Incoming, stored: Plan | None) -> Route:
    text = incoming.text
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", canned)
    env, rest = envelope_in.find(text)
    if env is not None:
        plan = envelope_in.to_plan(env, text)
        meaningful = rest and not queries.parse(rest)
        return Route("revise", plan=plan, instruction=rest) if meaningful else Route("loaded", plan=plan)
    command = queries.parse(text)
    if command and command.kind == "tracker":
        # "file this in Jira/Linear/…": the agent itself drafts only, but if the operator has
        # attached a tool that can do it, use it; otherwise a generic "connect a tool" reply.
        return Route("offer_tools")
    if stored is not None and not is_new_goal(text):
        return Route("query", command=command, plan=stored) if command else \
            Route("revise", plan=stored, instruction=text)
    if stored is None and command and command.kind != "team":
        return Route("reply", prompts.NO_PLAN)
    brief = parse_brief(text)
    if is_vague(brief) and asks_for_plan(text):
        return Route("pause", pending=need_goal(incoming))
    return Route("plan", brief=brief, replaced=stored is not None)


def need_goal(incoming: Incoming) -> hitl.Pending:
    return hitl.Pending(hitl.VAGUE, prompts_safe(incoming.text), incoming.wants_json)


def prompts_safe(text: str) -> str:
    return " ".join((text or "").split())[:300]


def plan_resume(answer: hitl.Answer) -> Route:
    if answer.action != "plan":
        return Route("reply", answer.reply or prompts.CANCELLED)
    return Route("plan", brief=parse_brief(answer.text), vague_used=True)
