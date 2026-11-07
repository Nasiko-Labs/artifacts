"""Turn 1 as graph nodes: locate → log → gather (commits ∥ history) → blame → verdict → render.

The nodes share one `Pipeline` per turn (the reader with its ledger and tool health, and the facts
gathered so far); only the reply, the remembered repository and the report reach the graph state.
Each node returns `route: "next"` to continue or a reply to end the turn.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import card_render
import evidence
import steps
import targets
import verdict as verdict_step
from analysis import Analysis
from deps import TurnDeps
from discovery import Discoverer
from reader import TurnReader

log = logging.getLogger("ci_detective.pipeline")


class Pipeline:
    def __init__(self, deps: TurnDeps | None):
        self._deps = deps
        self._reader: TurnReader | None = None
        self._analysis: Analysis | None = None
        self._verdict: verdict_step.Verdict | None = None

    async def locate(self, state: dict) -> dict:
        target = targets.Target.from_dict(state.get("target"))
        if target is None:
            return {"reply": "I lost track of which run to check. Please send it again.", "route": ""}
        discoverer = Discoverer(self._deps)
        failure = await discoverer.start()
        if failure:
            return {"reply": failure, "route": ""}
        self._reader = TurnReader(self._deps, discoverer)
        found = await steps.locate(self._reader, Analysis(target=target), state["query"])
        if isinstance(found, str):
            return {"reply": found, "route": ""}
        self._analysis = found
        return {"route": "next"}

    async def log(self, state: dict) -> dict:
        self._analysis = await steps.read_log(self._reader, self._analysis)
        return {"route": "next"}

    async def gather(self, state: dict) -> dict:
        a = await evidence.gather(self._reader, self._analysis)
        self._analysis = await evidence.compare_if_needed(self._reader, evidence.assess_only(a))
        return {"route": "next"}

    async def blame(self, state: dict) -> dict:
        a = await evidence.blame(self._reader, self._analysis)
        self._analysis = evidence.conclude(a, self._reader)
        return {"route": "next"}

    async def verdict(self, state: dict) -> dict:
        self._verdict = await verdict_step.decide(self._deps, self._analysis)
        self._analysis = verdict_step.with_class(self._analysis, self._verdict)
        return {"route": "next"}

    async def render(self, state: dict) -> dict:
        a, v = self._analysis, self._verdict
        if a is None or v is None:  # record_outcomes builds the graph without running it
            return {}
        text = card_render.card(a, v)
        report = card_render.report(a, v, text)
        log.info("verdict %s for run %s (class %s)", v.kind, a.run.run_id, v.failure_class)
        return {"reply": text, "report": report.to_dict(), "repo": a.target.repo_full, "route": "",
                "target": replace(a.target, job_name="").to_dict()}
