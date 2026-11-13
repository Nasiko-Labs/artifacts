"""Pure: what this conversation already holds, recovered from the platform's history slice (the agent stores
nothing between messages).

- The current draft is read back from the newest assistant reply that shows one (render.draft_reply). A
  reply that is cut off (no footer) or compressed counts as lost (`trimmed`); an older draft is never used
  in its place, because it would be stale.
- The tone is replayed in code: the newest writing sample still in the user turns (re-read by the model at
  use time, never stored), then the tone settings of the user turns after it, applied by the same rule as
  a live turn ("always ..." always sticks; settings alone stick while there is no draft yet). When the
  sample has dropped out, the formality the last draft showed is kept.
- Grounding for a revision: the user's own words still in the history (never a tone sample) plus the
  recovered draft, whose specifics were already checked when it was written."""

from __future__ import annotations

import re
from dataclasses import dataclass

import inputs as i
from draft import Draft
from history import ASSISTANT, USER, Turn
from register import REGISTERS
from render import FOOTER
from tone import FORMALITY, Profile, apply_patch, settings_only, settings_patch

_HEADER = re.compile(r"^\*\*(.+?) to (.+?)\*\* · (?:[a-z ]+) ([1-5])/5 · (tone from your sample|tone from your "
                     r"settings|neutral tone)\s*$")
_SUBJECT = re.compile(r"^\*\*Subject:\*\* (.*)$")
_LABELLED = re.compile(r"^(.+) \((.+)\)$")
_CHANNEL_OF = {reg.label: reg.channel for reg in REGISTERS.values()}
_SOURCE_OF = {"tone from your sample": "sample", "tone from your settings": "settings", "neutral tone": "default"}
_FOOTER_START = FOOTER[:40]
NO_RECIPIENT = "[recipient?]"


@dataclass(frozen=True)
class Shown:
    """A draft as a reply showed it, plus the tone line of its header."""

    draft: Draft
    formality: int
    source: str


@dataclass(frozen=True)
class Memory:
    draft: Draft | None = None
    trimmed: bool = False            # a draft reply is in the history but could not be read whole
    shown_formality: int | None = None
    shown_source: str = "default"
    sample: str = ""                 # the newest writing sample still in the history ("" if none)
    patches: tuple[dict, ...] = ()   # tone settings that stuck after it, in order
    ground: tuple[str, ...] = ()
    addressing: tuple[str, ...] = ()


def looks_like_draft(text: str) -> bool:
    return bool(_HEADER.match((text or "").split("\n", 1)[0]))


def read_draft(text: str) -> Shown | None:
    """The draft a reply showed, or None when the reply is not a whole draft reply."""
    lines = (text or "").split("\n")
    header = _HEADER.match(lines[0]) if lines else None
    if header is None or _FOOTER_START not in text or "[⋯ " in text:
        return None
    channel = _CHANNEL_OF.get(header.group(1))
    subject = _SUBJECT.match(lines[1]) if len(lines) > 1 else None
    start = len(lines[0]) + 1 + (len(lines[1]) + 1 if subject else 0)
    body, sep, _ = text[start:].rpartition("\n\n")
    if channel is None or not sep or not body.strip():
        return None
    name, role = _recipient(header.group(2))
    draft = Draft(recipient_name=name, recipient_role=role, channel=channel,
                  subject=subject.group(1).strip() if subject else "", body=body.strip())
    return Shown(draft, int(header.group(3)), _SOURCE_OF[header.group(4)])


def _recipient(label: str) -> tuple[str, str]:
    if label == NO_RECIPIENT:
        return "", ""
    both = _LABELLED.match(label)
    return (both.group(1), both.group(2)) if both else (label, "")


def remember(turns: tuple[Turn, ...]) -> Memory:
    index = next((n for n in range(len(turns) - 1, -1, -1)
                  if turns[n].role == ASSISTANT and looks_like_draft(turns[n].text)), None)
    shown = read_draft(turns[index].text) if index is not None else None
    sample, patches = _tone(turns)
    users = [i.split_sample(t.text).request for t in turns if t.role == USER]
    if shown is None:
        return Memory(trimmed=index is not None, sample=sample, patches=patches, ground=tuple(users),
                      addressing=tuple(users))
    d = shown.draft
    vetted = (d.subject, d.body, d.recipient_name, d.recipient_role)
    return Memory(draft=d, shown_formality=shown.formality, shown_source=shown.source, sample=sample,
                  patches=patches, ground=(*users, *vetted), addressing=(*users, d.recipient_name, d.recipient_role))


def _tone(turns: tuple[Turn, ...]) -> tuple[str, tuple[dict, ...]]:
    sample, patches, has_draft = "", [], False
    for turn in turns:
        if turn.role == ASSISTANT:
            has_draft = has_draft or looks_like_draft(turn.text)
            continue
        split = i.split_sample(turn.text)
        if split.sample and not turn.compressed:
            sample, patches = split.sample, []
            continue
        patch = settings_patch(split.request)
        if patch and (i.is_profile_scope(split.request) or (not has_draft and settings_only(split.request, patch))):
            patches.append(patch)
    return sample, tuple(patches)


def base_profile(memory: Memory, from_sample: Profile | None) -> tuple[Profile, str | None]:
    """The tone to write in before this message's own settings, and a note when part of it was lost."""
    note = None
    if from_sample is not None:
        profile = from_sample
    elif memory.shown_source == "sample" and memory.shown_formality:
        profile = Profile(formality=memory.shown_formality, source="sample")
        note = (f"Your writing sample is no longer in the recent conversation, so I kept only its formality "
                f"({FORMALITY[memory.shown_formality]}); paste it again to match your style fully.")
    else:
        profile = Profile()
    for patch in memory.patches:
        profile = apply_patch(profile, patch)
    return profile, note
