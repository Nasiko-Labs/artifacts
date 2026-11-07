"""Pure: the two single-select questions (which repository, which chat target) and reading their answers.

- Repo pick: repositories the code-host search returned (<= 10) when none matched the team or service name; a
  picked label is used as is (it came from that search), typed text is searched again.
- Target pick: the owner's DM and the channels found, plus custom text; the message is never posted without a
  target the user chose or named.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from records import Findings
from walk import PHASE_KEY

MAX_OPTIONS = 10
REPO_HEADER = "Which repository should I check for deploys?"
TARGET_HEADER = "Where should I post the message?"
_CHANNEL = re.compile(r"(?<![\w#])#([A-Za-z0-9][\w.-]*)")
_OWNER_WORDS = re.compile(r"\b(?:owner|dm|direct message|them|him|her)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Pause:
    text: str
    hitl: dict
    metadata: dict


@dataclass(frozen=True)
class Target:
    label: str
    recipient: str


def _label(text: str) -> str:
    return text.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def repo_question(found: Sequence[str], terms: Sequence[str]) -> Pause:
    offered = list(dict.fromkeys(_label(r) for r in found if r))[:MAX_OPTIONS]
    hitl = question_metadata(REPO_HEADER, [(r, "") for r in offered], multi_select=False, allow_custom_input=True)
    text = (f"No repository name matched {', '.join(terms)}. Pick the repository that runs this service, or type "
            "its name.")
    return Pause(text, hitl, {PHASE_KEY: {"phase": "pick_repo", "offered": offered}})


def read_repo_answer(answer: str, offered: Sequence[str]) -> tuple[tuple[str, ...], bool]:
    """(repos, verified): a picked label is verified (it came from the search); typed names are not."""
    text = (answer or "").strip()
    if text in offered:
        return (text,), True
    names = tuple(dict.fromkeys(n.strip("`'\". ") for n in re.split(r"[,\s]+", text) if n.strip("`'\". ")))
    return names[:5], False


def targets(f: Findings) -> tuple[Target, ...]:
    owner = (Target(_label(f"DM {f.owner.label}"), f.owner.chat_id),) if f.owner.known else ()
    channels = tuple(Target(_label(c.label), c.id) for c in f.channels if c.id)
    unique: dict[str, Target] = {}
    for t in (*owner, *channels):
        unique.setdefault(t.label, t)
    return tuple(unique.values())[:MAX_OPTIONS]


def named_target(text: str, f: Findings, options: Sequence[Target]) -> Target | None:
    """A target the user named in this message: a `#channel` (a found channel, else as typed) or the owner."""
    match = _CHANNEL.search(text or "")
    if match:
        name = match.group(1).casefold()
        found = next((t for t in options if t.label.lstrip("#").casefold() == name), None)
        return found or Target("#" + match.group(1), "#" + match.group(1))
    if f.owner.known and _OWNER_WORDS.search(text or ""):
        return next((t for t in options if t.recipient == f.owner.chat_id), None)
    return None


def target_question(options: Sequence[Target], post_body: str, facts: Mapping) -> Pause:
    hitl = question_metadata(TARGET_HEADER, [(t.label, "") for t in options], multi_select=False,
                             allow_custom_input=True)
    text = "Where should I post it? Pick one, or type a channel (for example #payments) or a chat user id."
    phase = {"phase": "pick_target", "options": {t.label: t.recipient for t in options}, "body": post_body,
             "facts": dict(facts)}
    return Pause(text, hitl, {PHASE_KEY: phase})


def read_target_answer(answer: str, options: Mapping[str, str]) -> Target | None:
    text = (answer or "").strip()
    if not text:
        return None
    if text in options:
        return Target(text, options[text])
    first = text.split()[0].strip("`'\".,")
    return Target(first, first) if first else None
