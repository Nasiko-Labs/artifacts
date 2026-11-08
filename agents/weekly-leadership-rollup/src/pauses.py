"""The one question this agent may pause on ("Which teams?", multi-select, rendered by the chat's request
card) and how the answer is read. Reading fails closed: an answer that matches no team stops the turn."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from checks import MAX_TEAMS
from nasiko_hitl import question_metadata

METADATA_KEY = "weekly_leadership_rollup"
TEAMS = "teams"
MAX_OPTIONS = 10
_SPLIT = re.compile(r"\s*(?:,|;|\band\b)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Pending:
    query: str
    teams: tuple[str, ...]
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": TEAMS, "query": self.query, "teams": list(self.teams),
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != TEAMS:
            return None
        teams = tuple(str(t) for t in data.get("teams") or () if str(t).strip())
        return Pending(query=str(data.get("query") or ""), teams=teams, wants_json=bool(data.get("wants_json")))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata). Options are the first 10 teams; others can be typed."""
    text = prompts.TEAMS_QUESTION.format(n=len(pending.teams))
    options = [(t[:200], "") for t in pending.teams[:MAX_OPTIONS]]
    return text, question_metadata(prompts.TEAMS_HEADER, options, multi_select=True, allow_custom_input=True)


def chosen_teams(pending: Pending, answer: str) -> tuple[str, ...]:
    """Picked labels arrive one per line, typed text last; each line (or piece of typed text) must name a
    visible team."""
    by_name = {t.casefold(): t for t in pending.teams}
    picked: list[str] = []
    for line in (answer or "").splitlines():
        key = _key(line)
        if key in by_name:
            picked.append(by_name[key])
            continue
        for piece in (_key(p) for p in _SPLIT.split(line)):
            picked += [by_name[piece]] if piece in by_name else [
                t for t in pending.teams if piece and re.search(rf"(?<!\w){re.escape(t.casefold())}(?!\w)", piece)]
    return tuple(dict.fromkeys(picked))[:MAX_TEAMS]


def _key(text: str) -> str:
    return text.strip().strip(".").casefold()
