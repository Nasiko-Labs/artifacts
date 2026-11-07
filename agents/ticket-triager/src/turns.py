"""The graph's node bodies: what each route does with the stored batch and this turn's input."""

from __future__ import annotations

from dataclasses import asdict, replace

import caveats
import dates
import followup
import intake as intake_mod
import prompts
import reason
import render
import rules
import signals
import smalltalk
from inputs import Config, Incoming, read_config
from llm import LlmError, TurnDeps, complete_json
from model import LEXICON_ONLY, Ticket, from_dict, to_dict


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("tickets")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def _config(state: dict, instruction: str, append: bool) -> Config:
    fresh = read_config(instruction)
    stored = state.get("config")
    if append and stored and fresh == Config():
        return Config(tuple(map(tuple, stored["sla_hours"])), tuple(map(tuple, stored["routing"])),
                      stored["only_open"], stored["top"])
    return fresh


async def triage(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    parsed = route.parsed
    config = _config(state, parsed.instruction, route.append)
    now = dates.as_of(parsed.instruction) or scope.deps.now()
    aged = [replace(t, age_hours=dates.age_hours(t.created, now)) for t in parsed.tickets]
    outcome = await signals.analyse(scope.deps, aged, scope.incoming.policy)
    if outcome.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    triaged = [reason.with_reason(rules.apply(t, config)) for t in outcome.tickets]
    if route.append:
        merged = {t.id: t for t in (*(from_dict(d) for d in state.get("tickets") or ()), *triaged)}
        triaged = list(merged.values())
    notes = caveats.caveats(triaged, parsed.skipped, parsed.duplicates, outcome.dropped_quotes,
                            bool(config.sla_hours))
    lexicon_ids = [t.label for t in triaged if t.analysis == LEXICON_ONLY]
    lead = [prompts.DEADLINE_NOTE.format(ids=", ".join(lexicon_ids[:20]))] if lexicon_ids else []
    view = followup.View(open_only=config.only_open, top=config.top)
    stored = {"tickets": [to_dict(t) for t in triaged], "notes": notes, "config": asdict(config)}
    return {**stored, **_show(triaged, notes, view, scope.incoming.wants_json, "view", lead)}


def _show(tickets: list[Ticket], notes: list[str], view: followup.View, wants_json: bool, kind: str,
          lead: list[str] = ()) -> dict:
    shown = followup.apply(view, tickets)
    env = caveats.envelope(tickets, shown, notes)
    if kind == "csv":
        reply = render.render_csv(shown)
    elif wants_json or kind == "json":
        reply = render.render_json(env)
    else:
        sort_note = {"risk": "sorted by risk, then priority, then age",
                     "age": "sorted by age, oldest first"}.get(view.sort, "sorted by priority, then risk, then age")
        reply = render.render(tickets, shown, notes, view.note(), sort_note)
    return {"reply": "\n\n".join([*lead, reply]), "data": env}


async def followup_turn(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    tickets = [from_dict(d) for d in state.get("tickets") or ()]
    notes = list(state.get("notes") or ())
    if command.kind == "why":
        matches = followup.find(command.text, tickets)
        if not matches:
            return {"reply": "I couldn't tell which ticket you mean. Name its id, like \"why is #4812 P2?\"."}
        return {"reply": "\n\n".join(reason.explain(t) for t in matches)}
    return _show(tickets, notes, command.view, scope.incoming.wants_json, command.kind)


def _scope_status(data) -> dict | None:
    status = data.get("status") if isinstance(data, dict) else None
    return {"status": status} if status in ("off_topic", "about_triage", "needs_tickets") else None


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    try:
        result = await complete_json(scope.deps, prompts.scope_messages(scope.incoming.text, scope.incoming.policy),
                                     prompts.SCOPE_CONTRACT, _scope_status, max_tokens=50)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if result["status"] == "needs_tickets":
        return {"reply": prompts.NO_TICKETS}
    if result["status"] == "about_triage":
        return {"reply": prompts.FOLLOWUP_HELP if state.get("tickets") else prompts.NO_BATCH}
    return {"reply": smalltalk.OFF_TOPIC_REPLY}
