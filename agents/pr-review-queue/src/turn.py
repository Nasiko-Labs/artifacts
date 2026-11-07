"""Turn 1 (and a new repository set): parse in code, decline off-topic, ask once for repositories when
none are known, read the code host (or a paste), compute the queue and render it.

Every function returns a `Step`: the reply, the envelope for the data part, proposals for the write walk,
and the state to remember for this conversation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

import intake
import paste
import prompts
import render
import smalltalk
import summary
from ask import ask_once
from deps import TurnDeps
from fetch import Fetcher
from view import Settings, Snapshot, View, build

log = logging.getLogger("pr_review_queue.turn")


@dataclass(frozen=True)
class Step:
    reply: str
    data: dict | None = None
    proposals: tuple = ()
    ask_repos: bool = False
    remember: dict = field(default_factory=dict)   # settings / snapshot to store


def decline() -> Step:
    return Step(smalltalk.OFF_TOPIC_REPLY)


async def classify_intake(deps: TurnDeps, text: str) -> tuple[str, tuple[str, ...]]:
    """("ok" | "off_topic", repositories the model read): one tool-less call. Repositories must appear
    verbatim in the user's words."""
    raw = await ask_once(deps, prompts.INTAKE_SYSTEM, prompts.fence(text))
    if prompts.OFF_TOPIC in raw.upper().replace(" ", "_") and '"ok"' not in raw:
        return "off_topic", ()
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start:end + 1]) if start != -1 and end > start else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or data.get("status") == "off_topic":
        return "off_topic", ()
    repos = tuple(r for r in data.get("repos") or () if isinstance(r, str) and r in text and intake.repos_in(r) == [r])
    return "ok", repos[:intake.MAX_REPOS]


async def intake_turn(deps: TurnDeps, text: str, known: Settings) -> Step:
    parsed = intake.parse(text)
    pasted = paste.pasted_prs(text, parsed.repos, prompts.iso(deps.now)) if _maybe_paste(text, parsed) else None
    if pasted is not None:
        repos = tuple(dict.fromkeys(f.repo for f in pasted.facts))
        settings = _settings(Settings(), parsed).with_changes(repos=repos, only="")
        return await render_queue(deps, settings, pasted, parsed.as_json)
    repos = parsed.repos
    if not repos or not parsed.on_topic_words:
        verdict, read = await classify_intake(deps, text)
        if verdict == "off_topic":
            return decline()
        repos = repos or read
    if not repos:
        return Step(prompts.ASK_REPOS, ask_repos=True, remember={"settings": _settings(known, parsed).to_dict()})
    settings = _settings(known, parsed).with_changes(repos=repos, only="")
    return await read_and_render(deps, settings, parsed, extra=parsed.extra_repos)


async def read_and_render(deps: TurnDeps, settings: Settings, parsed: intake.Parsed | None = None,
                          extra: int = 0) -> Step:
    result = await Fetcher(deps).read(settings.repos)
    if isinstance(result, str):
        return Step(result, remember={"settings": settings.to_dict()})
    if extra:
        note = f"Only the first {intake.MAX_REPOS} repositories were read ({extra} more named)."
        result = replace(result, notes=(*result.notes, note))
    return await render_queue(deps, settings, result, bool(parsed and parsed.as_json))


async def render_queue(deps: TurnDeps, settings: Settings, snapshot: Snapshot, as_json: bool) -> Step:
    v = build(snapshot, settings, deps.now)
    if v.stale:
        headline, reasons = await summary.write(deps, v)
    else:
        headline, reasons = prompts.no_stale(v.open_count, v.oldest_quiet), {}
    data = render.envelope(v, headline)
    text = render.as_json_text(data) if as_json else render.queue_markdown(v, headline, reasons)
    return Step(text, data, remember={"settings": settings.to_dict(), "snapshot": snapshot.to_dict()})


def render_load(v: View, as_json: bool) -> Step:
    headline = f"Review load for {len(v.load)} people over the last {v.settings.window_days} days."
    data = render.envelope(v, headline)
    return Step(render.as_json_text(data) if as_json else render.load_markdown(v), data)


def _settings(base: Settings, parsed: intake.Parsed) -> Settings:
    changes = {}
    if parsed.stale_days is not None:
        changes["stale_days"] = parsed.stale_days
    if parsed.include_drafts is not None:
        changes["include_drafts"] = parsed.include_drafts
    if parsed.window_days is not None:
        changes["window_days"] = parsed.window_days
    return base.with_changes(**changes)


def _maybe_paste(text: str, parsed: intake.Parsed) -> bool:
    if intake.envelope_input(text) is not None:
        return False
    return ("[" in text or "{" in text) and '"number"' in text and '"title"' in text


def settings_from(parsed: intake.Parsed, base: Settings) -> Settings:
    return _settings(base, parsed)
