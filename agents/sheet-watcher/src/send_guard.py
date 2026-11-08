"""Code-enforced gate on every MCP tool that changes something (pattern copied from the hardened kit;
owned by this agent, rewritten for its one write: post one chat alert).

The prompt asks the model to make exactly the approved post, but a prompt is a request, not a guarantee,
and tool output is untrusted text. So any tool whose name reads as a write is refused here unless the user
approved that exact post on the approval card, and only when the call is exactly it: the same tool, the
same channel id (every channel-like argument), and every free-text argument equal to the approved body
(compared by the SHA-256 of its whitespace-normalised text). One approval covers one write.
Pure: decisions only, no I/O.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "append",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write", "batch",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "replace", "clear", "overwrite", "sends", "posts",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def body_hash(text: str) -> str:
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Approval:
    """What the user approved: one post, through one named tool, to one channel, with one body."""

    tool: str
    channel_id: str
    text: str

    @property
    def body_sha256(self) -> str:
        return body_hash(self.text)

    def to_dict(self) -> dict:
        return {"tool": self.tool, "channel_id": self.channel_id, "text": self.text, "sha256": self.body_sha256}

    @staticmethod
    def from_dict(data: object) -> Approval | None:
        if not isinstance(data, dict) or not data.get("tool") or not data.get("channel_id"):
            return None
        approval = Approval(str(data["tool"]), str(data["channel_id"]), str(data.get("text") or ""))
        return approval if data.get("sha256") in (None, approval.body_sha256) else None


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


def check(tool_names: Sequence[str], arguments: Sequence[dict], approval: Approval | None) -> GuardDecision:
    writes = [(n, a) for n, a in zip(tool_names, arguments) if is_write_tool(n)]
    if not writes:
        return GuardDecision(allowed=True, is_write=False)
    names = ", ".join(n for n, _ in writes)
    if approval is None:
        return _refused(f"Refused: {names} changes something on the user's behalf and needs their explicit approval "
                        "first. Nothing was posted.")
    if len(writes) > 1:
        return _refused(f"Refused: one approval covers one post, but this call makes {len(writes)} ({names}).")
    name, args = writes[0]
    problem = _mismatch(name, args, approval)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _mismatch(name: str, args: dict, approval: Approval) -> str | None:
    if name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved posting with {approval.tool}, not {name}. Use the approved tool."
    leaves = _leaves(args)
    wanted = _channel(approval.channel_id)
    channels = [str(v) for k, v in leaves if "channel" in name_tokens(k)]
    if not channels and not any(isinstance(v, str) and _channel(v) == wanted for _, v in leaves):
        return f"Refused: this call does not name the approved channel ({approval.channel_id})."
    if any(_channel(c) != wanted for c in channels):
        return f"Refused: this call posts somewhere other than the approved channel ({approval.channel_id})."
    texts = [v for k, v in leaves if isinstance(v, str) and _WHITESPACE.search(v.strip())
             and "channel" not in name_tokens(k)]
    if not texts:
        return "Refused: this call carries no message text. Pass the approved text verbatim."
    if any(body_hash(t) != approval.body_sha256 for t in texts):
        return "Refused: the text in this call is not the text the user approved. Use it verbatim."
    return None


def _channel(value: str) -> str:
    return str(value or "").strip().lstrip("#").casefold()


def _leaves(value: Any, key: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [leaf for k, v in value.items() for leaf in _leaves(v, str(k))]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _leaves(v, key)]
    return [(key, value)]


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()
