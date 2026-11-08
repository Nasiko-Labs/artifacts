"""Pure: the reply text. Each variant goes in a ```text fence so Copy gives clean post text; everything
around it is rendered by code."""

from __future__ import annotations

from collections.abc import Sequence

from models import PostSet, Variant
from rules import PLATFORM_LABELS, X_MAX_WEIGHTED
from voice import Voice

_VOICE_SOURCE = {"sample": "from your sample", "settings": "from your settings", "default": "default"}
FOOTER = ("_Drafts only. I can't post for you: copy a variant and post it yourself. Say \"punchier hook\", "
          "\"make 2 a thread\", or \"no hashtags\"._")
DEFAULT_VOICE_NOTE = "Using a neutral voice; paste a past post (\"here's a past post: ...\") to match your style."


def reply(post_set: PostSet, summary: str, notes: Sequence[str] = (), revised: Sequence[int] = ()) -> str:
    voice = Voice.from_dict(post_set.voice)
    labels = sorted({PLATFORM_LABELS[v.platform] for v in post_set.variants})
    count = len(post_set.variants)
    header = (f"**{' + '.join(labels)} · {count} variant{'s' if count != 1 else ''}** · goal: {post_set.goal} · "
              f"voice: {voice.summary()} ({_VOICE_SOURCE.get(voice.source, 'default')})")
    lines = [header, ""]
    for number, variant in enumerate(post_set.variants, start=1):
        lines += _variant(number, variant, number in revised)
    lines += _tail(post_set, summary, notes, voice)
    return "\n".join(lines).rstrip()


def _variant(number: int, variant: Variant, revised: bool) -> list[str]:
    mark = " · revised" if revised else ""
    label = PLATFORM_LABELS[variant.platform]
    if variant.platform == "linkedin":
        size = f"{variant.chars[0]:,} chars"
    elif variant.platform == "x":
        size = f"{variant.chars[0]} of {X_MAX_WEIGHTED} weighted chars"
    else:
        size = f"{len(variant.posts)} posts, " + ", ".join(str(c) for c in variant.chars) + " weighted chars"
    out = [f"**{number}. {variant.angle or 'Variant'}** · {label} ({size}){mark}"]
    for post in variant.posts:
        out += ["```text", post.replace("```", "'''"), "```"]
    if variant.flags:
        out.append("Flags: " + "; ".join(variant.flags))
    out.append("")
    return out


def _tail(post_set: PostSet, summary: str, notes: Sequence[str], voice: Voice) -> list[str]:
    holes = list(dict.fromkeys(p for v in post_set.variants for p in v.placeholders))
    tail = [summary]
    if holes:
        tail.append("Fill in before posting: " + " · ".join(holes))
    if post_set.coverage:
        tail.append(post_set.coverage)
    tail += list(notes)
    if voice.source == "default":
        tail.append(DEFAULT_VOICE_NOTE)
    if any(v.platform != "linkedin" for v in post_set.variants):
        tail.append("X lengths are weighted (a link counts 23, an emoji 2); check X's current limit before posting.")
    tail.append(FOOTER)
    return tail


def facts_reply(post_set: PostSet | None) -> str:
    if post_set is None:
        return "I haven't drafted any posts in this conversation yet."
    lines = []
    for number, variant in enumerate(post_set.variants, start=1):
        for fact in variant.facts:
            lines.append(f"- {number}. {variant.angle}: {fact.fact} (from: \"{fact.source}\")")
    if not lines:
        return "These variants use no specific facts from what you gave me."
    return "Facts I used from what you gave me:\n" + "\n".join(lines)


def voice_saved(voice: Voice, has_set: bool, lead: str = "Saved your voice") -> str:
    parts = [f"{lead}: {voice.summary()}."]
    if voice.openers:
        parts.append("Openers: " + "; ".join(voice.openers) + ".")
    parts.append("Say \"redo\" to apply it to the current variants." if has_set
                 else "Now tell me what the post is about and where it goes (LinkedIn or X).")
    return " ".join(parts)
