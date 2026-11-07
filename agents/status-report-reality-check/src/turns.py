"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

import compare
import extract as extract_mod
import followup as followup_mod
import grounding
import intake as intake_mod
import match
import prompts
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps

FOLLOWUP_HELP = (
    "I can work on the comparison I already made: \"only high risk\", \"only blocked-vs-done\", \"list "
    "unmatched\", \"show matched tickets for <item>\", \"why did you flag <item>?\", \"stale after 7 days\", "
    "\"due soon within 7 days\", \"as of 2026-11-04\", \"Status map: qa = in progress\", \"include unreported "
    "risks\" or \"as json\". Or paste a new report and export.")


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, state)
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": _with_notes(route.reply, scope.incoming.notes)}
    return {"route": route.kind}


async def run(state: dict, scope: Scope) -> dict:
    route, deps, policy = scope.route, scope.deps, scope.incoming.policy
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    extracted = await extract_mod.extract(deps, route.report, route.request, policy)
    if extracted.status == extract_mod.FAILED:
        return {"reply": prompts.MODEL_FAILED}
    if extracted.status == extract_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    tickets = route.tickets
    items, dropped = grounding.ground(extracted.items, route.report, [t["id"] for t in tickets])
    if not items:
        return {"reply": prompts.NO_ITEMS}
    items, pending = match.by_code(items, tickets)
    items, fallback = await match.by_model(deps, items, pending, policy)
    meta = {**route.meta, "dropped": dropped, "coverage": extracted.coverage, "fallback": fallback}
    env = compare.run(items, tickets, route.options, deps.today(), meta)
    stored = {"items": items, "tickets": tickets, "options": route.options.to_state(), "meta": meta, "envelope": env}
    return {**stored, "data": env, "reply": _reply(env, scope)}


async def recompare(state: dict, scope: Scope) -> dict:
    opts = scope.route.options
    env = compare.run(state.get("items") or [], state.get("tickets") or [], opts, scope.deps.today(),
                      state.get("meta") or {})
    note = (f"_Re-compared the stored report and export (zero model calls): as of {env['as_of']}, stale after "
            f"{opts.stale_days} days, due soon within {opts.soon_days} days._")
    reply = render.render_json(env) if scope.incoming.wants_json else render.render(env, notes=(note,))
    return {"options": opts.to_state(), "envelope": env, "data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command, env = scope.route.command, state.get("envelope") or {}
    items = state.get("items") or []
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind in ("risk", "blocked_done", "all"):
        view, heading = _view(env, command)
        filtered = {**env, "items": view, "disagreements": view}
        reply = render.render_json(filtered) if scope.incoming.wants_json else render.render(env, view, heading)
        return {"reply": reply, "data": filtered}
    if command.kind == "unmatched":
        return {"reply": render.render_unmatched(env)}
    item = followup_mod.find_item(items, command.arg)
    if item is None:
        names = ", ".join(i["item"] for i in items[:10])
        return {"reply": f"I couldn't find '{command.arg[:80]}' among the report items: {names}."}
    if command.kind == "tickets":
        return {"reply": render.render_tickets(item, state.get("tickets") or [])}
    found = next((d for d in env.get("disagreements") or () if d.get("item_id") == item["id"]), None)
    return {"reply": render.render_why(item, found)}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    has_result = bool(state.get("envelope"))
    status = await followup_mod.classify(scope.deps, scope.incoming.text, has_result, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"reply": FOLLOWUP_HELP if has_result else prompts.NEED_BOTH}


def _view(env: dict, command: followup_mod.Command) -> tuple[list[dict], str]:
    found = env.get("disagreements") or []
    if command.kind == "risk":
        return [d for d in found if d["risk"] == command.arg], f"Only {command.arg} risk."
    if command.kind == "blocked_done":
        rules = set(followup_mod.BLOCKED_VS_DONE)
        return [d for d in found if rules & set(d["rules"])], "Only blocked-vs-done disagreements."
    return found, ""


def _reply(env: dict, scope: Scope) -> str:
    if scope.incoming.wants_json:
        return render.render_json(env)
    return render.render(env, notes=scope.incoming.notes)


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)

