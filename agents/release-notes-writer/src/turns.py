"""The graph's node bodies: what each route does with the stored release and this turn's input."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import envelope as envelope_mod
import followup as followup_mod
import intake as intake_mod
import notes
import pipeline
import prompts
import render
import smalltalk
from classify import classify_all
from envelope import Release
from inputs import Incoming
from llm import TurnDeps

TONE_WORDS = {"friendly": "friendlier", "formal": "more formal", "neutral": "neutral"}


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: Mapping[str, Any], scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, Release.from_state(state) is not None)
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    if route.kind in ("run", "scope") and not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACEPARENT}
    return {"route": route.kind}


def finish(release: Release, scope: Scope, extra: tuple[str, ...] = (), view: str | None = None) -> dict:
    env = envelope_mod.build(release)
    if scope.incoming.wants_json:
        reply = render.render_json(env)
    else:
        reply = render.render(env, release.header.channel, extra, release.unfinished, view)
    return {**release.to_state(), "envelope": env, "data": env, "reply": reply}


async def run(state: Mapping[str, Any], scope: Scope) -> dict:
    route = scope.route
    outcome = await pipeline.generate(scope.deps, route.changes, route.header, scope.incoming.policy)
    if outcome.off_topic or outcome.release is None:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return finish(outcome.release, scope)


def _set_block(release: Release, number: int, block: str | None) -> Release | str:
    """Drop (block None) or move item `number`; a message when there is no such item."""
    entries = release.entries()
    if not 1 <= number <= len(entries):
        return f"There's no item {number}; these notes have {len(entries)}."
    target = entries[number - 1].change.id

    def change(c):
        if c.id != target:
            return c
        if block is None:
            return replace(c, dropped=True)
        if block == "breaking":
            return replace(c, breaking=True)
        return replace(c, kind=block, breaking=False)

    return replace(release, changes=tuple(change(c) for c in release.changes))


async def followup(state: Mapping[str, Any], scope: Scope) -> dict:
    release = Release.from_state(state)
    command = scope.route.command
    kind = command.kind
    if kind == "json":
        env = envelope_mod.build(release)
        return {"reply": render.render_json(env), "data": env}
    if kind == "internal":
        return {"reply": render.render_internal(release.internal())}
    if kind in ("drop", "move"):
        updated = _set_block(release, command.number, command.value if kind == "move" else None)
        if isinstance(updated, str):
            return {"reply": updated}
        done = "dropped" if kind == "drop" else "moved"
        return finish(updated, scope, (f"Item {command.number} {done}; the rest are renumbered.",))
    if kind == "only":
        entries = release.entries()
        view = notes.markdown(entries, release.header, only=command.value)
        if not any(e.block == command.value for e in entries):
            view += f"\n\n_No {notes.LABELS[command.value]} items in these notes._"
        return finish(release, scope, (f"Showing only {notes.LABELS[command.value]}; the drafts below are unchanged.",),
                      view)
    if kind in ("version", "date"):
        return finish(replace(release, header=replace(release.header, **{kind: command.value})), scope,
                      (f"Set the {kind} to {command.value}.",))
    if kind == "first":
        return finish(release, scope, ("Breaking changes are always listed first.",))
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    return await _with_calls(release, scope, command)


async def _with_calls(release: Release, scope: Scope, command: followup_mod.Command) -> dict:
    deps, policy = scope.deps, scope.incoming.policy
    if command.kind == "include":
        changes = tuple(classify_all([replace(c, sentence="", theme="") for c in release.changes], True))
        outcome = await pipeline.generate(deps, changes, release.header, policy)
        if outcome.release is None:
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        return finish(outcome.release, scope, ("Internal changes are now included.",))
    if command.kind == "shorter":
        short = release.short or command.value in ("slack", "both")
        updated = await pipeline.drafts(deps, replace(release, short=short), policy, "Make it shorter.", command.value)
        return finish(updated, scope, ("Shortened the " + {"both": "Slack post and email", "slack": "Slack post",
                                                             "email": "email"}[command.value] + ".",))
    if command.kind == "audience":
        header = replace(release.header, audience=command.value)
        updated = await pipeline.drafts(deps, replace(release, header=header), policy)
        return finish(updated, scope, (f"Audience set to {command.value}; the Slack post and email were rewritten.",))
    header = replace(release.header, tone=command.value)
    updated = await pipeline.drafts(deps, replace(release, header=header), policy,
                                    f"Make it {TONE_WORDS[command.value]}.")
    return finish(updated, scope, (f"Tone set to {command.value}; the Slack post and email were rewritten.",))


async def scope_check(state: Mapping[str, Any], scope: Scope) -> dict:
    release = Release.from_state(state)
    status = await followup_mod.classify(scope.deps, scope.incoming.text, release is not None, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == followup_mod.REVISE and release is not None:
        updated = await pipeline.drafts(scope.deps, release, scope.incoming.policy, scope.incoming.text[:500])
        return finish(updated, scope, ("Rewrote the Slack post and email as asked; the notes are unchanged.",))
    return {"reply": prompts.TOO_FEW}
