"""One turn's steps, closed over its dependencies: classify and analyse in code, then (only when needed) one
model step. Each step returns the graph-state update."""

from __future__ import annotations

from dataclasses import dataclass

import envelope
import explain as model
import hitl
import probes as p
import prompts
import render
from analysis import Analysis
from analyze import analyze, cron_change, has_probe, suggested_cron
from classify import Found, classify, cue
from inputs import MAX_CONFIG_CHARS
from llm import Budget, TurnDeps


@dataclass(frozen=True)
class Request:
    text: str
    org: tuple[str, ...] = ()
    wants_json: bool = False
    forced: str | None = None       # the kind chosen in Q1
    pauses: int = 0


class Turn:
    def __init__(self, deps: TurnDeps, request: Request, budget: Budget):
        self.deps = deps
        self.request = request
        self.budget = budget
        self.found: Found | None = None
        self.analysis: Analysis | None = None
        self.has_config = False

    # --- intake (code only) -------------------------------------------------------------------------
    def intake(self, last: dict | None) -> dict:
        request = self.request
        found = classify(request.text, request.forced)
        self.found = found
        if found.status == "unsupported":
            return _reply(prompts.unsupported_reply(found.unsupported))
        if found.status == "too_long":
            return _reply(prompts.too_long_reply(len(found.config), MAX_CONFIG_CHARS))
        if found.status == "ambiguous":
            self.has_config = True
            return self._pause()
        if found.status == "none":
            return self._no_config(found, last)
        analysis = analyze(found.kind, found.config, p.read(found.words), self.deps.now)
        self.analysis = analysis
        remembered = {"kind": found.kind, "config": found.config, "certain": found.certain}
        if not analysis.needs_model:
            return {**self._finish(analysis, None, found.certain), "last": remembered}
        if not self.deps.traceparent:
            return _reply(prompts.NO_TRACE_CONTEXT)
        return {"route": "explain"}

    def _pause(self) -> dict:
        if self.request.pauses >= hitl.MAX_PAUSES:
            return _reply(prompts.PASTE_CONFIG)
        pending = hitl.Pending(self.request.text, self.request.org, self.request.wants_json, self.request.pauses + 1)
        return {"pending": pending.to_metadata(), "route": ""}

    def _no_config(self, found: Found, last: dict | None) -> dict:
        if self.request.forced:
            return _reply(prompts.PASTE_CONFIG)
        if last and last.get("config"):
            probes = p.read(found.words)
            if has_probe(last["kind"], probes):
                return self._code_followup(last, probes)
            return {"route": "followup"} if self.deps.traceparent else _reply(prompts.NO_TRACE_CONTEXT)
        if cue(found.words):
            return self._pause()
        return {"route": "screen"} if self.deps.traceparent else _reply(prompts.NO_TRACE_CONTEXT)

    def _code_followup(self, last: dict, probes: p.Probes) -> dict:
        change = cron_change(probes.words) if last["kind"] == "cron" else None
        if change:
            suggestion = suggested_cron(last["config"], change)
            if suggestion is not None:
                analysis = analyze("cron", suggestion[0], probes, self.deps.now)
                head = (f"Suggested change (untested: checked by my parser, not by your scheduler): "
                        f"`{suggestion[0]}`. {suggestion[1]}\n\n")
                done = self._finish(analysis, None, True)
                return {**done, "reply": done["reply"] if self.request.wants_json else head + done["reply"]}
        analysis = analyze(last["kind"], last["config"], probes, self.deps.now)
        envelope_data = envelope.build(analysis, None, bool(last.get("certain")))
        if self.request.wants_json or last["kind"] == "cron":
            return {"reply": render.reply(analysis, None, envelope_data, self.request.wants_json),
                    "data": envelope_data, "route": ""}
        lines = "\n".join(f"- {line}" for line in analysis.checked) or "- Nothing to check."
        return {"reply": f"**Checked for you** (on the {render.TITLES[analysis.kind]} from earlier)\n{lines}",
                "data": envelope_data, "route": ""}

    def _finish(self, analysis: Analysis, explained: model.Explained | None, certain: bool) -> dict:
        data = envelope.build(analysis, explained, certain)
        return {"reply": render.reply(analysis, explained, data, self.request.wants_json), "data": data, "route": ""}

    # --- model steps -------------------------------------------------------------------------------
    async def explain(self) -> dict:
        found, analysis = self.found, self.analysis
        explained = await model.explain(self.deps, self.budget, analysis, found.words, self.request.org)
        if explained.status == "off_topic":
            return _reply(prompts.OFF_TOPIC_REPLY)
        remembered = {"kind": found.kind, "config": found.config, "certain": found.certain}
        return {**self._finish(analysis, explained, found.certain), "last": remembered}

    async def followup(self, last: dict) -> dict:
        analysis = analyze(last["kind"], last["config"], p.read(""), self.deps.now)
        status, answer = await model.follow_up(self.deps, self.budget, analysis, self.found.words, self.request.org)
        if status == "off_topic":
            return _reply(prompts.OFF_TOPIC_REPLY)
        if status != "ok":
            return _reply(prompts.COULD_NOT_EXPLAIN.split(";")[0] + ". Try asking it another way.")
        return _reply(answer + f"\n\n_About the {render.TITLES[analysis.kind]} from earlier in this conversation._")

    async def screen(self) -> dict:
        verdict = await model.screen(self.deps, self.budget, self.found.words, self.request.org)
        if verdict == "off_topic":
            return _reply(prompts.OFF_TOPIC_REPLY)
        return self._pause()


def _reply(text: str) -> dict:
    return {"reply": text, "route": ""}
