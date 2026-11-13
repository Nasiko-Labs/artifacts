"""Pure: decide what one turn does, before any LLM call. Stateless: the plan to work from is the one the
user pasted, or the one recovered from the platform's history slice (recall.py); nothing is stored.

    greeting / small talk                         -> canned intro (no call)
    "put this in my tracker"                      -> fixed answer (no call)
    an envelope or a pasted plan                  -> load it as the plan (no call), or revise it
    a plan in the history ─┬─ a new goal          -> plan (replaces the old one)
                           ├─ a question / team   -> query (no call)
                           └─ anything else       -> revise (the model may still say off-topic)
    no plan ─┬─ a team/timebox change, goal in history -> plan that goal again with the change (rebuilt)
             ├─ a follow-up (question, "split T4") -> "paste the plan again" / "no plan yet" (no call)
             ├─ vague goal that asks for a plan    -> ask for the goal in one line, task completed (no call)
             └─ anything else                      -> plan (the model may say off-topic)
Nothing ever pauses: every route ends the task with a reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import envelope_in
import prompts
import queries
import recall
import smalltalk
from history import USER, Turn
from inputs import (
    MAX_GOAL_CHARS,
    Brief,
    Incoming,
    asks_for_plan,
    deadline,
    is_replan,
    is_vague,
    parse_brief,
    team_size,
)
from models import Plan

_NEW_GOAL = re.compile(r"^\s*(?:please\s+)?(?:break\s+(?:down|up)|plan\b|decompose|create (?:a |an )?(?:\w+ )?plan|"
                       r"make (?:a |an )?(?:\w+ ){0,2}plan|new (?:goal|plan)|here(?:'s| is) (?:our|the|my|a)\b|"
                       r"i need (?:a )?plan)", re.IGNORECASE)
_TASK_ID = re.compile(r"\bT\d+\b")
_PLAN_REF = re.compile(r"\b(?:the|this|that|my|your|our) plan\b|\bphase \d+\b", re.IGNORECASE)
# "make it a 2-week plan", "now cut this to 3 weeks": a change to a plan that came before, not a goal of its own
_REFERS_BACK = re.compile(r"^\s*(?:(?:ok(?:ay)?|now|then|please|and|so)[,\s]+)*(?:make|turn|change|redo|rework|"
                          r"re-?plan|fit|squeeze|adjust|shrink|cut|scale)\s+(?:it|this|that|them)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                           # reply | plan | revise | query | loaded
    reply: str = ""
    brief: Brief | None = None
    command: queries.Command | None = None
    plan: Plan | None = None            # for revise/query/loaded: the plan to work from
    instruction: str = ""
    replaced: bool = False
    rebuilt: bool = False               # the plan was not in the history: re-planned from the goal found there


def is_new_goal(text: str) -> bool:
    return len(text) > MAX_GOAL_CHARS or (bool(_NEW_GOAL.match(text)) and not _TASK_ID.search(text))


def is_followup(text: str, command: queries.Command | None) -> bool:
    """A message that only makes sense against an existing plan: a code-answered question (a team size alone
    may start a new plan), or an edit that names a task, a phase or the plan."""
    if is_new_goal(text):
        return False
    return bool((command and command.kind != "team") or _TASK_ID.search(text) or _PLAN_REF.search(text)
                or is_replan_change(text, command))


def is_replan_change(text: str, command: queries.Command | None) -> bool:
    """A team size or timebox applied to an earlier plan ("make it a 2-week plan for one engineer", "we are 2
    people"), as opposed to a new goal that happens to state one."""
    if is_new_goal(text) or not is_replan(text):
        return False
    return bool(_REFERS_BACK.match(text) or (command and command.kind == "team"))


def goal_in_history(turns: tuple[Turn, ...]) -> str | None:
    """The user's goal for the plan under discussion: the newest user turn that opens a plan, else the oldest
    user turn that reads as a goal (not small talk, not a follow-up, not too vague)."""
    users = [t.text for t in turns if t.role == USER and not t.compressed]
    newest = next((t for t in reversed(users) if is_new_goal(t)), None)
    if newest is not None:
        return newest
    return next((t for t in users if smalltalk.reply_to(t) is None and not is_followup(t, queries.parse(t))
                 and not is_vague(parse_brief(t))), None)


def rebuilt_brief(goal: str, change: str) -> Brief:
    """The goal's brief with the follow-up's team size and timebox applied."""
    base = parse_brief(goal)
    return replace(base, team_size=team_size(change) or base.team_size, deadline=deadline(change) or base.deadline)


def plan_turn(incoming: Incoming) -> Route:
    text = incoming.text
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", canned)
    env, rest = envelope_in.find(text)
    plan = envelope_in.to_plan(env, text) if env is not None else None
    if plan is None:
        plan, rest = recall.pasted(text)
    if plan is not None:
        asked = queries.parse(rest) if rest else None
        if asked is not None:
            return Route("query", command=asked, plan=plan)
        return Route("revise", plan=plan, instruction=rest) if rest else Route("loaded", plan=plan)
    command = queries.parse(text)
    if command and command.kind == "tracker":
        return Route("reply", prompts.TRACKER)
    recalled = recall.from_history(incoming.history)
    stored = recalled.plan
    if stored is not None and not is_new_goal(text):
        return Route("query", command=command, plan=stored) if command else \
            Route("revise", plan=stored, instruction=text)
    goal = goal_in_history(incoming.history) if stored is None and is_replan_change(text, command) else None
    if goal is not None:
        return Route("plan", brief=rebuilt_brief(goal, text), rebuilt=True)
    if stored is None and (is_followup(text, command) or (recalled.trimmed and command is not None)):
        return Route("reply", prompts.PLAN_NOT_IN_HISTORY if incoming.history else prompts.NO_PLAN)
    brief = parse_brief(text)
    if is_vague(brief) and asks_for_plan(text):
        return Route("reply", prompts.NEED_GOAL)
    return Route("plan", brief=brief, replaced=stored is not None)
