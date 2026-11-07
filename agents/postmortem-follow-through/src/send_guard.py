"""Code-enforced gate on every tool call that changes something (copied from promise-tracker and
extended here with multi-write approvals and id binding).

A prompt is a request, not a guarantee, and ticket text and documents are untrusted text that may try
to talk the model into writing. So a write runs only when the user approved it on the approval pause,
and only when the call is exactly one of the writes they approved:

    Approval.writes = (WriteSpec(kind, tool, bound_ids, body | None), ...)

- the same tool;
- every bound id present among the arguments (the recipient, the ticket key, the transition id), and
  every value under a recipient/target-like key one of the approved ids;
- body None: no message text at all. Body given: every body field EQUAL to it (case and runs of
  whitespace ignored) and no other free text: an approved comment plus one sentence is refused;
- each approved write at most once.

Pure: decisions only, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# Verb tokens that mark a tool as one that changes state, matched against the tool name split into
# words, so read tools (`get`, `list`, `search`) and words that merely contain a verb never match.
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "transition", "reopen", "resolve",
})
_OPENS_CONVERSATION = frozenset({"dm", "im", "conversation", "conversations", "direct"})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

BODY_KEYS = frozenset({
    "body", "text", "message", "content", "markdown_text", "markdown", "mrkdwn",
    "html", "html_body", "plain_text", "message_body", "comment", "comment_body",
})
# A key names a target when any of its words is one of these (`channel`, `user_ids`, `issueIdOrKey`).
TARGET_KEY_TOKENS = frozenset({
    "to", "cc", "bcc", "recipient", "recipients", "channel", "channels", "email", "emails", "address",
    "addresses", "user", "users", "member", "members", "conversation", "issue", "issues", "ticket", "key",
    "keys", "transition",
})


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """`c_1a2b__chat_postMessage` → ("c", "1a2b", "chat", "post", "message")."""
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = set(name_tokens(tool_name))
    return (bool(tokens & WRITE_VERBS) or {"chat", "message"} <= tokens
            or ("open" in tokens and bool(tokens & _OPENS_CONVERSATION)))


@dataclass(frozen=True)
class WriteSpec:
    kind: str                     # "open_dm" | "message" | "transition" | "comment"
    tool: str
    bound_ids: tuple[str, ...]
    body: str | None = None
    required: bool = True         # False: allowed once but not needed (opening a DM first)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "tool": self.tool, "bound_ids": list(self.bound_ids), "body": self.body,
                "required": self.required}

    @staticmethod
    def from_dict(data: Mapping) -> WriteSpec:
        return WriteSpec(kind=str(data["kind"]), tool=str(data["tool"]),
                         bound_ids=tuple(str(i) for i in data.get("bound_ids") or ()),
                         body=None if data.get("body") is None else str(data["body"]),
                         required=bool(data.get("required", True)))


@dataclass(frozen=True)
class Approval:
    """What the user approved: these writes, nothing else."""

    action: str
    target: str
    writes: tuple[WriteSpec, ...]

    def to_dict(self) -> dict:
        return {"action": self.action, "target": self.target, "writes": [w.to_dict() for w in self.writes]}

    @staticmethod
    def from_dict(data: object) -> Approval | None:
        if not isinstance(data, Mapping) or not data.get("writes"):
            return None
        return Approval(action=str(data.get("action", "")), target=str(data.get("target", "")),
                        writes=tuple(WriteSpec.from_dict(w) for w in data["writes"]))


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    kind: str = ""
    reason: str = ""


def check(name: str, arguments: Mapping, approval: Approval | None, used: frozenset[str] = frozenset(),
          learned: Mapping[str, Sequence[str]] | None = None) -> GuardDecision:
    """May this one call run? `used` holds the kinds already made; `learned` maps a kind to ids that may
    stand in for its first bound id (the conversation a DM-open call returned)."""
    if approval is None:
        return _refused(f"Refused: {name} needs the user's explicit approval first. Nothing was changed.")
    specs = [w for w in approval.writes if w.tool.casefold() == name.casefold()]
    if not specs:
        return _refused(f"Refused: the user did not approve {name}. Use only the approved tools.")
    fresh = [w for w in specs if w.kind not in used]
    if not fresh:
        return _refused("Refused: that approved write was already made; each one is allowed once.")
    fields = _fields(arguments)
    problems = [_mismatch(spec, fields, tuple((learned or {}).get(spec.kind, ()))) for spec in fresh]
    for spec, problem in zip(fresh, problems, strict=True):
        if problem is None:
            return GuardDecision(True, spec.kind)
    return _refused(problems[0])


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(False, "", reason)


def _mismatch(spec: WriteSpec, fields: list[tuple[str, str]], learned: tuple[str, ...]) -> str | None:
    values = {_normalize(v) for _, v in fields}
    groups = [{spec.bound_ids[0], *learned}, *({b} for b in spec.bound_ids[1:])] if spec.bound_ids else []
    if any(not {_normalize(i) for i in group} & values for group in groups):
        return f"Refused: this call does not name the approved {_ids(spec, learned)}. Use exactly those ids."
    allowed = {_normalize(i) for i in (*spec.bound_ids, *learned)}
    stray = sorted({v for k, v in fields if _is_target_key(k) and _normalize(v) not in allowed})
    if stray:
        return (f"Refused: this call names {', '.join(stray)}, which the user did not approve. Use only "
                f"{_ids(spec, learned)}.")
    return _body_problem(spec, fields)


def _body_problem(spec: WriteSpec, fields: list[tuple[str, str]]) -> str | None:
    bodies = [v for k, v in fields if k in BODY_KEYS and v.strip()]
    free = [v for k, v in fields if k not in BODY_KEYS and _WHITESPACE.search(v.strip())]
    if spec.body is None:
        if bodies or free:
            return "Refused: this write was approved with no text at all. Pass only the approved ids."
        return None
    wanted = _normalize(spec.body)
    if bodies and any(_normalize(b) != wanted for b in bodies):
        return ("Refused: the text in this call is not the text the user approved: it must be exactly that "
                "text, nothing added or removed. Use the approved text verbatim.")
    if not bodies and not any(_normalize(v) == wanted for _, v in fields):
        return "Refused: this call does not carry the approved text."
    if any(_normalize(v) != wanted for v in free):
        return "Refused: this call carries text the user did not approve. Pass only the approved text and ids."
    return None


def _ids(spec: WriteSpec, learned: tuple[str, ...]) -> str:
    return "ids (" + ", ".join((*spec.bound_ids, *learned)) + ")"


def _is_target_key(key: str) -> bool:
    return bool(TARGET_KEY_TOKENS.intersection(name_tokens(key)))


def _fields(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every scalar in the arguments with the (lower-cased) key it sits under, at any depth; list
    items inherit their list's key. Numbers count as values (a transition id may be one)."""
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(key, str(value))]
    if isinstance(value, Mapping):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    return []


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", str(text)).strip().casefold()
