"""Code-enforced gate on every MCP tool that changes something.

The prompt tells the model to ask before sending, but a prompt is a request,
not a guarantee — and fetched emails/Slack messages/web pages are untrusted
text that may try to talk it into sending. So any tool whose name reads as a
write (send, post, create draft, delete, …) is refused here unless the user
approved it through `request_approval`, and only when the call is exactly what
they approved: the same tool, the same recipients, the same subject, and a
body EQUAL to the approved draft (an approved body plus one injected sentence
is a different message). One approval covers one write.

Case is ignored and whitespace collapsed before comparing: a case-only change
cannot add a sentence, a recipient or a tool, and models re-emitting text
sometimes re-capitalize it, so case-sensitivity would buy refusals, not safety.

Pure: decisions only, no I/O.
"""

import re
from dataclasses import dataclass

# Verb tokens that mark a tool as one that changes state. Matched against the
# tool name split into words (`GMAIL_CREATE_EMAIL_DRAFT`, `slack.sendMessage`,
# `c_1a2b__post_message`), so read tools (`fetch`, `list`, `search`, `get`) and
# words that merely contain a verb (`scheduled`, `settings`) never match.
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify",
})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_EMAIL = re.compile(r"[^\s<>,;\"']+@[^\s<>,;\"']+")
_RECIPIENT_SEPARATOR = re.compile(r"[,;]")

# Argument keys (exact, case-insensitive) that carry the message text, its
# recipients, and its subject across Gmail/Slack tool schemas (Composio and
# MCP servers alike). Every body field must equal the approved draft, so a
# second body field (html_body next to body) cannot smuggle in other text.
BODY_KEYS = frozenset({
    "body", "text", "message", "content", "markdown_text", "markdown", "mrkdwn",
    "html", "html_body", "plain_text", "message_body", "email_body",
})
# A key names recipients when any of its words is one of these
# (`to`, `cc_emails`, `to_addresses`, `channel_id`, `reply_to`, …). Not `user`/
# `user_id`: Gmail tools use `user_id: "me"` to pick the account.
RECIPIENT_KEY_TOKENS = frozenset({
    "to", "cc", "bcc", "recipient", "recipients", "channel", "channels", "email", "emails",
    "address", "addresses", "attendee", "attendees",
})
SUBJECT_KEYS = frozenset({"subject"})


@dataclass(frozen=True)
class Approval:
    """What the user approved: one action, through one named tool, to exactly
    these recipients, with exactly this subject and body. The tool is shown on
    the approval card, so the user approves *how* it is sent too (a Slack "/me"
    action and a normal message are both "send" tools). `target` is the
    human-readable line on the card; `recipient` is what the write must name."""

    action: str
    target: str
    draft: str
    tool: str = ""
    recipient: str = ""
    subject: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "target": self.target,
            "draft": self.draft,
            "tool": self.tool,
            "recipient": self.recipient,
            "subject": self.subject,
        }

    @staticmethod
    def from_dict(data: object) -> "Approval | None":
        if not isinstance(data, dict) or not data.get("draft"):
            return None
        return Approval(
            action=str(data.get("action", "")),
            target=str(data.get("target", "")),
            draft=str(data["draft"]),
            tool=str(data.get("tool") or ""),
            recipient=str(data.get("recipient") or ""),
            subject=str(data.get("subject") or ""),
        )


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    is_write: bool
    reason: str = ""


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """`c_1a2b__GMAIL_createDraft` → ("c", "1a2b", "gmail", "create", "draft")."""
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = name_tokens(tool_name)
    # `SLACK_CHAT_ME_MESSAGE` posts without a verb in its name.
    return any(token in WRITE_VERBS for token in tokens) or {"chat", "message"} <= set(tokens)


def check(tool_names: list[str], arguments: list[dict], approval: Approval | None) -> GuardDecision:
    """Decide whether this call (possibly several inner tools) may run.

    A write runs only when it is the single write in the call and matches the
    approval exactly: same tool, same recipients, same subject, and every body
    field equal to the approved draft (whitespace-normalized, case-insensitive).
    """
    writes = [(name, args) for name, args in zip(tool_names, arguments) if is_write_tool(name)]
    if not writes:
        return GuardDecision(allowed=True, is_write=False)
    names = ", ".join(name for name, _ in writes)
    if approval is None:
        return _refused(
            f"Refused: {names} changes the user's Slack/Gmail and needs their explicit approval "
            "first. Call `request_approval` with the exact final text."
        )
    if len(writes) > 1:
        return _refused(
            f"Refused: one approval covers one write, but this call makes {len(writes)} ({names}). "
            "Make the single approved write on its own."
        )
    name, args = writes[0]
    problem = _mismatch(name, args, approval)
    return _refused(problem) if problem else GuardDecision(allowed=True, is_write=True)


