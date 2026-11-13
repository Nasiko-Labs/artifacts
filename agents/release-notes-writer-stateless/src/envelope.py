"""Pure: the stored release (what the checkpointer keeps between turns) and the output envelope built from it.

`summary`, counts, `confidence` and `needs_human` are computed here from the checks, never taken from the
model. Every zero-call follow-up changes the stored release and calls `build` again."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import notes
from change import Change
from email_draft import EmailParts
from email_draft import build as build_email
from inputs import Header
from slack import SlackParts
from slack import build as build_slack

LOW_FALLBACK_SHARE = 0.25
COUNT_ORDER = ("new", "improved", "fixed", "deprecated", "security", "breaking")


@dataclass(frozen=True)
class Release:
    changes: tuple[Change, ...]
    themes: tuple[str, ...] = ()
    header: Header = field(default_factory=Header)
    slack: SlackParts = field(default_factory=SlackParts)
    email: EmailParts = field(default_factory=EmailParts)
    unfinished: int = 0
    short: bool = False

    def to_state(self) -> dict:
        return {"changes": [c.to_dict() for c in self.changes], "themes": list(self.themes),
                "header": self.header.to_dict(), "slack": self.slack.to_dict(), "email": self.email.to_dict(),
                "unfinished": self.unfinished, "short": self.short}

    @staticmethod
    def from_state(state: Mapping[str, Any]) -> Release | None:
        if not state.get("changes"):
            return None
        return Release(tuple(Change.from_dict(c) for c in state["changes"]), tuple(state.get("themes") or ()),
                       Header.from_dict(state.get("header")), SlackParts.from_dict(state.get("slack")),
                       EmailParts.from_dict(state.get("email")), int(state.get("unfinished") or 0),
                       bool(state.get("short")))

    def entries(self) -> list[notes.Entry]:
        return notes.ordered(self.changes, self.themes)

    def internal(self) -> list[Change]:
        return [c for c in self.changes if c.internal]


def counts(entries: list[notes.Entry]) -> dict[str, int]:
    return {block: sum(1 for e in entries if e.block == block) for block in COUNT_ORDER}


def summary(entries: list[notes.Entry], internal: int) -> str:
    parts = [f"{n} {block}" for block, n in counts(entries).items() if n]
    text = f"Release notes for {len(entries)} changes" + (f": {', '.join(parts)}" if parts else "")
    return text + (f"; {internal} internal left out" if internal else "")


def flags(release: Release, entries: list[notes.Entry]) -> list[str]:
    found = []
    if any(e.change.fallback for e in entries):
        found.append("title_fallback")
    if any(e.change.jargon for e in entries):
        found.append("jargon")
    if any(e.change.breaking and not e.change.migration for e in entries):
        found.append("breaking_without_migration")
    if release.unfinished:
        found.append("unfinished")
    if release.slack.fallback or release.email.fallback:
        found.append("text_fallback")
    return found


def confidence(release: Release, entries: list[notes.Entry], found: list[str]) -> str:
    fallbacks = sum(1 for e in entries if e.change.fallback)
    if (entries and fallbacks / len(entries) > LOW_FALLBACK_SHARE) or "breaking_without_migration" in found:
        return "low"
    if release.header.version and release.header.date and not found:
        return "high"
    return "medium"


def needs_human(entries: list[notes.Entry], level: str) -> bool:
    risky = any((e.change.breaking and not e.change.migration) or e.block == "security" for e in entries)
    return risky or level == "low"


def item(entry: notes.Entry) -> dict:
    c = entry.change
    return {"number": entry.number, "id": c.id, "ref": c.ref, "url": c.url, "title": c.title, "sentence": c.sentence,
            "theme": c.theme, "kind": c.kind, "block": entry.block, "breaking": c.breaking,
            "migration": c.migration, "grounded": c.grounded, "title_only": c.fallback}


def build(release: Release) -> dict:
    entries = release.entries()
    found = flags(release, entries)
    level = confidence(release, entries, found)
    internal = release.internal()
    return {
        "summary": summary(entries, len(internal)),
        "items": [item(e) for e in entries],
        "notes_markdown": notes.markdown(entries, release.header),
        "slack_post": build_slack(release.slack, entries, release.header, release.short),
        "email_draft": build_email(release.email, entries, release.header),
        "internal_count": len(internal),
        "left_out": [c.title for c in internal],
        "counts": counts(entries),
        "version": release.header.version or None,
        "date": release.header.date or None,
        "flags": found,
        "confidence": level,
        "needs_human": needs_human(entries, level),
    }
