"""Pure: what code does after the one repair call. Made-up specifics become placeholders, unproven
superlatives become [claim?], hashtags are filtered and trimmed, thread numbering is added, flags are
listed (banned phrases are flagged, never silently rewritten) and factless variants are dropped."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

import rules
from checks import Basis
from grounding import Ground, replace_spans, ungrounded
from models import Variant

CLAIM = "[claim?]"


def finalize(variants: Sequence[Variant], basis: Basis,
             drop_factless: bool = True) -> tuple[tuple[Variant, ...], tuple[str, ...]]:
    fixed = [_finish(v, basis) for v in variants]
    kept = [v for v in fixed if v.facts]
    if not drop_factless or not kept or len(kept) == len(fixed):
        return tuple(fixed), ()
    dropped = [f"{v.angle or 'a variant'}" for v in fixed if not v.facts]
    note = f"Left out {len(dropped)} variant(s) ({', '.join(dropped)}) that used none of your facts."
    return tuple(kept), (note,)


def _finish(variant: Variant, basis: Basis) -> Variant:
    posts = tuple(_grounded(p, basis.ground) for p in variant.posts)
    limit = rules.hashtag_limit(variant.platform, basis.voice.hashtags)
    tags = tuple(t for t in variant.hashtags if allowed_tag(t, basis.tags))[:limit]
    posts = _assemble(variant.platform, posts, tags)
    facts = tuple(f for f in variant.facts if basis.ground.has_quote(f.source))
    finished = replace(variant, posts=posts, hashtags=tags, facts=facts)
    return replace(finished, flags=flags_for(finished))


def _grounded(post: str, ground: Ground) -> str:
    text = replace_spans(post, ungrounded(post, ground))
    for match in reversed(rules.superlatives(text)):
        if not ground.has_word(rules.superlative_word(match)):
            word = match.group("word") or match.group(0)
            start = match.start("word") if match.group("word") else match.start()
            text = text[:start] + CLAIM + text[start + len(word):]
    return text


def allowed_tag(tag: str, ground: Ground) -> bool:
    """A hashtag must be made of words the user (or an organization rule) used; a digit must be theirs too."""
    if not tag or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", tag):
        return False
    letters = re.sub(r"[^a-z0-9]", "", ground.text)
    if tag.casefold() in letters and all(d in ground.text for d in re.findall(r"\d+", tag)):
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", tag)
        return all(p.casefold() in ground.words or p.isdigit() or p.casefold() in ground.text for p in parts)
    return False


def _assemble(platform: str, posts: tuple[str, ...], tags: tuple[str, ...]) -> tuple[str, ...]:
    line = " ".join(f"#{t}" for t in tags)
    if platform == "linkedin":
        return (f"{posts[0]}\n\n{line}" if line else posts[0],)
    if platform == "x":
        joined = f"{posts[0]} {line}" if line else posts[0]
        return (joined if rules.x_length(joined) <= rules.X_MAX_WEIGHTED else posts[0],)
    total = len(posts)
    numbered = [f"{p}\n\n{n}/{total}" for n, p in enumerate(posts, start=1)]
    if line and rules.x_length(f"{posts[-1]} {line}\n\n{total}/{total}") <= rules.X_MAX_WEIGHTED:
        numbered[-1] = f"{posts[-1]} {line}\n\n{total}/{total}"
    return tuple(numbered)


def flags_for(variant: Variant) -> tuple[str, ...]:
    """What the user should look at, computed from the final text."""
    text = "\n".join(variant.posts)
    flags = [f"uses \"{hit}\"" for hit in rules.banned_hits(text)]
    if variant.posts and (opener := rules.banned_opener(variant.posts[0])):
        flags.append(f"opens with \"{opener}\"")
    if any(rules.exclamations(p) > 1 for p in variant.posts):
        flags.append("more than one exclamation mark")
    flags += _length_flags(variant)
    return tuple(flags)


def _length_flags(variant: Variant) -> list[str]:
    if variant.platform == "linkedin":
        size = len(variant.posts[0])
        out = [f"over the LinkedIn cap ({size:,} of {rules.LINKEDIN_HARD_CAP:,} characters)"] \
            if size > rules.LINKEDIN_HARD_CAP else []
        if len(variant.hook) > rules.LINKEDIN_HOOK_MAX:
            out.append(f"hook longer than {rules.LINKEDIN_HOOK_MAX} characters (may be cut at \"see more\")")
        return out
    over = [n for n, p in enumerate(variant.posts, start=1) if rules.x_length(p) > rules.X_MAX_WEIGHTED]
    out = [f"post {n} over {rules.X_MAX_WEIGHTED} weighted characters" for n in over]
    if variant.platform == "x_thread" and not rules.THREAD_MIN <= len(variant.posts) <= rules.THREAD_MAX:
        out.append(f"thread has {len(variant.posts)} posts (aim for {rules.THREAD_MIN}-{rules.THREAD_MAX})")
    return out


def is_over_length(variant: Variant) -> bool:
    return any(f.startswith(("over the LinkedIn cap", "post ", "thread has")) for f in variant.flags)
