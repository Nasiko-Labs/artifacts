"""What a turn ends with, and the walk state that rides on the A2A task between pauses (any replica can
resume it). Task metadata round-trips through a protobuf Struct, so numbers come back as floats and are
re-read through records.from_dict."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import envelope
import render
from envelope import Checks
from records import Project, ProposedIssue, Spec, Team, from_dict, issues_from

METADATA_KEY = "spec_breaker"


@dataclass(frozen=True)
class Outcome:
    kind: str                       # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None    # task metadata: the walk, to resume from
    hitl: dict | None = None        # status-message metadata: the question, or the gateway's hitl_request_id
    envelope: dict | None = None    # the output envelope, on completion
    last: dict | None = None        # what the conversation remembers (checkpointer)


class View:
    """Read-only accessors over a walk state dict."""

    def __init__(self, state: Mapping[str, Any]):
        self.state = dict(state)

    @property
    def spec(self) -> Spec:
        return from_dict(Spec, self.state.get("spec") or {"title": "", "text": ""})

    @property
    def teams(self) -> tuple[Team, ...]:
        return tuple(from_dict(Team, t) for t in self.state.get("teams") or ())

    @property
    def projects(self) -> tuple[Project, ...]:
        return tuple(from_dict(Project, p) for p in self.state.get("projects") or ())

    @property
    def team(self) -> Team | None:
        return from_dict(Team, self.state.get("team"))

    @property
    def project(self) -> Project | None:
        return from_dict(Project, self.state.get("project"))

    @property
    def issues(self) -> tuple[ProposedIssue, ...]:
        return issues_from(self.state.get("issues"))

    @property
    def checks(self) -> Checks:
        raw = dict(self.state.get("checks") or {})
        ints = {"sources_planned", "sources_read", "extracted", "dropped"}
        return Checks(**{k: int(v) if k in ints else bool(v) for k, v in raw.items()})

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(str(n) for n in self.state.get("notes") or ())

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)


def pause(text: str, state: Mapping[str, Any], hitl: dict, kind: str = "input_required") -> Outcome:
    return Outcome(kind, text, {METADATA_KEY: dict(state)}, hitl, last=_last(state))


def complete(text: str, state: Mapping[str, Any], summary: str, awaiting: bool = False) -> Outcome:
    view = View(state)
    checks = view.checks
    if awaiting:
        checks = replace(checks, awaiting_approval=True)
    env = envelope.build(summary, view.issues, checks, view.team, view.project)
    shown = envelope.as_json_text(env) if view.get("json_mode") else text
    return Outcome("complete", shown, envelope=env, last=_last(state))


def summary_of(state: Mapping[str, Any]) -> str:
    view = View(state)
    return render.headline(view.spec, view.team, view.project, view.issues)


def _last(state: Mapping[str, Any]) -> dict:
    """The conversation's memory of the latest proposal (no spec text)."""
    view = View(state)
    return {"spec_title": view.spec.title, "issues": list(state.get("issues") or ())}
