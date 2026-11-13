"""Pure: building the drafting job for a new set or a revision, and folding the model's answer back into
the conversation's post set.

The grounding set is what the user gave: the request and its material, and for a revision every earlier
input plus the new instruction. Never the voice sample, never organization instructions, never the
model's own earlier words (a post only keeps tokens that were grounded)."""

from __future__ import annotations

import re
from dataclasses import replace

import inputs as i
from angles import Angle, angle_index, angles_for
from drafting import Job
from fence import facts_only
from models import PostSet, Variant
from voice import Voice

MAX_GROUND_CHARS = 60_000
REQUEST_MAX_CHARS = 1_500
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_PER_PLATFORM = {"linkedin": ("linkedin",), "x": ("x",), "x_thread": ("x_thread",), "both": ("linkedin", "x")}
REDO_INSTRUCTION = "Rewrite each variant in my voice as described below. Keep the same facts, angle and platform."


def new_job(text: str, material: str, facts: tuple[str, ...], request: i.Request, voice: Voice) -> Job:
    """The job for a new set."""
    platform = i.detect_platform(text) or "linkedin"
    goal = request.goal or i.detect_goal(text) or "announce"
    angles = angles_for(goal)
    platforms = tuple(p for p in _PER_PLATFORM[platform] for _ in angles)
    asked, pasted = split_request(text) if not facts else (text[:REQUEST_MAX_CHARS], "")
    job = Job(request=asked, material=pasted or material, facts=facts, org=request.org, platforms=platforms,
              angles=angles * len(_PER_PLATFORM[platform]), goal=goal, voice=voice,
              ground=cap_ground((facts_only(text), facts_only(material))))
    return job


def split_request(text: str) -> tuple[str, str]:
    """The first paragraph is the request; anything after a blank line is pasted material (data)."""
    parts = _PARAGRAPH_BREAK.split((text or "").strip(), maxsplit=1)
    if len(parts) == 1 or len(parts[0]) > REQUEST_MAX_CHARS:
        return (text or "").strip(), ""
    return parts[0].strip(), parts[1].strip()


def revision_targets(text: str, post_set: PostSet) -> tuple[int, ...]:
    count = len(post_set.variants)
    refs = tuple(n for n in i.variant_refs(text) if 1 <= n <= count)
    if not refs:
        by_angle = angle_index(text, tuple(v.angle for v in post_set.variants))
        refs = (by_angle,) if by_angle else ()
    return refs or tuple(range(1, count + 1))


def revision_job(text: str, post_set: PostSet, request: i.Request, voice: Voice,
                 targets: tuple[int, ...]) -> Job:
    wanted = i.detect_platform(text)
    current = tuple(post_set.variants[n - 1] for n in targets)
    platforms = tuple(wanted if wanted in ("linkedin", "x", "x_thread") else v.platform for v in current)
    instruction = REDO_INSTRUCTION if i.is_redo(text) else text
    return Job(request=instruction, material="", facts=(), org=request.org, platforms=platforms,
               angles=tuple(Angle(v.angle, "keep this angle") for v in current), goal=post_set.goal, voice=voice,
               ground=cap_ground((*post_set.ground, facts_only(text))), current=current)


def merged(post_set: PostSet, targets: tuple[int, ...], revised: tuple[Variant, ...], voice: Voice,
           ground: tuple[str, ...]) -> PostSet:
    variants = list(post_set.variants)
    for number, variant in zip(targets, revised, strict=False):
        variants[number - 1] = variant
    return replace(post_set, variants=tuple(variants), platform=platform_of(variants), voice=voice.to_dict(),
                   ground=ground)


def platform_of(variants) -> str:
    kinds = {v.platform for v in variants}
    if len(kinds) == 1:
        return next(iter(kinds))
    return "both" if "linkedin" in kinds else "x"


def cap_ground(texts) -> tuple[str, ...]:
    """Keep the newest inputs within MAX_GROUND_CHARS (the prompt stays small)."""
    kept: list[str] = []
    total = 0
    for text in reversed([t for t in texts if t]):
        if total + len(text) > MAX_GROUND_CHARS:
            kept.append(text[:MAX_GROUND_CHARS - total])
            break
        kept.append(text)
        total += len(text)
    return tuple(reversed([t for t in kept if t]))