def _refused(reason: str) -> GuardDecision:
    return GuardDecision(allowed=False, is_write=True, reason=reason)


def _mismatch(name: str, args: object, approval: Approval) -> str | None:
    fields = _fields(args)
    if not _body_matches(fields, approval.draft):
        return (
            "Refused: the text in this call is not the draft the user approved — it must be "
            "exactly that text, nothing added or removed. Use the approved text verbatim, or call "
            "`request_approval` again with the new text."
        )
    if not approval.tool or name.lower() != approval.tool.lower():
        return (
            f"Refused: the user approved sending with {approval.tool or 'no named tool'}, not "
            f"{name}. Use the approved tool, or call `request_approval` again naming the tool you "
            "want to use."
        )
    approved_to = _recipients([approval.recipient])
    named_to = _recipients([v for k, v in fields if _is_recipient_key(k)])
    stray = {e.casefold() for k, v in fields if k not in BODY_KEYS for e in _EMAIL.findall(v)}
    if named_to != approved_to or not stray <= approved_to:
        return (
            f"Refused: the recipients in this call are not the recipient the user approved "
            f"({approval.recipient or 'none'}). Write to exactly that recipient — no extra to/cc/bcc "
            "— or call `request_approval` again naming the recipient."
        )
    subject = _normalize(approval.subject)
    # A call may leave the subject out (a reply inherits the thread's); it may not name another.
    if any(_normalize(v) not in ("", subject) for k, v in fields if k in SUBJECT_KEYS):
        return (
            f"Refused: the subject in this call is not the subject the user approved "
            f"({approval.subject or 'none'}). Use it verbatim, or call `request_approval` again."
        )
    unapproved = sorted({k or "?" for k, v in fields if _is_unapproved_text(k, v, approval)})
    if unapproved:
        return (
            f"Refused: this call carries text the user did not approve (in {', '.join(unapproved)}). "
            "Pass only the approved draft, recipient and subject, plus ids."
        )
    return None


def _body_matches(fields: list[tuple[str, str]], draft: str) -> bool:
    wanted = _normalize(draft)
    if not wanted:
        return False
    bodies = [_normalize(v) for k, v in fields if k in BODY_KEYS]
    if bodies:
        return all(body == wanted for body in bodies)
    # No recognised body field: the draft must still appear as one whole value.
    return any(_normalize(v) == wanted for _, v in fields)


def _is_unapproved_text(key: str, value: str, approval: Approval) -> bool:
    """Free text (anything with a space) outside the body/recipient/subject
    fields is only allowed when it IS the approved draft or subject; ids,
    flags and the like have no spaces."""
    if key in BODY_KEYS or key in SUBJECT_KEYS or _is_recipient_key(key):
        return False
    if not _WHITESPACE.search(value.strip()):
        return False
    return _normalize(value) not in (_normalize(approval.draft), _normalize(approval.subject))


def _is_recipient_key(key: str) -> bool:
    return bool(RECIPIENT_KEY_TOKENS.intersection(name_tokens(key)))


def _fields(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every string in the arguments with the (lower-cased) key it sits under,
    at any depth; list items inherit their list's key."""
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, dict):
        return [f for k, v in value.items() for f in _fields(v, str(k).lower())]
    if isinstance(value, list):
        return [f for v in value for f in _fields(v, key)]
    return []


def _recipients(values: list[str]) -> frozenset[str]:
    """Addresses/ids, case-folded; "Jordan Lee <j@x.com>, k@y.com" → {j@x.com, k@y.com}."""
    found: set[str] = set()
    for value in values:
        for part in _RECIPIENT_SEPARATOR.split(value):
            part = part.strip()
            if not part:
                continue
            emails = _EMAIL.findall(part)
            found.update(e.casefold() for e in emails) if emails else found.add(part.casefold())
    return frozenset(found)


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()
