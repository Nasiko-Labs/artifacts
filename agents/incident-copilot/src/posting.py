"""The post plan: which channel, which post tool, and (only when asked) which one person to tag, all fixed
in code from checked data, never chosen by the model. Then the approval question and reading the answer.

- Channel: the incident's own chat reference (verbatim from alerting output), else the channel the user
  named; resolved with the channel-lookup tool and accepted only on an exact name or id match.
- Person: the top suspect's author, only when their commit email was seen verbatim and the user lookup
  returns exactly one chat user with that email. The mention token is built here from that user's id.
- Typed text on the approval becomes a responder note at the top; mentions in it are stripped.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace

import loops
import prompts
from deps import TurnDeps
from discovery import Catalog
from jsonutil import first_key, parse_json, walk_dicts
from nasiko_hitl import question_metadata
from nasiko_mcp import McpTool

MENTION = "<@{id}>"
POST, SKIP, POST_NO_TAG = "Post", "Skip", "Post without tag"
_POST_WORDS = frozenset({"post", "yes", "y", "ok", "okay", "post it", "approve", "approved", "send", "go"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "stop", "don't post", "dont post", "denied", "deny",
                         "reject", "decline"})
_MENTION_TOKENS = re.compile(r"<[@!#][^>]*>|(?<![\w.])@[\w.-]+")
_ID_LIKE = re.compile(r"^[A-Za-z0-9_-]{2,80}$")
_POSTY = re.compile(r"post|message", re.IGNORECASE)
_NOT_POSTY = re.compile(r"schedul|update|delete|edit|remove", re.IGNORECASE)
EMAIL_KEYS = ("email", "email_address", "mail")
USER_NAME_KEYS = ("real_name", "display_name", "name", "login", "username")


@dataclass(frozen=True)
class PostPlan:
    card: str
    card_hash: str
    channel_id: str
    channel_name: str
    tool: str
    tag_id: str = ""
    tag_name: str = ""
    tag_email: str = ""
    tag_ref: str = ""
    no_tag_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> PostPlan:
        return PostPlan(**{f.name: str(data.get(f.name) or "") for f in fields(PostPlan)})

    @property
    def tag_label(self) -> str:
        return f"Post and tag {self.tag_name or self.tag_email}" if self.tag_id else ""


def choose_post_tool(writes: tuple[McpTool, ...]) -> McpTool | None:
    """The single write tool the chat-post query returned; with several, the first that says post/message
    and not schedule/update/delete."""
    if len(writes) == 1:
        return writes[0]
    return next((t for t in writes if _POSTY.search(f"{t.name} {t.description}")
                 and not _NOT_POSTY.search(t.name)), None)


def channel_matches(calls, hint: str) -> set[tuple[str, str]]:
    wanted = {hint.casefold(), hint.lstrip("#").casefold(), hint.rstrip("/").rsplit("/", 1)[-1].casefold()}
    found = set()
    for call in calls:
        for record in walk_dicts(parse_json(call.text)):
            ident, name = record.get("id"), first_key(record, ("name", "name_normalized"))
            if not isinstance(ident, str) or not isinstance(name, str):
                continue
            if ident.casefold() in wanted or name.casefold() in wanted:
                found.add((ident, name))
    return found


def user_matches(calls, email: str) -> set[tuple[str, str]]:
    found = set()
    for call in calls:
        for record in walk_dicts(parse_json(call.text)):
            ident = record.get("id")
            mail = first_key(record, EMAIL_KEYS) or first_key(record.get("profile") or {}, EMAIL_KEYS)
            if isinstance(ident, str) and isinstance(mail, str) and mail.casefold() == email.casefold():
                profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
                name = first_key(record, USER_NAME_KEYS) or first_key(profile, USER_NAME_KEYS) or email
                found.add((ident, str(name)))
    return found


async def resolve_channel(deps: TurnDeps, catalog: Catalog, hint: str, from_alerting: bool) -> tuple[str, str]:
    """(channel id, display name), or ("", "") when the hint cannot be matched exactly."""
    reader = loops.reader_for(deps, catalog)
    if reader.tools:
        await loops.run(deps, reader, prompts.find_channel_system(hint), f"Channel: {hint}", 1)
        matches = channel_matches(reader.calls, hint)
        if len(matches) == 1:
            ident, name = matches.pop()
            return ident, f"#{name.lstrip('#')}"
    if from_alerting and _ID_LIKE.match(hint):  # verbatim from the incident record
        return hint, hint
    return "", ""


async def resolve_person(deps: TurnDeps, catalog: Catalog, email: str) -> tuple[str, str]:
    """(chat user id, name) for exactly one user whose email equals the commit email, else ("", "")."""
    reader = loops.reader_for(deps, catalog)
    if not email or not reader.tools:
        return "", ""
    await loops.run(deps, reader, prompts.find_user_system(email), f"Email: {email}", 1)
    matches = user_matches(reader.calls, email)
    return matches.pop() if len(matches) == 1 else ("", "")


async def with_tag(deps: TurnDeps, catalog: Catalog, plan: PostPlan, suspect: Mapping | None) -> PostPlan:
    if not suspect:
        return replace(plan, no_tag_reason="there is no top suspect to tag")
    email = str(suspect.get("author_email") or "")
    if not email:
        return replace(plan, no_tag_reason="the top suspect's commit carries no author email")
    ident, name = await resolve_person(deps, catalog, email)
    if not ident:
        return replace(plan, no_tag_reason="no chat user with the commit email")
    return replace(plan, tag_id=ident, tag_name=name, tag_email=email, tag_ref=str(suspect.get("ref") or ""))


def clean_note(text: str) -> str:
    """Typed text never adds a mention: mention-style tokens are stripped."""
    return " ".join(_MENTION_TOKENS.sub("", text or "").split())


def body(plan: PostPlan, note: str = "", tag: bool = False) -> str:
    lines = []
    if note:
        lines.append(f"Responder note: {note}")
    if tag and plan.tag_id:
        lines.append(f"{MENTION.format(id=plan.tag_id)} (author of {plan.tag_ref})")
    return "\n".join([*lines, plan.card])


def question(plan: PostPlan) -> tuple[str, dict]:
    options = ([(plan.tag_label, f"Post and mention {plan.tag_email}."), (POST_NO_TAG, "Post, mention no one.")]
               if plan.tag_id else [(POST, "Post exactly this card.")])
    hitl = question_metadata(f"Post this card to {plan.channel_name}?"[:190],
                             [*options, (SKIP, "Don't post it.")], multi_select=False, allow_custom_input=True)
    lines = [f"Channel: {plan.channel_name} ({plan.channel_id})", f"Via: {plan.tool}"]
    if plan.tag_id:
        lines.append(f"Mentions: {plan.tag_name} ({plan.tag_email}), author of {plan.tag_ref} — only if you choose "
                     f"\"{plan.tag_label}\".")
    elif plan.no_tag_reason:
        lines.append(f"No one will be tagged: {plan.no_tag_reason}.")
    hint = "Choose an option, or type a note to add at the top of the card (it is posted without a tag)."
    return "\n".join([*lines, "", plan.card, "", hint]), hitl


def interpret(answer: str, plan: PostPlan) -> tuple[str, bool, str]:
    """(decision "post" | "skip", tag?, note). Fails closed: empty and refusals are skip; only the option
    naming the person tags them."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if plan.tag_id and word == plan.tag_label.casefold():
        return "post", True, ""
    if not word or word in _SKIP_WORDS:
        return "skip", False, ""
    if word in _POST_WORDS or word == POST_NO_TAG.casefold():
        return "post", False, ""
    note = clean_note(text)
    return ("post", False, note) if note else ("skip", False, "")
