"""Code-enforced gate on every tool call that changes something (copied in once from the MCP kit and
narrowed to this agent's one write: a reply in a chat thread; owned here).

A write runs only when the user approved that exact reply on the approval card, and only when the call
is exactly what they approved: the same tool, the same channel, the same thread, and a text EQUAL to the
approved text (an approved text plus one injected sentence is a different message). Nothing else with
free text may ride along. One approval covers one write. Case is ignored and whitespace collapsed.

Pure: decisions only, no I/O.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "kick", "join",
    "leave", "rename", "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share",
    "submit", "import", "cancel", "complete", "close", "assign", "approve", "transfer", "notify", "append",
    "clear",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

TEXT_KEYS = frozenset({"text", "markdown_text", "message", "body", "content", "mrkdwn", "markdown", "blocks",
                       "attachments"})
CHANNEL_TOKENS = frozenset({"channel", "conversation"})
THREAD_TOKENS = frozenset({"thread", "ts", "parent"})


@dataclass(frozen=True)
class Approval:
    """What the user approved: one reply, through one named tool, in exactly this channel and thread,
    with exactly this text."""

    action: str
    target: str
    draft: str
    tool: str
    channel: str
    thread: str

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(_normalize(self.draft).encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: object) -> Approval | None:
        if not isinstance(data, Mapping) or not data.get("draft") or not data.get("tool"):
            return None
        return Approval(**{k: str(data.get(k) or "") for k in ("action", "target", "draft", "tool", "channel",
                                                               "thread")})


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = name_tokens(tool_name)
    return any(t in WRITE_VERBS for t in tokens) or {"chat", "message"} <= set(tokens)


def check(tool_name: str, arguments: Mapping[str, object], approval: Approval | None) -> GuardDecision:
    if not is_write_tool(tool_name):
        return GuardDecision(allowed=True, is_write=False)
    if approval is None:
        return _refused(f"Refused: {tool_name} posts in chat and needs the user's approval first. Nothing was posted.")
    problem = _mismatch(tool_name, arguments, approval)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _mismatch(name: str, args: Mapping[str, object], approval: Approval) -> str | None:
    if name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved posting with {approval.tool}, not {name}."
    fields = _fields(args)
    bodies = [v for k, v in fields if k in TEXT_KEYS]
    if not bodies or any(_normalize(b) != _normalize(approval.draft) for b in bodies):
        return "Refused: the text is not the reply the user approved, word for word."
    channels = {v for k, v in fields if CHANNEL_TOKENS & set(name_tokens(k))}
    if channels != {approval.channel}:
        return f"Refused: the user approved a reply in channel {approval.channel}, not {sorted(channels)}."
    threads = {v for k, v in fields if THREAD_TOKENS & set(name_tokens(k)) and not CHANNEL_TOKENS & set(name_tokens(k))}
    if threads != {approval.thread}:
        return f"Refused: the user approved a reply in thread {approval.thread}, not {sorted(threads)}."
    extra = sorted(k for k, v in fields if k not in TEXT_KEYS and _WHITESPACE.search(v.strip()))
    if extra:
        return f"Refused: the call carries text the user did not approve (in {', '.join(extra)})."
    return None


def _fields(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every string (or number) in the arguments with the lower-cased key it sits under, at any depth."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (str, int, float)):
        return [(key, str(value))]
    if isinstance(value, Mapping):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    return []


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()
