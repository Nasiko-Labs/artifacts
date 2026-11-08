"""The per-owner message: everything factual is written by code (issue list, links, why, suggested actions,
the honesty line); the model words only one intro sentence, which is checked here and replaced by a fixed one
when it carries any detail. Also the checks on a message the user edited on the approval card."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import replies
import rules
from config import Config
from records import Finding, Owner
from send_guard import BROADCAST

FALLBACK_INTRO = "Here is a quick check of some tracker issues assigned to you."
MAX_INTRO_CHARS = 160
_INTRO_FORBIDDEN = re.compile(r"\d|https?://|www\.|@|\btap\b|\bbutton|\breply\b|\brespond|\bdeadline|\bpromise|\bby "
                              r"(?:monday|tuesday|wednesday|thursday|friday|tomorrow|today)", re.IGNORECASE)


@dataclass(frozen=True)
class Dm:
    owner_key: str
    owner_name: str
    user_id: str          # empty: no chat match, copy only
    body: str
    tool: str = ""
    links: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {**asdict(self), "links": list(self.links), "warnings": list(self.warnings)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Dm:
        values = {f.name: data.get(f.name) for f in fields(Dm)}
        return Dm(owner_key=str(values["owner_key"] or ""), owner_name=str(values["owner_name"] or ""),
                  user_id=str(values["user_id"] or ""), body=str(values["body"] or ""), tool=str(values["tool"] or ""),
                  links=tuple(str(x) for x in values["links"] or ()),
                  warnings=tuple(str(x) for x in values["warnings"] or ()))


def checked_intro(text: str) -> str:
    """The model's sentence if it is one short line with no issue detail, number, link, mention or promise."""
    sentence = " ".join((text or "").split()).strip().strip('"')
    if not sentence or len(sentence) > MAX_INTRO_CHARS or _INTRO_FORBIDDEN.search(sentence):
        return FALLBACK_INTRO
    return sentence


def first_name(owner: Owner) -> str:
    name = owner.name if "@" not in owner.name else owner.name.split("@")[0]
    return (name.split() or ["there"])[0]


def issue_line(finding: Finding) -> str:
    issue = finding.issue
    title = issue.id if issue.title == issue.id else f"{issue.id} {issue.title}"
    link = issue.url or "link unknown"
    actions = "; ".join(f"{n}) {action}" for n, action in enumerate(finding.actions, start=1))
    return f"- {title} - {link} - why: {rules.why(finding)} - suggested: {actions}"


def body_for(owner: Owner, intro: str, config: Config) -> str:
    shown = owner.findings[:config.max_dm_issues]
    rest = owner.count - len(shown)
    noun = "issue" if owner.count == 1 else "issues"
    lines = [f"Hi {first_name(owner)},", intro, "", f"You have {owner.count} {noun} that need attention:",
             *(issue_line(f) for f in shown)]
    if rest:
        teams = ", ".join(dict.fromkeys(rules.team_of(f.issue) for f in owner.findings[len(shown):]))
        lines.append(f"...and {rest} more in the {teams} view.")
    return "\n".join([*lines, "", replies.HONESTY_LINE])


def build(owner: Owner, intro: str, config: Config, tool: str = "") -> Dm:
    links = tuple(f.issue.url for f in owner.findings[:config.max_dm_issues] if f.issue.url)
    return Dm(owner_key=owner.key, owner_name=owner.name, user_id=owner.user_id,
              body=body_for(owner, checked_intro(intro), config), tool=tool, links=links)


def edit_problems(text: str, original: Dm, config: Config) -> tuple[str, ...]:
    """What an edited message lost or broke; shown once as a warning before it is sent anyway."""
    problems = []
    if replies.HONESTY_LINE.casefold() not in " ".join(text.split()).casefold():
        problems.append("the line saying replies are not read was removed")
    missing = [link for link in original.links if link not in text]
    if missing:
        problems.append(f"{len(missing)} issue link(s) were removed")
    if len(text) > config.max_message_chars:
        problems.append(f"it is longer than {config.max_message_chars} characters")
    if BROADCAST.search(text):
        problems.append("it contains a broadcast mention, which is never sent")
    return tuple(problems)


def copyable(dm: Dm, reason: str) -> str:
    return f"**{dm.owner_name}**: {reason} Copy it if you want to send it yourself:\n\n```\n{dm.body}\n```"


def copyable_all(dms: Sequence[Dm], reason: str) -> str:
    return "\n\n".join(copyable(d, reason) for d in dms)
