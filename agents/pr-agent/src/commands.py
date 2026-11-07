"""Pure: read the user's turn into a PR reference, a command and whether to post the result.

Accepted forms (mixed freely with natural language):
  https://github.com/owner/repo/pull/123[/files...]   owner/repo#123   owner/repo/pull/123
  /describe  /review  /improve  /ask <question>       (default: /review)
"Post it" / "and comment on the PR" asks for posting; the post itself is confirmed by a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Command = Literal["describe", "review", "improve", "ask"]
COMMANDS: tuple[Command, ...] = ("describe", "review", "improve", "ask")
DEFAULT_COMMAND: Command = "review"

# The orchestrator prepends earlier turns as `{history}\n\nCurrent message: {query}`.
HISTORY_MARKER = "\n\nCurrent message: "

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})"
_PR_URL = re.compile(
    rf"https?://(?:www\.)?github\.com/(?P<owner>{_NAME})/(?P<repo>{_NAME})/pulls?/(?P<number>\d{{1,9}})", re.I
)
_PR_SHORT = re.compile(rf"(?<![\w/.-])(?P<owner>{_NAME})/(?P<repo>{_NAME})(?:#|/pulls?/)(?P<number>\d{{1,9}})\b")
_SLASH = re.compile(r"(?<![\w/])/(describe|review|improve|ask)\b", re.I)

_NATURAL: tuple[tuple[Command, re.Pattern[str]], ...] = (
    ("improve", re.compile(r"\b(improve|improvements?|code suggestions?|suggest(?:ions)?|refactor(?:ing)?s?)\b", re.I)),
    ("describe", re.compile(
        r"\b(describe|description|summari[sz]e|summary|walk ?through|what does (?:this|the) pr do)\b", re.I)),
    ("review", re.compile(r"\b(review|feedback|look over|check)\b", re.I)),
)
_POST = re.compile(
    r"\b(?:post|publish)\b[^.?!\n]{0,40}\b(?:it|this|that|comment|review|result|summary|description|"
    r"suggestions?|on (?:the|this|that) pr|to (?:the|this|that) pr|on github|to github)\b"
    r"|\b(?:leave|add|write)\s+(?:it|this|that|the result|(?:a|the|my|your)\s+(?:comment|review))\s+"
    r"(?:as a comment\s+)?(?:on|to)\s+(?:the|this|that)\s+(?:pr|pull request)\b",
    re.I,
)
_POST_ONLY = re.compile(r"^\s*(?:please\s+)?(?:go ahead and\s+)?(post|publish)\b.{0,60}$", re.I | re.S)


@dataclass(frozen=True)
class PrRef:
    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    def to_state(self) -> dict:
        return {"owner": self.owner, "repo": self.repo, "number": self.number}

    @staticmethod
    def from_state(raw: dict | None) -> PrRef | None:
        if not raw:
            return None
        return PrRef(str(raw["owner"]), str(raw["repo"]), int(raw["number"]))


@dataclass(frozen=True)
class Request:
    pr: PrRef | None
    command: Command
    question: str
    wants_post: bool
    explicit_command: bool
    post_only: bool  # "post it": no new analysis asked for, only posting the last result


def current_message(text: str) -> str:
    """The user's own turn when the orchestrator glued history in front of it."""
    idx = text.rfind(HISTORY_MARKER)
    return text if idx == -1 else text[idx + len(HISTORY_MARKER):]


def parse_pr_ref(text: str) -> PrRef | None:
    for pattern in (_PR_URL, _PR_SHORT):
        match = pattern.search(text or "")
        if match:
            repo = match.group("repo")
            repo = repo[:-4] if repo.lower().endswith(".git") else repo
            return PrRef(match.group("owner"), repo, int(match.group("number")))
    return None


def parse_request(text: str) -> Request:
    message = current_message(text or "").strip()
    pr = parse_pr_ref(message)
    command, question, explicit = _command(message)
    # A question about posting ("/ask should I post a changelog?") is not a request to post.
    scope = message.replace(question, " ") if command == "ask" and question else message
    wants_post = bool(_POST.search(_without_refs(scope)))
    post_only = wants_post and not explicit and pr is None and bool(_POST_ONLY.match(message))
    return Request(pr=pr, command=command, question=question, wants_post=wants_post,
                   explicit_command=explicit, post_only=post_only)


def _command(message: str) -> tuple[Command, str, bool]:
    slash = _SLASH.search(message)
    if slash:
        command: Command = slash.group(1).lower()  # type: ignore[assignment]
        rest = _without_refs(message[slash.end():]).strip(" :-\n\t")
        if command == "ask":
            return "ask", rest, True
        return command, "", True
    stripped = _without_refs(message)
    for command, pattern in _NATURAL:
        if pattern.search(stripped):
            return command, "", False
    if "?" in stripped and _looks_like_question(stripped):
        return "ask", stripped.strip(), False
    return DEFAULT_COMMAND, "", False


def _looks_like_question(text: str) -> bool:
    return bool(re.search(r"\b(why|what|how|does|is|are|can|should|where|which|who|when)\b", text, re.I))


def _without_refs(text: str) -> str:
    return _PR_SHORT.sub(" ", _PR_URL.sub(" ", text))


_DECLINE = re.compile(r"\b(don'?t|do not|no|nope|not|cancel|skip|stop|never)\b", re.I)
_CONFIRM = re.compile(r"^\s*(post|yes|yep|yeah|sure|ok(?:ay)?|confirm(?:ed)?|approve[d]?|go ahead|do it|publish)\b",
                      re.I)


def is_post_confirmation(answer: str) -> bool:
    """The human's answer to "Post this to GitHub?". Anything unclear is a no: nothing is posted."""
    text = current_message(answer or "").strip()
    if not text or _DECLINE.search(text):
        return False
    return bool(_CONFIRM.match(text))
