"""Code-enforced gate on every chat write (adapted from the hardened kit; owned by this agent).

Issue text is untrusted, so a prompt is not a guarantee. A write runs only when it is exactly what the user
approved for ONE owner:
- the tool is the one the approval card named;
- every recipient field (channel, user, to, ...) equals the approved chat user id, taken from the checked
  owner record, never from the model; no channel, no second person;
- every body field equals the approved message (case and whitespace ignored), and no other free text;
- no broadcast mention (@here, @channel, @everyone) anywhere;
- one call makes exactly one write; one approval allows one write (the sender consumes it).
Pure: decisions only, no I/O."""

import re
from dataclasses import dataclass

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "unassign", "approve", "transfer", "notify", "merge", "comment", "label",
})
BODY_KEYS = frozenset({"body", "text", "message", "content", "markdown_text", "markdown", "mrkdwn", "html",
                       "plain_text", "message_body"})
RECIPIENT_KEY_TOKENS = frozenset({"to", "cc", "bcc", "recipient", "recipients", "channel", "channels", "user", "users",
                                  "email", "emails", "member", "members", "conversation"})
BROADCAST = re.compile(r"(?:^|[^\w])@(?:here|channel|everyone)\b|<!(?:here|channel|everyone)", re.IGNORECASE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Approval:
    """What the user approved: one direct message through one named tool to one chat user id."""

    owner: str            # the owner's name, for the card and notes
    recipient: str        # the chat user id
    draft: str
    tool: str = ""

    def to_dict(self) -> dict:
        return {"owner": self.owner, "recipient": self.recipient, "draft": self.draft, "tool": self.tool}

    @staticmethod
    def from_dict(data: object) -> "Approval | None":
        if not isinstance(data, dict) or not data.get("draft") or not data.get("recipient"):
            return None
        return Approval(owner=str(data.get("owner") or ""), recipient=str(data["recipient"]), draft=str(data["draft"]),
                        tool=str(data.get("tool") or ""))


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = set(name_tokens(tool_name))
    # Some chat tools post or open a conversation without a write verb in their name.
    return bool(tokens & WRITE_VERBS) or {"chat", "message"} <= tokens or (
        "open" in tokens and bool(tokens & {"dm", "im", "conversation", "conversations"}))


def check(tool_name: str, arguments: dict, approval: Approval | None) -> GuardDecision:
    if not is_write_tool(tool_name):
        return GuardDecision(allowed=True, is_write=False)
    if approval is None:
        return _refused(f"Refused: {tool_name} writes to chat and needs the user's approval first. Nothing was sent.")
    problem = _mismatch(tool_name, _fields(arguments), approval)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _mismatch(name: str, fields: list[tuple[str, str]], approval: Approval) -> str | None:
    if not approval.tool or name.casefold() != approval.tool.casefold():
        return f"Refused: the user approved sending with {approval.tool or 'no named tool'}, not {name}."
    if BROADCAST.search(approval.draft) or any(BROADCAST.search(v) for _, v in fields):
        return "Refused: broadcast mentions (@here, @channel, @everyone) are never sent."
    if not _body_matches(fields, approval.draft):
        return ("Refused: the text in this call is not the message the user approved. Use it verbatim, nothing "
                "added or removed.")
    named = {v.strip().casefold() for k, v in fields if _is_recipient_key(k) and v.strip()}
    if named != {approval.recipient.casefold()}:
        return (f"Refused: this message may go only to the approved chat user {approval.recipient}, as a direct "
                "message: no channel and no other recipient.")
    stray = sorted({k or "?" for k, v in fields if k not in BODY_KEYS and not _is_recipient_key(k)
                    and _WHITESPACE.search(v.strip()) and _normalize(v) != _normalize(approval.draft)})
    if stray:
        return f"Refused: this call carries text the user did not approve (in {', '.join(stray)})."
    return None


def _body_matches(fields: list[tuple[str, str]], draft: str) -> bool:
    wanted = _normalize(draft)
    if not wanted:
        return False
    bodies = [_normalize(v) for k, v in fields if k in BODY_KEYS]
    if bodies:
        return all(body == wanted for body in bodies)
    return any(_normalize(v) == wanted for _, v in fields)


def _is_recipient_key(key: str) -> bool:
    return bool(RECIPIENT_KEY_TOKENS.intersection(name_tokens(key)))


def _fields(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every scalar in the arguments (as text) with the lower-cased key it sits under, at any depth."""
    if isinstance(value, dict):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    if value is None or isinstance(value, bool):
        return []
    return [(key, str(value))]


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()
