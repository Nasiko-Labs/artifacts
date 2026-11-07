"""Pure: assembling one compose job from the checkpointed state and this turn's request.

The grounding set is what the user gave: the request and its context, pause answers, and for a
revision every earlier fact plus the new instruction. Never the tone sample, never organization
instructions, never the model's own earlier words (a draft only keeps tokens that were grounded)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import inputs as i
from draft import Draft
from drafting import Job
from register import detect_channel
from tone import Profile


def build_job(state: Mapping[str, Any], text: str, instruction: str | None, request: i.Request) -> Job:
    current = Draft.from_dict(state.get("draft"))
    profile = Profile.from_dict(state.get("profile"))
    said = instruction or text
    asked, context = i.request_and_context(said)
    slots = dict(request.slots)
    given = tuple(f"{slot}: {value}" for slot, value in slots.items())
    answers = tuple(slots.values())
    channel = detect_channel(asked) or slots.get("channel") or (current.channel if current else None)
    earlier_ground = tuple(state.get("ground") or ())
    earlier_addressing = tuple(state.get("addressing") or ())
    return Job(
        request=asked, context=context, given=given, org=request.org, channel=channel,
        length=i.length_hint(said) or profile.length, profile=profile, current=current,
        ground_new=(said, *answers), ground_revision=(*earlier_ground, said, *answers),
        addressing_new=(asked, *answers, *i.addressee_lines(context)),
        addressing_revision=(*earlier_addressing, asked, *answers),
        known=frozenset(slots) | ({"channel"} if channel else frozenset()),
    )
