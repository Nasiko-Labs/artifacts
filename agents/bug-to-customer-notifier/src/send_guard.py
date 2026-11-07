"""Code-enforced gate on every support-desk write (copied in from promise-tracker, extended here).

Ticket text is customer-written and untrusted, so a prompt is not a guarantee. A write runs only when it
is exactly what the user approved for ONE ticket:
- every ticket/id argument equals the approved ticket id (taken from the checked ticket, never the model);
- every body field equals the approved reply (case and whitespace ignored), and no other free text;
- a public/internal flag, if present, says public; no recipient, cc, follower, requester or author field;
- a status field, if present, equals the approved status (no approved status = no status field);
- one approval allows at most two writes, each once, in order: the reply, then (if approved) the status;
  one call carrying both is allowed when both match.
Pure: decisions only, no I/O."""

import re
from dataclasses import dataclass, replace

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "merge", "solve",
})
DESTRUCTIVE_VERBS = frozenset({"delete", "remove", "trash", "merge", "spam", "suspend", "archive", "destroy",
                               "redact", "forward", "import", "restore", "bulk", "permanently"})
BODY_KEYS = frozenset({"body", "text", "message", "content", "comment", "html", "html_body", "plain_body",
                       "plain_text", "markdown", "reply", "comment_body", "html_comment"})
STATUS_KEYS = frozenset({"status", "state", "ticket_status"})
PUBLIC_KEYS = frozenset({"public", "is_public"})
INTERNAL_KEYS = frozenset({"internal", "is_internal", "private", "is_private", "internal_note"})
FORBIDDEN_TOKENS = frozenset({"to", "cc", "ccs", "bcc", "recipient", "recipients", "email", "emails", "follower",
                              "followers", "collaborator", "collaborators", "requester", "submitter", "author",
                              "assignee", "group", "organization", "brand", "tags"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_TRUE, _FALSE = frozenset({"true", "1", "yes"}), frozenset({"false", "0", "no"})


@dataclass(frozen=True)
class Approval:
    """What the user approved for one ticket. `tools` are the write tools the card named."""

    target_id: str
    body: str
    status: str = ""          # "solved", or empty: no status change
    tools: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"target_id": self.target_id, "body": self.body, "status": self.status, "tools": list(self.tools)}

    @staticmethod
    def from_dict(data: object) -> "Approval | None":
        if not isinstance(data, dict) or not data.get("body") or not data.get("target_id"):
            return None
        return Approval(target_id=str(data["target_id"]), body=str(data["body"]), status=str(data.get("status") or ""),
                        tools=tuple(str(t) for t in data.get("tools") or ()))


@dataclass(frozen=True)
class Consumed:
    reply: bool = False
    status: bool = False


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""
    writes: frozenset = frozenset()   # {"reply", "status"} this call makes


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


def is_destructive_tool(tool_name: str) -> bool:
    return any(token in DESTRUCTIVE_VERBS for token in name_tokens(tool_name))


def consume(consumed: Consumed, writes: frozenset) -> Consumed:
    return replace(consumed, reply=consumed.reply or "reply" in writes, status=consumed.status or "status" in writes)


def check(tool_name: str, arguments: dict, approval: "Approval | None", consumed: Consumed | None = None) -> GuardDecision:
    if not is_write_tool(tool_name):
        return GuardDecision(allowed=True, is_write=False)
    if approval is None:
        return _refused(f"Refused: {tool_name} changes a support ticket and needs the user's approval first.")
    if is_destructive_tool(tool_name) or (approval.tools and tool_name not in approval.tools):
        return _refused(f"Refused: {tool_name} is not one of the approved tools for this reply.")
    consumed = consumed or Consumed()
    fields = _fields(arguments)
    problem = (_target_problem(fields, approval) or _forbidden_problem(fields) or _visibility_problem(fields)
               or _text_problem(fields, approval))
    if problem:
        return _refused(problem)
    writes = frozenset({*(["reply"] if _has(fields, BODY_KEYS) else []), *(["status"] if _has(fields, STATUS_KEYS) else [])})
    problem = _write_problem(fields, writes, approval, consumed)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True, writes=writes)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _target_problem(fields: list, approval: Approval) -> str | None:
    ids = [_text(v).lstrip("#") for k, v in fields if _is_id_key(k)]
    if not ids or any(i != approval.target_id for i in ids):
        return f"Refused: this call must name exactly ticket {approval.target_id} and no other."
    return None


def _forbidden_problem(fields: list) -> str | None:
    bad = sorted({k for k, v in fields if FORBIDDEN_TOKENS.intersection(name_tokens(k)) and _text(v)})
    return f"Refused: this call sets {', '.join(bad)}; add no recipients, copies, followers or owners." if bad else None


def _visibility_problem(fields: list) -> str | None:
    hidden = any(_text(v).casefold() not in _TRUE for k, v in fields if k in PUBLIC_KEYS)
    hidden = hidden or any(_text(v).casefold() not in _FALSE for k, v in fields if k in INTERNAL_KEYS)
    return "Refused: the approved reply is public; do not post it as an internal note." if hidden else None


def _text_problem(fields: list, approval: Approval) -> str | None:
    wanted = _normalize(approval.body)
    bodies = [_normalize(_text(v)) for k, v in fields if k in BODY_KEYS]
    if any(body != wanted for body in bodies):
        return "Refused: the reply text is not exactly the approved text. Use it verbatim, nothing added."
    stray = sorted({k or "?" for k, v in fields if k not in BODY_KEYS and isinstance(v, str)
                    and _WHITESPACE.search(v.strip()) and _normalize(v) != wanted})
    return f"Refused: this call carries text the user did not approve (in {', '.join(stray)})." if stray else None


def _write_problem(fields: list, writes: frozenset, approval: Approval, consumed: Consumed) -> str | None:
    if not writes:
        return "Refused: this call makes no approved change (a public reply, or the approved status)."
    statuses = {_text(v).casefold() for k, v in fields if k in STATUS_KEYS}
    if "status" in writes and (not approval.status or statuses != {approval.status.casefold()}):
        return f"Refused: the approved status change is {approval.status or 'none'}; set no other status."
    if ("reply" in writes and consumed.reply) or ("status" in writes and consumed.status):
        return "Refused: that approved change was already made; one approval allows each change once."
    if writes == {"status"} and not consumed.reply:
        return "Refused: post the approved reply first, then change the status."
    return None


def _is_id_key(key: str) -> bool:
    return key == "id" or "ticket" in name_tokens(key)


def _has(fields: list, keys: frozenset) -> bool:
    return any(k in keys for k, _ in fields)


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip() if value is not None else ""


def _fields(value: object, key: str = "") -> list:
    """Every scalar in the arguments with the (lower-cased) key it sits under, at any depth; list items
    inherit their list's key."""
    if isinstance(value, dict):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    if value is None:
        return []
    return [(key, value)]


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()
