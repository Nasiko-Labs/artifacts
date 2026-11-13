"""Pure: the code checks on a set of variants, worded for the one repair call. Cleaning (markdown,
emoji, numbering, hashtags lifted out) happens first, so the checks see only the words the model wrote."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import rules
from grounding import Ground, ungrounded
from models import Variant
from voice import Voice

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML = re.compile(r"</?[A-Za-z][^>]*>|<!--.*?-->", re.DOTALL)
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
_EMPHASIS = re.compile(r"\*\*|__|```+|`")
_NUMBERING_RESERVE = 7          # "\n\n8/8" added by code to every thread post
_OPENING_CHARS = 40


@dataclass(frozen=True)
class Basis:
    ground: Ground       # everything the user gave for this set: request, material, later instructions
    tags: Ground         # where a hashtag word may come from: the user's words plus organization rules
    voice: Voice


def clean_text(text: str) -> str:
    out = _IMAGE.sub("", text or "")
    out = _HTML.sub("", out)
    out = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", out)
    out = _EMPHASIS.sub("", out)
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in out.split("\n")).strip()


def prepare(variant: Variant, voice: Voice) -> Variant:
    """Code clean-up before checking: markdown, emoji (when the voice has none), thread numbering, and
    trailing hashtags lifted into the variant's hashtag list."""
    posts, tags = [], list(variant.hashtags)
    for post in variant.posts:
        text = clean_text(post)
        if not voice.emoji:
            text = rules.strip_emoji(text)
        if variant.platform == "x_thread":
            text = rules.strip_numbering(text)
        body, lifted = rules.split_hashtags(text)
        posts.append(body)
        tags.extend(lifted)
    return replace(variant, posts=tuple(p for p in posts if p), hashtags=_unique(rules.tag_name(t) for t in tags))


def _unique(tags) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for tag in tags:
        if tag and tag.casefold() not in seen:
            seen[tag.casefold()] = tag
    return tuple(seen.values())


def problems(variants: Sequence[Variant], basis: Basis, expected: int) -> list[str]:
    found: list[str] = []
    if len(variants) < expected:
        found.append(f"return {expected} variants, each with its own angle; you returned {len(variants)}")
    for index, variant in enumerate(variants, start=1):
        found += [f"variant {index}: {p}" for p in variant_problems(variant, basis)]
    found += _same_openings(variants)
    if found:  # a nudge worth adding only when a repair is happening anyway
        found += [f"variant {i}: the hook has no concrete detail; open with a specific from the material"
                  for i, v in enumerate(variants, start=1) if not _concrete(v.hook)]
    return found


def variant_problems(variant: Variant, basis: Basis) -> list[str]:
    found: list[str] = []
    for post in variant.posts:
        found += [f"'{s.text}' is not in the user's material: write {s.placeholder} instead"
                  for s in ungrounded(post, basis.ground)]
        found += [f"'{m.group(0)}' is a claim the material does not make: remove it"
                  for m in rules.superlatives(post) if not basis.ground.has_word(rules.superlative_word(m))]
        found += [f"replace the phrase '{hit}'" for hit in rules.banned_hits(post)]
        if rules.exclamations(post) > 1:
            found.append("use at most one exclamation mark")
        if basis.voice.emoji and rules.emoji_count(post) > rules.MAX_EMOJI:
            found.append(f"use at most {rules.MAX_EMOJI} emoji")
    opener = rules.banned_opener(variant.posts[0]) if variant.posts else None
    if opener:
        found.append(f"do not open with '{opener}'")
    return found + _length_problems(variant)


def _length_problems(variant: Variant) -> list[str]:
    if variant.platform == "linkedin":
        size, hook = len(variant.posts[0]), len(variant.hook)
        found = [f"the post is {size} characters; the LinkedIn cap is {rules.LINKEDIN_HARD_CAP}"] \
            if size > rules.LINKEDIN_HARD_CAP else []
        if hook > rules.LINKEDIN_HOOK_MAX:
            found.append(f"the first line is {hook} characters; keep the hook under {rules.LINKEDIN_HOOK_MAX}")
        return found
    if variant.platform == "x":
        size = rules.x_length(variant.posts[0])
        return [f"the post is {size} weighted characters; X allows {rules.X_MAX_WEIGHTED}"] \
            if size > rules.X_MAX_WEIGHTED else []
    found = []
    if not rules.THREAD_MIN <= len(variant.posts) <= rules.THREAD_MAX:
        found.append(f"the thread has {len(variant.posts)} posts; write {rules.THREAD_MIN} to {rules.THREAD_MAX}")
    limit = rules.X_MAX_WEIGHTED - _NUMBERING_RESERVE
    found += [f"thread post {n} is {rules.x_length(p)} weighted characters; keep each under {limit}"
              for n, p in enumerate(variant.posts, start=1) if rules.x_length(p) > limit]
    return found


def _same_openings(variants: Sequence[Variant]) -> list[str]:
    seen: dict[tuple[str, str], int] = {}
    found = []
    for index, variant in enumerate(variants, start=1):
        key = (variant.platform, variant.hook.casefold()[:_OPENING_CHARS])
        if key in seen:
            found.append(f"variants {seen[key]} and {index} open the same way; each variant must take its own angle")
        else:
            seen[key] = index
    return found


def _concrete(hook: str) -> bool:
    return bool(re.search(r"\d|\?", hook)) or bool(re.search(r"(?<=\s)[A-Z]\w+", hook))
