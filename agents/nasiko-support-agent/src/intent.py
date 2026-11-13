"""Pure: reading one user message. How-to vs troubleshoot, Nasiko scope, plan questions, "as json",
requests to do something for the user, and the history prefix direct chat adds."""

from __future__ import annotations

import re
from dataclasses import dataclass

HOWTO = "howto"
TROUBLESHOOT = "troubleshoot"

_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
_JSON_WORDS = re.compile(r"[,;:]?\s*\b(?:as json|json only|in json)\b[.!]?", re.IGNORECASE)
_TROUBLE = re.compile(
    r"\b(?:isn'?t|is not|not|won'?t|doesn'?t|does not|can'?t|cannot|didn'?t|never)\s+(?:\w+\s+)?"
    r"(?:work|working|start|starting|run|running|deploy|deploying|load|loading|respond|responding|reply|replying|"
    r"answer|answering|build|building|connect|connecting|show|showing)\b"
    r"|\b(?:error|errors|failed|fails|failing|failure|broken|crash|crashed|crashing|stuck|hangs?|timed? ?out|"
    r"timeout|exception|traceback|wrong|problem|issue|bug|502|500|503|401|403|409)\b"
    r"|\bwhat do i do\b|\bwhy (?:is|does|did|isn'?t|doesn'?t|won'?t|can'?t)\b",
    re.IGNORECASE,
)
_HOWTO_LEAD = re.compile(r"^\s*(?:how|where|what|which|can i|is there|do i|show me|explain)\b", re.IGNORECASE)
# Words that make a message about Nasiko. Without one, a message is out of scope.
_DOMAIN = re.compile(
    r"\b(?:nasiko|openruntime|agents?|deploy\w*|upload\w*|zip|dockerfile|agentcard|mcp|llm|router|routing|models?|"
    r"secrets?|api ?keys?|workflows?|sessions?|flows?|traces?|tokenops|budgets?|chats?|orchestrator|"
    r"conversations?|history|harness\w*|cli|"
    r"registry|connectors?|sidebar|dashboard|logs?|builds?|versions?|sso|login|log ?in|sign ?in|tools?|"
    r"approvals?|permissions?|access|users?|org(?:anization)? units?|settings|cost|spend|claude code|codex|"
    r"cursor|opencode|a2a|container|port|replicas?|restart|rollback|plan|plans|quota|limits?|workspace|"
    r"provider|openai|anthropic|slack|github|import|publish)\b",
    re.IGNORECASE,
)
# Features with their own troubleshooting notes. A problem report naming none of them is about an agent
# in general, and gets the clarifying question first.
_FEATURE = re.compile(
    r"\b(?:llm[- ]?router|routing config|llm[- ]?config|mcp|connectors?|tools?|workflows?|maf|sessions?|traces?|flows?|"
    r"tokenops|budgets?|costs?|secrets?|harness\w*|claude code|codex|cursor|opencode|registry|import|publish|"
    r"access|share|sharing|sso|single sign-on|approvals?|approve|org units?|users?|github)\b",
    re.IGNORECASE,
)
_PLANS = re.compile(
    r"\b(?:my|our|which|what|free|paid|pro|team|enterprise|trial|current|starter|business)\s+(?:plan|tier)\b"
    r"|\bplan limits?\b|\bsubscription\b|\bquotas?\b|\bupgrade (?:my|our|the) (?:plan|account|workspace|tier)\b"
    r"|\bhow many (?:agents|users|seats|workflows|deployments)\b|\b(?:pricing|price) (?:plan|tier)s?\b"
    r"|\bpricing\b.*\bplans?\b|\bplans?\b.*\b(?:include|limit|allow|cost)"
    # What Nasiko itself costs (not what an agent spends: that's TokenOps).
    r"|(?<!model )(?<!token )(?<!llm )\bpricing\b|\bhow much (?:does|is|will) (?:nasiko|openruntime)\b"
    r"|\b(?:cost|price) of (?:using )?(?:nasiko|openruntime)\b|\b(?:nasiko|openruntime)(?:'s)? (?:cost|price)s?\b"
    r"|\bhow much (?:does|do|will) (?:it|this) cost to use (?:nasiko|openruntime)\b",
    re.IGNORECASE,
)
# A request to write code: general coding, out of scope even when it names a Nasiko word ("logs").
_CODING = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:write|code|generate|implement)\s+(?:me\s+|us\s+)?"
    r"(?:a|an|some|the)\s+(?:[\w+#.-]+\s+){0,4}?(?:function|script|class|program|method|regex|snippet|module)\b",
    re.IGNORECASE,
)
# A developer asking how their agent's code reaches a platform feature (vs. where to click in the dashboard):
# "my code", "from/in my agent", "I've built an agent", "env vars", "can my agent call …".
_AGENT_VERBS = (r"(?:call|calls|reach|reaches|use|uses|read|reads|access|accesses|stream|streams|talk|talks|send|"
                r"sends|get|gets|emit|emits|pause|pauses|listen|listens|print|prints)")
_INTEGRATION = re.compile(
    r"\b(?:my|our|the|its|this)\s+(?:agent'?s\s+)?(?:own\s+)?code\b|\bagent\s+code\b"
    r"|\b(?:from|in|inside|within)\s+(?:my|our|the)\s+agent\b"
    r"|\b(?:i|we)\s*(?:have|'ve)?\s+(?:developed|built|written|wrote|created|made|coded)\s+(?:a|an|my|our|the)\s+"
    r"(?:[\w-]+\s+)?agents?\b"
    r"|\benv(?:ironment)?\s+var(?:iable)?s?\b|\b(?:sdk|programmatically|json-?rpc)\b"
    r"|\b(?:my|our|the)\s+agents?\s+(?:(?:can|could|should|will|to)\s+)?" + _AGENT_VERBS + r"\b"
    r"|\b(?:can|could|does|do|should|will)\s+(?:my|our|the)\s+agents?\s+(?:\w+\s+)?" + _AGENT_VERBS + r"\b"
    r"|\b(?:connect|hook|wire|integrate|point)\s+(?:up\s+)?(?:my|our|the)\s+agents?\s+(?:up\s+)?(?:to|with|into|at)\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:delete|remove|deploy|restart|stop|start|create|upload|rotate|"
    r"rollback|roll back|scale|enable|disable|connect|grant)\s+(?:my|the|this|our|it|that|a|an)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Message:
    text: str            # the user's words, "as json" removed
    wants_json: bool


def current_message(raw: str) -> str:
    """Direct chat sends "{history}\\n\\nCurrent message: {text}" once a session has turns."""
    idx = (raw or "").rfind(_CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(_CURRENT_MESSAGE_MARKER):]


def read(raw: str) -> Message:
    text = current_message(raw or "")
    wants_json = bool(_JSON_WORDS.search(text))
    return Message(_JSON_WORDS.sub("", text).strip() if wants_json else text.strip(), wants_json)


def mode(text: str) -> str:
    return TROUBLESHOOT if _TROUBLE.search(text or "") else HOWTO


def in_scope(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))


def names_feature(text: str) -> bool:
    return bool(_FEATURE.search(text or ""))


def asks_about_plans(text: str) -> bool:
    return bool(_PLANS.search(text or ""))


def asks_for_code(text: str) -> bool:
    return bool(_CODING.match(text or ""))


def asks_to_act(text: str) -> bool:
    return bool(_ACTION.match(text or ""))


def asks_about_integration(text: str) -> bool:
    return bool(_INTEGRATION.search(text or ""))


def looks_like_question(text: str) -> bool:
    return bool(_HOWTO_LEAD.match(text or "")) or (text or "").rstrip().endswith("?")
