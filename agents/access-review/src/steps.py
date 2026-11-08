"""The conversational graph nodes: intake (a new review, or the answer to the organization question) and
the follow-up on a stored review (filters, drafts, policy changes, mappings). Every prompt that reads
the user's words carries the scope rule and the off-topic sentinel. A request to remove access is
answered with drafts only: this agent has no write tool."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

import intake
import policy as pol
import prompts
import render
import review
import smalltalk
from deps import TurnDeps
from parse import AccessRow, RosterRow
from reader import ask_once

_MAX_QUERY_CHARS = 2_000


def _reply(text: str, **extra) -> dict:
    return {"reply": text, "route": "", **extra}


async def intake_node(deps: TurnDeps, state: dict) -> dict:
    query = state["query"]
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    pending = state.get("pending") or {}
    mappings = dict(state.get("mappings") or {})
    request = intake.read(query)
    if pending.get("stage") == "org" and not request.has_data:
        return await _answer_org(deps, query, pending, mappings)
    override = pol.parse(f"{request.policy_text}\n{request.prose}", pol.Policy.from_dict(state.get("policy")))
    cmd = intake.command(request.prose, ())
    if cmd.kind == "map":
        mappings = {**mappings, cmd.login: cmd.email}
    saved = {"policy": override.policy.to_dict(), "mappings": mappings, "pending": {}}
    if not request.has_data and not request.org:
        return {**saved, **await _no_data(deps, request, override, cmd)}
    parsed = await review.parse_request(deps, request)
    if not parsed.access and not parsed.roster and not request.org:
        rejected = parsed.access_rejected + parsed.roster_rejected
        text = prompts.NO_ROWS.format(rejected=rejected) if rejected else prompts.NOTHING_TO_REVIEW
        return _reply(text, **saved)
    if request.wants_code_host and not request.org and not state.get("as_json"):
        ask = {"stage": "org", "parsed": _parsed_to_dict(parsed), "policy": override.policy.to_dict(),
               "changed": list(override.changed)}
        return _reply(prompts.ASK_ORG, **{**saved, "pending": ask}, ask=True, ask_header=prompts.ASK_ORG_HEADER)
    result = await review.run(deps, request, parsed, override.policy, mappings, override.changed)
    return {**result, **saved}


async def _no_data(deps: TurnDeps, request: intake.Request, override: pol.Override, cmd: intake.Command) -> dict:
    if cmd.kind == "remove":
        return _reply(f"{prompts.REMOVE_REFUSAL}\n\n{prompts.NO_REVIEW}")
    if override.changed or cmd.kind == "map":
        what = "; ".join((*override.changed, *((f"map {cmd.login} to {cmd.email}",) if cmd.kind == "map" else ())))
        return _reply(f"Saved for this conversation: {what}. " + prompts.NOTHING_TO_REVIEW)
    if request.wants_code_host:
        return _reply(prompts.ASK_ORG, pending={"stage": "org", "parsed": {}, "policy": override.policy.to_dict(),
                                                "changed": list(override.changed)},
                      ask=True, ask_header=prompts.ASK_ORG_HEADER)
    intent = await _intent(deps, request.prose)
    if intent == prompts.OFF_TOPIC:
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    if intent == "remove":
        return _reply(f"{prompts.REMOVE_REFUSAL}\n\n{prompts.NO_REVIEW}")
    return _reply(prompts.NOTHING_TO_REVIEW)


async def _intent(deps: TurnDeps, text: str) -> str:
    answer = await ask_once(deps, ({"role": "system", "content": prompts.INTENT_SYSTEM},
                                   {"role": "user", "content": prompts.fence_paste(text[:_MAX_QUERY_CHARS])}))
    if prompts.OFF_TOPIC in answer:
        return prompts.OFF_TOPIC
    try:
        data = json.loads(answer.strip().strip("`").removeprefix("json").strip())
    except ValueError:
        data = {}
    intent = str(data.get("intent") or "") if isinstance(data, dict) else ""
    return intent if intent in ("review", "remove") else prompts.OFF_TOPIC


async def _answer_org(deps: TurnDeps, query: str, pending: dict, mappings: dict) -> dict:
    text = query.strip()
    skipped = text.casefold().startswith("skip") or text == prompts.SKIP_ORG[0]
    first = re.match(r"@?([A-Za-z0-9][A-Za-z0-9-]{0,38})\b", text)
    org = "" if skipped else (intake.org_in(text) or (first.group(1) if first else ""))
    request = intake.Request(org=org, repos=intake.repos_in(text, org), wants_code_host=False)
    parsed = _parsed_from_dict(pending.get("parsed") or {})
    if not org and not parsed.access and not parsed.roster:
        return _reply(prompts.NOTHING_TO_REVIEW, pending={})
    result = await review.run(deps, request, parsed, pol.Policy.from_dict(pending.get("policy")), mappings,
                              tuple(pending.get("changed") or ()))
    return {**result, "pending": {}}


def _parsed_to_dict(parsed: review.Parsed) -> dict:
    return {"access": [asdict(r) for r in parsed.access], "access_rejected": parsed.access_rejected,
            "roster": [asdict(r) for r in parsed.roster], "roster_rejected": parsed.roster_rejected}


def _parsed_from_dict(data: dict) -> review.Parsed:
    return review.Parsed(tuple(AccessRow(**r) for r in data.get("access") or ()), int(data.get("access_rejected") or 0),
                         tuple(RosterRow(**r) for r in data.get("roster") or ()), int(data.get("roster_rejected") or 0),
                         0)


async def followup_node(deps: TurnDeps, state: dict) -> dict:
    query = state["query"]
    stored = state.get("review") or {}
    override = pol.parse(query, pol.Policy.from_dict(state.get("policy")))
    cmd = intake.command(query, stored.get("systems") or ())
    if cmd.kind == "map":
        mappings = {**dict(state.get("mappings") or {}), cmd.login: cmd.email}
        return _reply(prompts.NEEDS_REPASTE.format(what=f"map {cmd.login} to {cmd.email}"), mappings=mappings)
    if override.changed:
        return _reply(prompts.NEEDS_REPASTE.format(what="; ".join(override.changed)),
                      policy=override.policy.to_dict())
    if not cmd.kind:
        if not deps.traceparent:
            return _reply(prompts.NO_TRACE_CONTEXT)
        cmd = await _classified(deps, query)
        if cmd is None:
            return _reply(smalltalk.OFF_TOPIC_REPLY)
    return _command_reply(cmd, stored, query)


async def _classified(deps: TurnDeps, query: str) -> intake.Command | None:
    answer = await ask_once(deps, ({"role": "system", "content": prompts.FOLLOWUP_SYSTEM},
                                   {"role": "user", "content": prompts.fence_paste(query[:_MAX_QUERY_CHARS])}))
    word = answer.strip().strip("`.\"'").split()[0].upper() if answer.strip() else ""
    return {"SHOW": intake.Command("show"), "DRAFTS": intake.Command("drafts"), "REMOVE": intake.Command("remove"),
            "RERUN": intake.Command("rerun")}.get(word)


def _command_reply(cmd: intake.Command, stored: dict, query: str) -> dict:
    if cmd.kind == "rerun":
        return _reply(prompts.NEEDS_REPASTE.format(what="that change"))
    if cmd.kind == "remove":
        named = _named(stored, query)
        text = render.drafts_only(stored, cmd.finding, cmd.system, prompts.REMOVE_REFUSAL)
        if named:
            text = render.drafts_only({**stored, "removals": named}, "", "", prompts.REMOVE_REFUSAL)
        return _reply(text)
    if cmd.kind == "drafts":
        return _reply(render.drafts_only(stored, cmd.finding, cmd.system))
    text = render.report(stored, cmd.finding, cmd.system)
    hint = "" if cmd.kind == "show" else f"\n\n{prompts.FOLLOWUP_HINT}"
    return _reply(text + hint, envelope=render.envelope(stored, cmd.finding, cmd.system))


def _named(stored: dict, query: str) -> list[dict]:
    """Drafts whose person label shares a word (3+ letters) with the request: "remove access for Dana"."""
    words = {w for w in re.findall(r"[a-z]{3,}", query.casefold())} - {"remove", "access", "for", "the", "and"}
    return [r for r in stored.get("removals") or ()
            if words & set(re.findall(r"[a-z]{3,}", str(r.get("person") or "").casefold()))]
