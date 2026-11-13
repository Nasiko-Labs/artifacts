"""Pure: a post variant and the model's JSON answer parsed into variants (or a status sentinel)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from grounding import placeholders
from rules import first_line, size

PLATFORMS = ("linkedin", "x", "x_thread")
STATUSES = ("ok", "needs_info", "off_topic", "private_message", "publish")
MAX_FACTS = 12
MAX_FACT_CHARS = 200
MAX_POSTS = 12
MAX_VARIANTS = 6


@dataclass(frozen=True)
class Fact:
    fact: str
    source: str        # the user's words it came from, verbatim

    def to_dict(self) -> dict:
        return {"fact": self.fact, "from": self.source}


@dataclass(frozen=True)
class Variant:
    platform: str
    angle: str
    posts: tuple[str, ...]
    hashtags: tuple[str, ...] = ()
    facts: tuple[Fact, ...] = ()
    flags: tuple[str, ...] = ()

    @property
    def hook(self) -> str:
        return first_line(self.posts[0]) if self.posts else ""

    @property
    def placeholders(self) -> list[str]:
        return placeholders("\n".join(self.posts))

    @property
    def chars(self) -> list[int]:
        return [size(self.platform, p) for p in self.posts]

    def to_dict(self) -> dict:
        return {"platform": self.platform, "angle": self.angle, "hook": self.hook, "posts": list(self.posts),
                "hashtags": list(self.hashtags), "placeholders": self.placeholders,
                "facts": [f.to_dict() for f in self.facts], "chars": self.chars, "flags": list(self.flags)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Variant:
        return Variant(platform=str(data.get("platform") or "linkedin"), angle=str(data.get("angle") or ""),
                       posts=tuple(str(p) for p in data.get("posts") or ()),
                       hashtags=tuple(str(h) for h in data.get("hashtags") or ()),
                       facts=_facts(data.get("facts")), flags=tuple(str(f) for f in data.get("flags") or ()))


@dataclass(frozen=True)
class Answer:
    status: str                       # ok | needs_info | off_topic | private_message | publish | invalid
    variants: tuple[Variant, ...] = ()
    goal: str | None = None


@dataclass(frozen=True)
class PostSet:
    """The current variants of this conversation, with what grounds them."""

    variants: tuple[Variant, ...]
    platform: str
    goal: str
    voice: dict = field(default_factory=dict)
    ground: tuple[str, ...] = ()
    coverage: str = ""

    def to_dict(self) -> dict:
        return {"variants": [v.to_dict() for v in self.variants], "platform": self.platform, "goal": self.goal,
                "voice": dict(self.voice), "ground": list(self.ground), "coverage": self.coverage}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> PostSet | None:
        if not data or not data.get("variants"):
            return None
        return PostSet(variants=tuple(Variant.from_dict(v) for v in data["variants"]),
                       platform=str(data.get("platform") or "linkedin"), goal=str(data.get("goal") or "announce"),
                       voice=dict(data.get("voice") or {}), ground=tuple(str(g) for g in data.get("ground") or ()),
                       coverage=str(data.get("coverage") or ""))


def parse_answer(data: Mapping[str, Any] | None, angles: Sequence[str], platforms: Sequence[str],
                 goals: Sequence[str]) -> Answer:
    """`platforms` is the platform of each expected variant in order; `angles` labels them by position
    (the model's own label is ignored, so the angle set stays the one code picked)."""
    if not isinstance(data, Mapping):
        return Answer("invalid")
    status = data.get("status")
    if status in STATUSES[1:]:
        return Answer(status)
    raw = data.get("variants")
    if status != "ok" or not isinstance(raw, list):
        return Answer("invalid")
    variants = []
    for index, item in enumerate(raw[:MAX_VARIANTS]):
        variant = _variant(item, angles[index % len(angles)] if angles else "",
                           platforms[index] if index < len(platforms) else platforms[-1])
        if variant is not None:
            variants.append(variant)
    if not variants:
        return Answer("invalid")
    goal = data.get("goal") if data.get("goal") in goals else None
    return Answer("ok", tuple(variants), goal)


def _variant(item: Any, angle: str, expected: str) -> Variant | None:
    if not isinstance(item, Mapping):
        return None
    posts = item.get("posts")
    if isinstance(posts, str):
        posts = [posts]
    if not isinstance(posts, list):
        return None
    clean = tuple(p.strip() for p in posts[:MAX_POSTS] if isinstance(p, str) and p.strip())
    if not clean:
        return None
    platform = "x_thread" if expected == "x" and len(clean) > 1 else expected
    if platform == "linkedin":
        clean = ("\n\n".join(clean),)       # a LinkedIn post is one post
    tags = item.get("hashtags") if isinstance(item.get("hashtags"), list) else []
    return Variant(platform=platform, angle=angle or str(item.get("angle") or ""), posts=clean,
                   hashtags=tuple(str(t) for t in tags if isinstance(t, str)), facts=_facts(item.get("facts_used")))


def _facts(value: Any) -> tuple[Fact, ...]:
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        if isinstance(item, Mapping):
            fact, source = str(item.get("fact") or "").strip(), str(item.get("from") or "").strip()
            if fact and source:
                out.append(Fact(fact[:MAX_FACT_CHARS], source[:MAX_FACT_CHARS]))
    return tuple(out[:MAX_FACTS])
