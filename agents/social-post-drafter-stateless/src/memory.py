"""Pure: what the platform's chat history still holds, read back into this agent's terms. This is the agent's
only memory: nothing is stored between messages.

- The current post set: parsed back from the newest reply that rendered variants (`render.reply`'s layout:
  a `**N. angle** · Platform (...)` line, then one ```text fence per post). A variant whose fence was cut off,
  or a gap in the numbering, means the reply was trimmed: no set. The facts list is not in the reply.
- Grounding for a revision: every earlier user message (without voice samples) plus the parsed variants,
  which were grounded when they were written.
- The voice: settings the user made lasting ("from now on ...") or sent on their own, replayed oldest first;
  the newest pasted voice sample (re-read by the draft step).
- The newest earlier message with material, for when the variants were trimmed but the material was not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import history
import inputs as i
from fence import facts_only
from models import PostSet, Variant
from rules import PLATFORM_LABELS
from voice import Voice, apply, settings_only, settings_patch

LASTING = ("always", "from now on", "by default", "going forward", "in general", "all my posts")
SET_MARK = "I can't post for you"

_LABEL_TO_PLATFORM = {label: key for key, label in PLATFORM_LABELS.items()}
_VARIANT = re.compile(r"^\*\*(\d+)\. (.*?)\*\* · (X thread|LinkedIn|X) \(")
_GOAL = re.compile(r"· goal: (\w+) ·")
_REVISION_CUE = re.compile(r"\b(?:shorter|longer|punchier|hook|tone|hashtags?|emojis?|thread|variant|version|option|"
                           r"redo|rewrite|reword|more|less|formal|casual|make (?:it|them|[1-9])|add|remove|drop|change|"
                           r"first|second|third|[1-9])\b", re.IGNORECASE)


@dataclass(frozen=True)
class Recalled:
    post_set: PostSet | None = None
    drafted_before: bool = False          # an earlier reply drafted posts (even if it is no longer readable)
    voice: Voice = field(default_factory=Voice)
    sample: str = ""
    material: str = ""


def recall(turns: tuple[history.Turn, ...]) -> Recalled:
    replies = [t.text for t in turns if t.role == history.ASSISTANT]
    drafted = [r for r in replies if SET_MARK in r]
    users = [t.text for t in turns if t.role == history.USER]
    post_set = parse_post_set(drafted[-1]) if drafted else None
    if post_set is not None:
        ground = (*(facts_only(i.split_sample(u).request) for u in users), *(p for v in post_set.variants
                                                                           for p in v.posts))
        post_set = PostSet(post_set.variants, post_set.platform, post_set.goal, {}, tuple(g for g in ground if g))
    samples = [s for s in (i.split_sample(u).sample for u in users) if s]
    return Recalled(post_set, bool(drafted), replay_voice(Voice(), users), samples[-1] if samples else "",
                    _material(users))


def replay_voice(base: Voice, users: list[str] | tuple[str, ...]) -> Voice:
    voice = base
    for text in users:
        patch = settings_patch(text)
        if patch and (any(w in text.casefold() for w in LASTING) or settings_only(text, patch)):
            voice = apply(voice, patch)
    return voice


def parse_post_set(reply: str) -> PostSet | None:
    variants: list[Variant] = []
    lines = (reply or "").splitlines()
    index = 0
    while index < len(lines):
        match = _VARIANT.match(lines[index])
        index += 1
        if not match:
            continue
        if int(match.group(1)) != len(variants) + 1:
            return None
        posts, index, closed = _fences(lines, index)
        if not posts or not closed:
            return None
        variants.append(Variant(_LABEL_TO_PLATFORM[match.group(3)], match.group(2), tuple(posts)))
    if not variants:
        return None
    goal = _GOAL.search(reply)
    kinds = {v.platform for v in variants}
    platform = next(iter(kinds)) if len(kinds) == 1 else ("both" if "linkedin" in kinds else "x")
    return PostSet(tuple(variants), platform, goal.group(1) if goal else "announce")


def _fences(lines: list[str], index: int) -> tuple[list[str], int, bool]:
    """The ```text posts after a variant line, up to the next variant line."""
    posts: list[str] = []
    while index < len(lines) and not _VARIANT.match(lines[index]):
        if lines[index].strip() != "```text":
            index += 1
            continue
        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip() != "```":
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            return posts, index, False
        posts.append("\n".join(body).strip())
        index += 1
    return posts, index, True


def _material(users: list[str]) -> str:
    for text in reversed(users):
        request = i.split_sample(text).request
        if i.has_material(request) and not i.is_revision(request, True):
            return request
    return ""


def looks_like_revision(text: str) -> bool:
    """A short change request about earlier variants ("punchier hook", "make 2 a thread"), not a new request."""
    return i.is_revision(text, True) and bool(_REVISION_CUE.search(text or ""))
