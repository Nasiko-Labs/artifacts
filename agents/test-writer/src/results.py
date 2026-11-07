"""Pure: from checked tests to the stored state and the reply (assemble -> envelope -> render)."""

from __future__ import annotations

import dataclasses

import envelope as envelope_mod
import prompts
import render
from assemble import assemble
from census import Census
from checks import GenTest
from lang import SourceFile
from write import Outcome


def remap(tests: list[GenTest], source: Census, target: Census) -> list[GenTest]:
    """Edge-case ids are per census; carry claims over by (callable, label)."""
    by_id = {c.id: (c.callable, c.label) for c in source.cases}
    to_id = {(c.callable, c.label): c.id for c in target.cases}
    out = []
    for t in tests:
        ids = tuple(to_id[by_id[i]] for i in t.covers if i in by_id and by_id[i] in to_id)
        out.append(dataclasses.replace(t, covers=ids))
    return out


def files_to_state(files: tuple[SourceFile, ...]) -> list[dict]:
    return [dataclasses.asdict(f) for f in files]


def files_from_state(data) -> tuple[SourceFile, ...]:
    return tuple(SourceFile(**f) for f in data or ())


def finish(census: Census, outcome: Outcome, framework: str, module: str, notes: list[str], wants_json: bool,
           base: dict) -> dict:
    """The graph update for a finished run: stored state + reply + data part."""
    assembled = assemble(census, outcome.tests, outcome.helpers, outcome.imports, framework, module)
    env = envelope_mod.build(census, assembled, outcome, framework, [n for n in notes if n])
    stored = {**base, "framework": framework, "module": module, "selected": [c.name for c in census.selected],
              "known": [c.name for c in census.callables], "remaining": list(census.remaining),
              "tests": [t.to_dict() for t in assembled.tests], "helpers": list(dict.fromkeys(outcome.helpers)),
              "imports": list(dict.fromkeys(outcome.imports)), "envelope": env}
    if wants_json:
        return {**stored, "reply": render.render_json(env), "data": env}
    if not assembled.tests:
        return {**stored, "reply": prompts.NOTHING_KEPT, "data": env}
    return {**stored, "reply": render.render(env), "data": env}


def stored_outcome(state: dict) -> Outcome:
    return Outcome(tests=[GenTest.from_dict(t) for t in state.get("tests") or ()],
                   helpers=list(state.get("helpers") or ()), imports=list(state.get("imports") or ()))
