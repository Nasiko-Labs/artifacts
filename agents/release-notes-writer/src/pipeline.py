"""The model steps of one release: rewrite+theme (chunked), then the Slack and email calls.

Budget per turn: rewrite 1 (+ up to 7 more chunks) + Slack 1 + email 1 + one repair; 240 s deadline. A step
that cannot run (deadline, failed call) degrades to raw titles or fixed text, never to a failed turn."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import email_draft
import rewrite
import slack
from change import Change
from envelope import Release
from inputs import Header
from llm import TurnDeps


@dataclass(frozen=True)
class Outcome:
    release: Release | None
    off_topic: bool = False


async def generate(deps: TurnDeps, changes: Sequence[Change], header: Header, policy: Sequence[str] = ()) -> Outcome:
    written = await rewrite.rewrite(deps, changes, header, policy)
    if written.off_topic:
        return Outcome(None, off_topic=True)
    release = Release(written.changes, written.themes, header, unfinished=written.unfinished)
    return Outcome(await drafts(deps, release, policy))


async def drafts(deps: TurnDeps, release: Release, policy: Sequence[str] = (), instruction: str = "",
                 which: str = "both") -> Release:
    """Ask again for the Slack and/or email text of `release` (`which`: slack | email | both)."""
    entries = release.entries()
    if not entries:
        return release
    updated = release
    if which in ("slack", "both"):
        updated = replace(updated, slack=await slack.ask(deps, entries, release.header, policy, instruction))
    if which in ("email", "both"):
        updated = replace(updated, email=await email_draft.ask(deps, entries, release.header, policy, instruction))
    return updated
