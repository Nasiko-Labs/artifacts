"""The chain's step bodies: what each route does with this turn's input and the batch and triage re-read
from history (`recall.py`; the chain's state lives for this turn only). A view the earlier table can answer
costs no call; anything else triages the batch from history again first."""

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
import table as table_mod
from inputs import Config, Incoming, read_config
from llm import LlmError, TurnDeps, complete_json
from model import LEXICON_ONLY, Ticket
from parse_tickets import Parsed
from recall import Prior


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, prior: Prior | None = None):
        self.deps, self.incoming, self.prior = deps, incoming, prior or Prior()
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    has_prior = bool(scope.prior.batch or scope.prior.table)
    route = intake_mod.plan_turn(scope.incoming, has_prior)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


def _with_history(parsed: Parsed, scope: Scope) -> Parsed:
    """The batch from history (and its later additions) merged by id with this turn's tickets."""
    earlier = [intake_mod.parse_batch(text)[0] for text in scope.prior.batch]
    merged = {t.id: t for p in (*earlier, parsed) for t in p.tickets}
    has_own_config = read_config(parsed.instruction) != Config()
    instruction = parsed.instruction if has_own_config or not earlier else earlier[0].instruction
    return replace(parsed, tickets=tuple(merged.values()), instruction=instruction)


async def _run(parsed: Parsed, scope: Scope) -> tuple[list[Ticket], list[str], list[str], object] | str:
    """(triaged tickets, caveats, lead notes, config) or a fixed reply."""
    parsed, cap_notes = intake_mod.capped(parsed)
    config = read_config(parsed.instruction)
    now = dates.as_of(parsed.instruction) or scope.deps.now()
    aged = [replace(t, age_hours=dates.age_hours(t.created, now)) for t in parsed.tickets]
    outcome = await signals.analyse(scope.deps, aged, scope.incoming.policy)
    if outcome.status == "failed":
        return prompts.MODEL_FAILED
    if outcome.status == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    triaged = [reason.with_reason(rules.apply(t, config)) for t in outcome.tickets]
    notes = caveats.caveats(triaged, parsed.skipped, parsed.duplicates, outcome.dropped_quotes,
                            bool(config.sla_hours))
    lexicon_ids = [t.label for t in triaged if t.analysis == LEXICON_ONLY]
    lead = [prompts.DEADLINE_NOTE.format(ids=", ".join(lexicon_ids[:20]))] if lexicon_ids else []
    return triaged, notes, [*cap_notes, *lead], config


async def triage(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    parsed = _with_history(route.parsed, scope) if route.append else route.parsed
    result = await _run(parsed, scope)
    if isinstance(result, str):
        return {"reply": result}
    triaged, notes, lead, config = result
    view = followup.View(open_only=config.only_open, top=config.top)
    return {"config": asdict(config), **_show(triaged, notes, view, scope.incoming.wants_json, "view",
                                               [*route.notes, *lead])}


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
    found = scope.prior.table
    if found is not None and command.kind == "why":
        answer = table_mod.why(found, command.text)
        if answer:
            return {"reply": answer}
    if found is not None and command.kind == "view" and table_mod.serves(command.view):
        return {"reply": table_mod.render_view(found, command.view)}
    if not scope.prior.batch:
        return {"reply": prompts.NOT_IN_HISTORY}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    result = await _run(_with_history(Parsed("history"), scope), scope)
    if isinstance(result, str):
        return {"reply": result}
    tickets, notes, lead, _ = result
    lead = [*lead, prompts.RETRIAGE_NOTE]
    if command.kind == "why":
        matches = followup.find(command.text, tickets)
        if not matches:
            return {"reply": "I couldn't tell which ticket you mean. Name its id, like \"why is #4812 P2?\"."}
        return {"reply": "\n\n".join([*(reason.explain(t) for t in matches), prompts.RETRIAGE_NOTE])}
    return _show(tickets, notes, command.view, scope.incoming.wants_json, command.kind, lead)


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
        has_prior = scope.prior.batch or scope.prior.table
        return {"reply": prompts.FOLLOWUP_HELP if has_prior else intake_mod.ask_for_batch(scope.incoming)}
    return {"reply": smalltalk.OFF_TOPIC_REPLY}
