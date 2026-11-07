"""The Slack draft: one model call for the intro and up to 3 highlight ids; code builds the post.

    *<product> <version> is out*
    <intro>
    - <validated sentence of each highlight>
    Full notes: <link>          (only when the header gave a link)

mrkdwn only, at most 900 characters (600 when asked for shorter), never an @channel / @here mention."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from banned import remove_banned
from grounding import free_text_problems
from inputs import Header
from llm import LlmError, TurnDeps, complete_json
from notes import Entry

log = logging.getLogger("release-notes.slack")

MAX_CHARS = 900
SHORT_MAX_CHARS = 600
MAX_HIGHLIGHTS = 3
SHORT_HIGHLIGHTS = 2
INTRO_MAX_WORDS = 25
FALLBACK_INTRO = "Here are the highlights of this release."
_MENTION = re.compile(r"<!(?:channel|here|everyone)[^>]*>|@\w+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class SlackParts:
    intro: str = FALLBACK_INTRO
    highlights: tuple[str, ...] = ()
    fallback: bool = True

    def to_dict(self) -> dict:
        return {"intro": self.intro, "highlights": list(self.highlights), "fallback": self.fallback}

    @staticmethod
    def from_dict(data: dict | None) -> SlackParts:
        data = data or {}
        return SlackParts(str(data.get("intro") or FALLBACK_INTRO), tuple(str(h) for h in data.get("highlights") or ()),
                          bool(data.get("fallback", True)))


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("intro"), str):
        return None
    highlights = data.get("highlights")
    return {"intro": data["intro"], "highlights": highlights if isinstance(highlights, list) else []}


def allowed_text(entries: Sequence[Entry], header: Header, policy: Sequence[str] = ()) -> str:
    return " ".join([*(e.change.sentence for e in entries), header.text(), *policy])


def checked(data: dict, entries: Sequence[Entry], header: Header) -> SlackParts:
    """The intro may use only what the notes and header say (no link: the post's link is code-built)."""
    intro = remove_banned(" ".join(str(data.get("intro") or "").split()))
    bad = free_text_problems(intro, allowed_text(entries, header), INTRO_MAX_WORDS)
    ids = {e.change.id for e in entries}
    picked = tuple(dict.fromkeys(str(h) for h in data.get("highlights") or () if str(h) in ids))[:MAX_HIGHLIGHTS]
    if bad:
        log.info("slack intro dropped: %s", bad)
    return SlackParts(FALLBACK_INTRO if bad else intro, picked, fallback=bool(bad))


async def ask(deps: TurnDeps, entries: Sequence[Entry], header: Header, policy: Sequence[str] = (),
              instruction: str = "") -> SlackParts:
    sentences = {e.change.id: e.change.sentence for e in entries}
    messages = prompts.slack_messages(sentences, header.product, header.audience, header.tone, instruction, policy)
    try:
        data = await complete_json(deps, messages, prompts.SLACK_CONTRACT, _validate)
    except LlmError as e:
        log.warning("slack call failed: %s", e)
        return SlackParts()
    return checked(data, entries, header)


def _mrkdwn(text: str) -> str:
    out = _MD_LINK.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", text or "")
    return _MENTION.sub("", out.replace("**", "*")).strip()


def build(parts: SlackParts, entries: Sequence[Entry], header: Header, short: bool = False) -> str:
    limit, count = (SHORT_MAX_CHARS, SHORT_HIGHLIGHTS) if short else (MAX_CHARS, MAX_HIGHLIGHTS)
    by_id = {e.change.id: e for e in entries}
    chosen = [by_id[h] for h in parts.highlights if h in by_id] or list(entries)
    title = f"*{header.product or '[product?]'} {header.version or '[version?]'} is out*"
    tail = [f"Full notes: {header.link}"] if header.link else []
    bullets = [f"- {_mrkdwn(e.change.sentence)}" for e in chosen[:count]]
    while True:
        post = "\n".join([title, _mrkdwn(parts.intro), *bullets, *tail])
        if len(post) <= limit or not bullets:
            break
        bullets = bullets[:-1]
    return post[:limit]
