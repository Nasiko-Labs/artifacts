"""Pure: the channel question (single-select over up to 10 channels the chat read found, plus custom text) and
reading its answer. The post never goes out without a channel the user chose or named."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import prompts
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from subs import Channel
from walk import PHASE_KEY

MAX_OPTIONS = 10
_CHANNEL = re.compile(r"#?([A-Za-z0-9][\w.-]{0,79})")


@dataclass(frozen=True)
class Target:
    label: str        # "#revenue"
    recipient: str    # the channel id the write names (the typed "#name" when no read found it)


@dataclass(frozen=True)
class Pause:
    text: str
    hitl: dict
    metadata: dict


def _label(text: str) -> str:
    return text.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def options(channels: Sequence[Channel]) -> tuple[Target, ...]:
    unique: dict[str, Target] = {}
    for c in channels:
        unique.setdefault(_label(c.label), Target(_label(c.label), c.id))
    return tuple(unique.values())[:MAX_OPTIONS]


def matching(name: str, channels: Sequence[Channel]) -> Target | None:
    """The found channel with this name (case-insensitive, `#` ignored), or None."""
    wanted = name.lstrip("#").casefold()
    found = next((c for c in channels if c.name.lstrip("#").casefold() == wanted), None)
    return Target(found.label, found.id) if found else None


def typed(name: str) -> Target:
    clean = "#" + name.lstrip("#")
    return Target(clean, clean)


def question(targets: Sequence[Target]) -> Pause:
    hitl = question_metadata(prompts.CHANNEL_HEADER, [(t.label, "") for t in targets], multi_select=False,
                             allow_custom_input=True)
    text = f"{prompts.CHANNEL_HEADER} Pick one, or type a channel name (for example #revenue)."
    phase = {"phase": "pick_channel", "options": {t.label: t.recipient for t in targets}}
    return Pause(text, hitl, {PHASE_KEY: phase})


def read_answer(answer: str, offered: Mapping[str, str]) -> Target | None:
    text = (answer or "").strip()
    if not text:
        return None
    if text in offered:
        return Target(text, offered[text])
    match = _CHANNEL.search(text)
    if match is None:
        return None
    by_name = next((Target(k, v) for k, v in offered.items() if k.lstrip("#").casefold() == match.group(1).casefold()),
                   None)
    return by_name or typed(match.group(1))
