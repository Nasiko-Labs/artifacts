"""Pure: assembling one compose job from the state recovered from the history and this turn's request.

The grounding set is what the user gave: the request and its context, and for a revision the user's
earlier words still in the history, the recovered draft (its specifics were checked when it was written)
and the new instruction. Never the tone sample, never organization instructions."""

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
    channel = detect_channel(asked) or (current.channel if current else None)
    earlier_ground = tuple(state.get("ground") or ())
    earlier_addressing = tuple(state.get("addressing") or ())
    return Job(
        request=asked, context=context, given=(), org=request.org, channel=channel,
        length=i.length_hint(said) or profile.length, profile=profile, current=current,
        ground_new=(said,), ground_revision=(*earlier_ground, said),
        addressing_new=(asked, *i.addressee_lines(context)),
        addressing_revision=(*earlier_addressing, asked),
        known=frozenset({"channel"}) if channel else frozenset(),
    )
