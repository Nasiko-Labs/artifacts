"""Code-enforced gate on every chat write (copied in from an earlier agent and adapted; owned here).

Tracker issues, comments and chat messages are untrusted text, so a prompt is not a guarantee. A write runs
only when it is exactly what the user approved on the post card:
- the tool is one of the approved chat post tools, never a destructive one;
- every channel field names the approved channel: its name (with or without `#`), or a channel id that a
  read in this turn returned next to that name;
- every body field equals the approved retro (case and whitespace ignored), no other free text, no mention;
- no user, email or recipient field (a retro is never a direct message);
- one approval allows one write, once.
Pure: decisions only, no I/O."""

import re
from dataclasses import dataclass

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "merge", "save", "keep", "store",
})
DESTRUCTIVE_VERBS = frozenset({"delete", "remove", "trash", "archive", "kick", "leave", "rename", "invite",
                               "destroy", "bulk", "merge", "import", "transfer"})
BODY_KEYS = frozenset({"body", "text", "message", "content", "markdown", "markdown_text", "mrkdwn", "html",
                       "plain_text", "message_body"})
CHANNEL_TOKENS = frozenset({"channel", "channels", "conversation", "room", "chat"})
FORBIDDEN_TOKENS = frozenset({"user", "users", "email", "emails", "to", "cc", "bcc", "recipient", "recipients",
                              "member", "members", "attendee", "attendees"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_MENTION = re.compile(r"<[!@][^>]*>|@")


@dataclass(frozen=True)
class Approval:
    """What the user approved: this body, to this channel (name without `#`), through these tools."""

    channel: str
    body: str
    tools: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"channel": self.channel, "body": self.body, "tools": list(self.tools)}

    @staticmethod
    def from_dict(data: object) -> "Approval | None":
        if not isinstance(data, dict) or not data.get("body") or not data.get("channel"):
            return None
        return Approval(channel=str(data["channel"]), body=str(data["body"]),
                        tools=tuple(str(t) for t in data.get("tools") or ()))


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
    return any(token in WRITE_VERBS for token in tokens) or {"chat", "message"} <= set(tokens)


def is_destructive_tool(tool_name: str) -> bool:
    return any(token in DESTRUCTIVE_VERBS for token in name_tokens(tool_name))


def strip_mentions(text: str) -> str:
    """No one is paged by a retro: `<@U1>`, `<!channel>` and bare `@` are removed in code."""
    return _MENTION.sub("", text)


def check(tool_name: str, arguments: dict, approval: "Approval | None", consumed: bool = False,
          seen_text: str = "") -> GuardDecision:
    if not is_write_tool(tool_name):
        return GuardDecision(allowed=True, is_write=False)
    if approval is None:
        return _refused(f"Refused: {tool_name} writes to chat and needs the user's approval first.")
    if is_destructive_tool(tool_name) or tool_name not in approval.tools:
        return _refused(f"Refused: {tool_name} is not the approved post tool.")
    if consumed:
        return _refused("Refused: the approved post was already made; one approval allows one post.")
    fields = _fields(arguments)
    problem = (_forbidden_problem(fields) or _channel_problem(fields, approval, seen_text)
               or _text_problem(fields, approval))
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _forbidden_problem(fields: list) -> str | None:
    bad = sorted({k for k, v in fields if FORBIDDEN_TOKENS.intersection(name_tokens(k)) and _text(v)})
    return f"Refused: this call sets {', '.join(bad)}; post to the channel only, to nobody else." if bad else None


def _channel_problem(fields: list, approval: Approval, seen_text: str) -> str | None:
    values = [_text(v) for k, v in fields if _is_channel_key(k)]
    if not values or not all(channel_matches(v, approval.channel, seen_text) for v in values):
        return f"Refused: this call must post to #{approval.channel} and no other channel."
    return None


def channel_matches(value: str, channel: str, seen_text: str) -> bool:
    """The approved channel by name, or an id a read in this turn returned next to that name."""
    wanted = channel.lstrip("#").casefold()
    if value.lstrip("#").casefold() == wanted:
        return True
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", value):
        return False
    name = re.compile(rf"(?<![a-z0-9_.-])#?{re.escape(wanted)}(?![a-z0-9_.-])")
    for match in re.finditer(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])", seen_text):
        if name.search(_record_around(seen_text, match.start(), match.end()).casefold()):
            return True
    return False


def _record_around(text: str, start: int, end: int) -> str:
    """The innermost `{...}` (else the line) holding text[start:end]: the id and the name must share a record."""
    opener = max(text.rfind("{", 0, start), text.rfind("\n", 0, start))
    closers = [i for i in (text.find("}", end), text.find("\n", end)) if i != -1]
    return text[opener + 1:min(closers) if closers else len(text)]


def _text_problem(fields: list, approval: Approval) -> str | None:
    wanted = _normalize(approval.body)
    bodies = [_normalize(_text(v)) for k, v in fields if k in BODY_KEYS]
    if not bodies or any(body != wanted for body in bodies):
        return "Refused: the message is not exactly the approved retro. Use it verbatim, nothing added."
    if any("@" in _text(v) for k, v in fields if k in BODY_KEYS):
        return "Refused: the message mentions someone; post it without mentions."
    stray = sorted({k or "?" for k, v in fields if k not in BODY_KEYS and isinstance(v, str)
                    and _WHITESPACE.search(v.strip()) and _normalize(v) != wanted})
    return f"Refused: this call carries text the user did not approve (in {', '.join(stray)})." if stray else None


def _is_channel_key(key: str) -> bool:
    return bool(CHANNEL_TOKENS.intersection(name_tokens(key)))


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip() if value is not None else ""


def _fields(value: object, key: str = "") -> list:
    """Every scalar in the arguments with the (lower-cased) key it sits under, at any depth."""
    if isinstance(value, dict):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    if value is None:
        return []
    return [(key, value)]


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()
