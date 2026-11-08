"""Pure data: the records, reading the model's JSON answers, and state (de)serialisation. What survives
the code checks is decided in checks.py; nothing here trusts a value."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, TypeVar

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
STATE_TYPES = ("triage", "backlog", "unstarted", "started", "completed", "canceled")
ISSUE_STATUSES = ("ok", "off_topic")
T = TypeVar("T")


@dataclass(frozen=True)
class Issue:
    id: str                       # the human key, e.g. PLAT-120
    url: str = ""
    title: str = ""
    state: str = ""
    state_type: str = ""          # one of STATE_TYPES, else "" (unknown)
    assignee_name: str = ""
    assignee_email: str = ""
    project: str | None = None    # None: no project (as the tool said)
    estimate: str | None = None   # None: no estimate
    priority: str = ""
    created_at: str = ""
    updated_at: str = ""
    team: str = ""


@dataclass(frozen=True)
class Finding:
    """One open issue that breaks at least one rule; every number here is computed in code."""

    issue: Issue
    rules: tuple[str, ...]        # H1..H4, in the order they are shown
    age_days: int | None
    days_since_update: int | None
    actions: tuple[str, ...]


@dataclass(frozen=True)
class ChatUser:
    email: str
    user_id: str
    name: str = ""


@dataclass(frozen=True)
class Owner:
    """An assignee with flagged issues. `user_id` is set only from a checked chat lookup."""

    key: str                      # casefolded email, or "name:<name>" when the tracker gave no email
    name: str
    email: str
    user_id: str = ""
    findings: tuple[Finding, ...] = ()

    @property
    def count(self) -> int:
        return len(self.findings)


@dataclass(frozen=True)
class IssueAnswer:
    status: str
    issues: tuple[Issue, ...]


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a model reply, code fences tolerated."""
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def _optional(value: Any) -> str | None:
    text = text_of(value)
    return text if text and text.casefold() not in ("null", "none") else None


def parse_issues(text: str) -> IssueAnswer | None:
    data = load_json_object(text)
    if data is None or not isinstance(data.get("issues", []), list):
        return None
    status = text_of(data.get("status")) or "ok"
    if status not in ISSUE_STATUSES:
        return None
    issues = tuple(i for i in (_issue(raw) for raw in data.get("issues") or ()) if i is not None)
    return IssueAnswer(status, issues)


def _issue(raw: Any) -> Issue | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    state_type = text_of(raw.get("state_type")).casefold()
    return Issue(id=text_of(raw.get("id")), url=text_of(raw.get("url")), title=text_of(raw.get("title")),
                 state=text_of(raw.get("state")), state_type=state_type if state_type in STATE_TYPES else "",
                 assignee_name=text_of(raw.get("assignee_name")), assignee_email=text_of(raw.get("assignee_email")),
                 project=_optional(raw.get("project")), estimate=_optional(raw.get("estimate")),
                 priority=text_of(raw.get("priority")), created_at=text_of(raw.get("created_at")),
                 updated_at=text_of(raw.get("updated_at")), team=text_of(raw.get("team")))


def parse_users(text: str) -> tuple[ChatUser, ...] | None:
    data = load_json_object(text)
    raw = data.get("users") if data else None
    if not isinstance(raw, list):
        return None
    users = (ChatUser(email=text_of(u.get("email")), user_id=text_of(u.get("user_id")), name=text_of(u.get("name")))
             for u in raw if isinstance(u, Mapping))
    return tuple(u for u in users if u.email and u.user_id)


def to_state(items: Sequence[Any]) -> list[dict]:
    return [asdict(item) for item in items]


def issue_from(raw: Mapping[str, Any]) -> Issue:
    names = {f.name for f in fields(Issue)}
    return Issue(**{k: v for k, v in raw.items() if k in names})


def finding_from(raw: Mapping[str, Any]) -> Finding:
    return Finding(issue=issue_from(raw.get("issue") or {}), rules=tuple(raw.get("rules") or ()),
                   age_days=_int_or_none(raw.get("age_days")), days_since_update=_int_or_none(raw.get("days_since_update")),
                   actions=tuple(raw.get("actions") or ()))


def owner_from(raw: Mapping[str, Any]) -> Owner:
    return Owner(key=str(raw.get("key") or ""), name=str(raw.get("name") or ""), email=str(raw.get("email") or ""),
                 user_id=str(raw.get("user_id") or ""),
                 findings=tuple(finding_from(f) for f in raw.get("findings") or ()))


def _int_or_none(value: Any) -> int | None:
    # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
    return None if value is None else int(value)
