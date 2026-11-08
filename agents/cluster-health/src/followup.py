"""Follow-ups on the stored report, answered in code with no model call: `only <namespace>`, `only nodes`,
`explain <object>`, `show rollouts`, `what should I paste next`, `as json`, and change requests (refused,
evidence re-shown). Anything else costs one scope call: off-topic → the fixed decline; in scope → the
report again with the list of supported follow-ups."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import llm
import prompts
from inputs import Incoming
from llm import TurnDeps
from outcome import TurnResult, finish, paste_next, reply

_CHANGE = re.compile(
    r"(?:^|[.!?;,]\s*|\b(?:please|pls|can you|could you|would you|will you|go ahead and|just|now|then|and)\s+)"
    r"(?:restart|reboot|delete|remove|kill|scale|cordon|uncordon|drain|evict|roll ?back|rollback|undo|apply|"
    r"patch|edit|exec|redeploy|bounce|recreate|terminate|taint|fix|set image)\b", re.IGNORECASE)
_NEXT = re.compile(r"\bwhat (?:should|do|can) i (?:paste|run|check|send)\b|\bwhat next\b|\bpaste next\b|"
                   r"\bwhat else\b", re.IGNORECASE)
_NODES = re.compile(r"\b(?:only|just|show)\s+(?:the\s+)?nodes?\b|\bnodes? only\b", re.IGNORECASE)
_ROLLOUTS = re.compile(r"\b(?:recent\s+)?rollouts?\b", re.IGNORECASE)
_EXPLAIN = re.compile(r"\b(?:explain|why|what(?:'s| is) wrong with|details? (?:on|for|about))\b", re.IGNORECASE)
_ONLY = re.compile(r"\b(?:only|just)\s+(?:namespace\s+|ns\s+|the\s+)?([a-z0-9][-a-z0-9]*)\b|"
                   r"\bnamespace\s+([a-z0-9][-a-z0-9]*)\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9][-a-z0-9.]*", re.IGNORECASE)
NODE_KINDS = frozenset({"node_not_ready", "node_pressure"})
ROLLOUT_KINDS = frozenset({"rollout_unhealthy", "recent_rollout"})
_KIND_WORDS = {"crashloop": ("crash_loop", "oom_killed"), "crash": ("crash_loop", "oom_killed"),
               "oom": ("oom_killed",), "oomkilled": ("oom_killed",), "pending": ("pending_unschedulable",),
               "image": ("image_pull",), "pull": ("image_pull",), "restarts": ("restart_storm",),
               "evicted": ("evicted_or_failed",), "pressure": ("node_pressure", "resource_pressure")}


def wants_change(text: str) -> bool:
    return bool(_CHANGE.search(text or ""))


def wants_next(text: str) -> bool:
    return bool(_NEXT.search(text or ""))


async def answer(incoming: Incoming, deps: TurnDeps, stored: Mapping[str, Any], text: str) -> TurnResult:
    result = stored["result"]
    issues = result["issues"]
    if wants_change(text):
        return finish(result, incoming, lead=prompts.READ_ONLY)
    if wants_next(text):
        return reply(paste_next(result))
    if incoming.wants_json and len(text.split()) <= 4:
        return finish(result, incoming)
    if _NODES.search(text):
        shown = [i for i in issues if i["kind"] in NODE_KINDS or i["object_type"] == "node"]
        return finish(result, incoming, issues=shown, lead="Only node issues:")
    if _ROLLOUTS.search(text):
        shown = [i for i in issues if i["kind"] in ROLLOUT_KINDS]
        return finish(result, incoming, issues=shown, lead="Only rollouts:")
    if _EXPLAIN.search(text):
        match = best_match(issues, text)
        if match is not None:
            return finish(result, incoming, issues=[match], full=True, lead="In detail:")
        return finish(result, incoming, lead="Nothing flagged matches that; here is everything I flagged:")
    namespace = only_namespace(text, issues)
    if namespace is not None:
        shown = [i for i in issues if i["namespace"] == namespace]
        return finish(result, incoming, issues=shown, lead=f"Only namespace {namespace}:")
    status = await classify(deps, incoming, has_result=True)
    if status == "off_topic":
        return reply(prompts.DECLINE)
    return finish(result, incoming, notes=(prompts.FOLLOW_UPS,))


def only_namespace(text: str, issues: list[dict]) -> str | None:
    known = {i["namespace"] for i in issues if i["namespace"]}
    for match in _ONLY.finditer(text or ""):
        name = (match.group(1) or match.group(2) or "").casefold()
        if name in known:
            return name
    return None


def best_match(issues: list[dict], text: str) -> dict | None:
    words = {w.casefold() for w in _WORD.findall(text or "")}
    kinds = {k for w in words for k in _KIND_WORDS.get(w, ())}
    scored = []
    for issue in issues:
        names = {issue["object"].casefold(), issue["namespace"].casefold(), *map(str.casefold, issue["examples"])}
        score = 2 * len(words & names) + (1 if issue["kind"] in kinds else 0)
        if score:
            scored.append((score, -len(scored), issue))
    return max(scored, key=lambda s: (s[0], s[1]))[2] if scored else None


def parse_scope(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") in (prompts.OFF_TOPIC, "in_scope"):
        return {"status": data["status"]}
    return None


async def classify(deps: TurnDeps, incoming: Incoming, has_result: bool) -> str | None:
    """off_topic | in_scope, or None when no model call could be made."""
    if not deps.traceparent:
        return None
    try:
        parsed = await llm.complete_json(deps, prompts.scope_messages(incoming.text, has_result, incoming.context),
                                         '{"status": "in_scope"} or {"status": "off_topic"}', parse_scope)
    except llm.LlmError:
        return None
    return parsed["status"]
