"""Pure: this step's raw tool output, and readers that find things in it by shape.

`Corpus.contains(value)` is the verbatim check: a value the model cites must appear in the raw text
or in a decoded JSON string of it (so escaped quotes and unicode escapes still match). Case and runs
of whitespace are ignored; nothing else is.

The by-shape readers never trust the model: comments (to measure their length and find change links),
status transitions, and chat users with a given email are read straight from the tool output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_WS = re.compile(r"\s+")
_CREATED_KEYS = ("created", "created_at", "createdat", "creation_date", "timestamp", "date")
_BODY_KEYS = ("body", "text", "content", "comment", "renderedbody", "message")
_CHANGE_LINK = re.compile(r"/pull/\d+|/commit/[0-9a-f]{7,40}|/merge_requests/\d+", re.IGNORECASE)
_RICH_TEXT_META = frozenset({"type", "attrs", "marks", "version", "id", "localId"})
_USER_ID_KEYS = ("id", "user_id", "userid", "member_id", "account_id", "accountid")


def norm(text: str) -> str:
    return _WS.sub(" ", str(text)).strip().casefold()


def has_change_link(text: str) -> bool:
    return bool(_CHANGE_LINK.search(text or ""))


@dataclass(frozen=True)
class Record:
    """One tool call this step made: name, arguments, and the raw text it returned."""

    name: str
    arguments: Mapping[str, Any]
    body: str

    def mentions(self, value: str) -> bool:
        wanted = norm(value)
        return any(wanted == norm(v) or wanted in norm(v) for v in _strings(self.arguments))

    @property
    def parsed(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


class Corpus:
    """Everything the tools returned in one step, ready for verbatim checks."""

    def __init__(self, records: Sequence[Record]):
        self.records = tuple(records)
        decoded = [s for r in self.records for s in _strings(r.parsed)]
        self._text = norm("\n".join([*(r.body for r in self.records), *decoded]))

    def contains(self, value: object) -> bool:
        wanted = norm(value) if value is not None else ""
        return bool(wanted) and wanted in self._text

    @property
    def empty(self) -> bool:
        return not self.records


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _dicts(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for v in value.values():
            yield from _dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _dicts(v)


def _lower_keys(d: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k).casefold(): v for k, v in d.items()}


def flatten_text(value: Any) -> str:
    """Plain text of a comment body: a string, or the text leaves of a rich-text document."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if isinstance(value.get("text"), str):
            return value["text"]
        return " ".join(t for t in (flatten_text(v) for k, v in value.items() if k not in _RICH_TEXT_META) if t)
    if isinstance(value, list):
        return " ".join(t for t in (flatten_text(v) for v in value) if t)
    return ""


# --- comments --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Comment:
    created_at: str
    chars: int
    has_change_link: bool


def _as_comment(d: Mapping[str, Any]) -> Comment | None:
    keys = _lower_keys(d)
    created = next((keys[k] for k in _CREATED_KEYS if isinstance(keys.get(k), str) and keys[k]), None)
    body_value = next((keys[k] for k in _BODY_KEYS if keys.get(k)), None)
    if created is None or body_value is None:
        return None
    text = flatten_text(body_value).strip()
    return Comment(created, len(text), has_change_link(text)) if text else None


def comments_for(key: str, records: Iterable[Record], other_keys: Iterable[str]) -> tuple[Comment, ...]:
    """Comments of ticket `key`: every comment-shaped object in a call made for this key alone, plus
    those nested inside an issue object that names this key."""
    others = [k for k in other_keys if k != key]
    found: dict[tuple[str, int], Comment] = {}
    for record in records:
        parsed = record.parsed
        if record.mentions(key) and not any(record.mentions(o) for o in others):
            scopes: list[Any] = [parsed]
        else:
            scopes = [d for d in _dicts(parsed) if any(v == key for v in d.values() if isinstance(v, str))]
        for scope in scopes:
            for d in _dicts(scope):
                comment = _as_comment(d)
                if comment is not None:
                    found.setdefault((comment.created_at, comment.chars), comment)
    return tuple(found.values())


def change_links_for(key: str, records: Iterable[Record], other_keys: Iterable[str]) -> bool:
    """A code-host change link in a call made for this key alone."""
    others = [k for k in other_keys if k != key]
    return any(has_change_link(r.body) for r in records
               if r.mentions(key) and not any(r.mentions(o) for o in others))


# --- transitions -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Transition:
    id: str
    name: str
    to_status: str
    category_key: str  # the tool's own category key, or ""


def transitions_in(records: Iterable[Record]) -> tuple[Transition, ...]:
    found: dict[str, Transition] = {}
    for record in records:
        parsed = record.parsed
        for d in _dicts(parsed):
            lists = [v for k, v in d.items() if str(k).casefold() == "transitions" and isinstance(v, list)]
            for item in (i for lst in lists for i in lst if isinstance(i, Mapping)):
                transition = _as_transition(item)
                if transition is not None:
                    found.setdefault(transition.id, transition)
    return tuple(found.values())


def _as_transition(d: Mapping[str, Any]) -> Transition | None:
    tid, name = d.get("id"), d.get("name")
    if not isinstance(tid, (str, int)) or not isinstance(name, str) or not name:
        return None
    to = d.get("to") if isinstance(d.get("to"), Mapping) else {}
    to_name = to.get("name") if isinstance(to.get("name"), str) else name
    category = to.get("statusCategory") or to.get("status_category") or {}
    key = category.get("key") if isinstance(category, Mapping) else category
    return Transition(str(tid), name, to_name, str(key or ""))


# --- chat users ------------------------------------------------------------------------------

def users_with_email(email: str, records: Iterable[Record]) -> tuple[str, ...]:
    """Distinct user ids of objects that carry exactly this email (at any depth of that object)."""
    wanted = norm(email)
    ids: dict[str, None] = {}
    for record in records:
        for d in _dicts(record.parsed):
            keys = _lower_keys(d)
            uid = next((keys[k] for k in _USER_ID_KEYS if isinstance(keys.get(k), str) and keys[k]), None)
            if uid is None:
                continue
            if any(norm(s) == wanted for s in _strings(d)):
                ids.setdefault(uid, None)
    return tuple(ids)


def dm_channel_ids(body: str) -> tuple[str, ...]:
    """Ids a "open a direct message" call returned (`id` / `channel` / `channel_id` values)."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return ()
    found: dict[str, None] = {}
    for d in _dicts(parsed):
        for key, value in _lower_keys(d).items():
            if key in ("id", "channel", "channel_id", "conversation_id") and isinstance(value, str) and value:
                found.setdefault(value, None)
    return tuple(found)
