"""Pure: the standup markdown, from templates in code. The model writes no line: each one is assembled
out of a checked record (title or checked gist, reference, link, state). Links come only from records.
People are in the order typed (or alphabetical); lines newest first. Counts are computed here."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from standup import Line, PersonStandup
from window import Window

MAX_TEXT_CHARS = 120
READ_ONLY_NOTE = "_Read-only: nothing was posted or changed. Copy the standup above._"
_URL = re.compile(r"^https?://[^\s()<>]+$")
_UNSAFE = re.compile(r"[\[\]()<>*`|\\\x00-\x1f]")
_UNSAFE_NOTE = re.compile(r"[\[\]()<>*|\\\x00-\x1f]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class View:
    only: tuple[str, ...] = ()  # people labels to show; empty = everyone
    shorter: bool = False
    reviews: bool = False
    blocked_only: bool = False
    chat_ready: bool = False

    def to_dict(self) -> dict:
        return {"only": list(self.only), "shorter": self.shorter, "reviews": self.reviews,
                "blocked_only": self.blocked_only, "chat_ready": self.chat_ready}

    @staticmethod
    def from_dict(raw: Mapping) -> View:
        return View(tuple(raw.get("only", ())), bool(raw.get("shorter")), bool(raw.get("reviews")),
                    bool(raw.get("blocked_only")), bool(raw.get("chat_ready")))


def clean(text: str) -> str:
    return _SPACES.sub(" ", _UNSAFE.sub(" ", text or "")).strip()[:MAX_TEXT_CHARS]


def clean_note(text: str) -> str:
    """A note built in code (may carry a `branch` in backticks)."""
    return _SPACES.sub(" ", _UNSAFE_NOTE.sub(" ", text or "")).strip()[:MAX_TEXT_CHARS]


def visible(people: Sequence[PersonStandup], view: View) -> list[PersonStandup]:
    wanted = {p.casefold() for p in view.only}
    shown = [p for p in people if not wanted or p.name.casefold() in wanted]
    return [p for p in shown if p.blocked] if view.blocked_only else shown


def counts(people: Sequence[PersonStandup], view: View) -> tuple[int, int, int]:
    shown = visible(people, view)
    return (sum(len(p.shipped) for p in shown), sum(len(p.in_progress) for p in shown),
            sum(len(p.blocked) for p in shown))


def standup(people: Sequence[PersonStandup], scope: str, window: Window, view: View,
            gists: Mapping[tuple[str, str], str] | None = None) -> str:
    gists = gists or {}
    shown = visible(people, view)
    shipped, progress, blocked = counts(people, view)
    head = [f"Standup · {clean(scope) or 'your connected tools'} · {window.describe()}"]
    head.append(f"Team: {blocked} blocked" if view.blocked_only
                else f"Team: {shipped} shipped · {progress} in progress · {blocked} blocked")
    if view.blocked_only and not shown:
        head.append("Nobody is blocked in this window.")
    body = [_person(p, view, gists) for p in shown]
    if view.chat_ready:
        return "```\n" + "\n".join([*head, *body]) + "\n```"
    return "\n\n".join([*head, *body])  # blank lines: each header and person is its own markdown block


def _person(person: PersonStandup, view: View, gists) -> str:
    name = clean(person.name)
    if person.silent and not (view.reviews and person.reviews):
        return f"- *{name}* - no activity found in the window" if view.chat_ready else \
            f"**{name}** - no activity found in the window"
    sections = [("Blocked", person.blocked)] if view.blocked_only else [
        ("Shipped", person.shipped), ("In progress", person.in_progress), ("Blocked", person.blocked)]
    if view.reviews and not view.blocked_only:
        sections.append(("Reviews given", person.reviews))
    lines = [f"- *{name}*" if view.chat_ready else f"**{name}**"]
    for title, items in sections:
        if items:
            text = "; ".join(_item(i, view, gists.get((person.name, i.ref))) for i in items)
            lines.append(f"  - {title}: {text}" if view.chat_ready else f"- {title}: {text}")
    return "\n".join(lines)


def _item(line: Line, view: View, gist: str | None) -> str:
    if line.kind == "note":
        return clean(line.title)
    link = _link(line.ref, line.url, view.chat_ready)
    if line.kind == "commits":
        return f"{clean_note(line.note)} ({_link('latest commit', line.url, view.chat_ready)})"
    if line.reason:
        return _blocked(line, link, view, gist)
    extra = ", ".join(clean(x) for x in (line.note, *line.links) if x)
    if view.shorter or not (gist or line.title):
        return f"{link} {extra}".strip()
    return f"{clean(gist or line.title)} ({link}, {extra})" if extra else f"{clean(gist or line.title)} ({link})"


def _blocked(line: Line, link: str, view: View, gist: str | None) -> str:
    why = clean(line.reason) if line.reason != "comment" else "comment"
    quote = f" (\"{clean(line.quote)}\")" if line.quote else ""
    on = f", blocked on {clean(line.blocked_on)}" if line.blocked_on else ""
    head = link if view.shorter or not (gist or line.title) else f"{clean(gist or line.title)} ({link})"
    return f"{head}: {why}{on}{quote}"


def _link(text: str, url: str, chat_ready: bool) -> str:
    label = clean(text)
    if not _URL.match(url or ""):
        return label
    return f"<{url}|{label}>" if chat_ready else f"[{label}]({url})"


def coverage_block(lines: Sequence[str]) -> str:
    return "\n".join(f"Coverage: {_SPACES.sub(' ', line).strip()}" for line in lines)
