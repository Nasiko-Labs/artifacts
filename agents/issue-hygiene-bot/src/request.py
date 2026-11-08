"""Pure: reading the user's request in code — team, project, rule subset, json mode, an input envelope,
and the requests this agent declines without a model call (edits to the tracker)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from checks import Scope
from rules import ALL_RULES

_NAME = r"(?:\"([^\"]{1,60})\"|'([^']{1,60})'|([A-Za-z0-9][\w.-]{0,40}))"
_TEAM = re.compile(rf"\bteam\s+{_NAME}", re.IGNORECASE)
_PROJECT = re.compile(rf"\bproject\s+{_NAME}", re.IGNORECASE)
_NOT_NAMES = frozenset({"the", "a", "an", "my", "our", "each", "every", "all", "and", "or", "to", "is", "are", "with",
                        "without", "no", "named", "called", "for"})
_RULE_WORDS = (
    ("H1", re.compile(r"\b(?:no|without|missing)\s+(?:a\s+)?project\b|\bunprojected\b", re.IGNORECASE)),
    ("H2", re.compile(r"\bestimat", re.IGNORECASE)),
    ("H3", re.compile(r"\bstale\b|\bin[ -]progress\b|\bstuck\b|\bnot updated\b", re.IGNORECASE)),
    ("H4", re.compile(r"\b90\+?\s*days?\b|\bopen (?:over|more than|for)\b|\bold(?:est)?\b|\bancient\b",
                      re.IGNORECASE)),
)
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_EDIT = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:close|reassign|assign|delete|archive|cancel|resolve|"
                   r"move|edit|update|label|set|change|comment on|unassign)\b", re.IGNORECASE)
# Words that make a message plainly about this job; anything else gets one tool-less scope check first,
# so an off-topic message is declined even when no tracker tool is connected.
DOMAIN = re.compile(r"hygiene|stale|issue|ticket|tracker|backlog|estimate|project|team|owner|assignee|in progress|"
                    r"open over|90 days|clean ?up|triage|message|\bdm\b|nudge|ping|remind|check", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    scope: Scope
    rules: tuple[str, ...] = ALL_RULES
    json_mode: bool = False
    from_envelope: bool = False


def parse(text: str) -> Request:
    envelope_ids = envelope_issue_ids(text)
    scope = Scope(team=_name(_TEAM, text), project=_name(_PROJECT, text), issue_ids=envelope_ids)
    wanted = tuple(rule for rule, pattern in _RULE_WORDS if pattern.search(text or ""))
    return Request(scope=scope, rules=wanted or ALL_RULES, json_mode=wants_json(text),
                   from_envelope=bool(envelope_ids))


def wants_json(text: str) -> bool:
    return bool(_JSON_MODE.search(text or ""))


def asks_for_edit(text: str) -> bool:
    return bool(_EDIT.search(text or ""))


def on_topic(text: str) -> bool:
    return bool(DOMAIN.search(text or "")) or bool(envelope_issue_ids(text))


def _name(pattern: re.Pattern, text: str) -> str:
    for match in pattern.finditer(text or ""):
        name = next(g for g in match.groups() if g is not None).strip().rstrip(".,;:!?")
        if name and name.casefold() not in _NOT_NAMES:
            return name
    return ""


def envelope_issue_ids(text: str) -> tuple[str, ...]:
    """An envelope from a previous step: `items[].issue_id` (this agent's own shape) is read; nothing else."""
    body = (text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return ()
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return ()
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ()
    ids = (str(i.get("issue_id")).strip() for i in items if isinstance(i, dict) and i.get("issue_id"))
    return tuple(dict.fromkeys(i for i in ids if i))
