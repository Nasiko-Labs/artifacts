"""The graph's node bodies: intake (code routing), review, follow-up and the scope/question call.

Routing, in order: small talk -> intro (0 calls); no traceparent -> fixed reply (0 calls); pasted config ->
review; a follow-up command on the stored review -> re-render (0 calls); "why is AR004 a problem" -> the
catalog (0 calls); a plain review request with nothing pasted -> one question (0 calls); a question naming a
reviewed alert -> one scoped call; anything else -> one call that may come back off_topic."""

from __future__ import annotations

import re
from dataclasses import dataclass

import catalog
import detect
import envelope
import followup
import hitl
import prompts
import render
import review
import smalltalk
from inputs import Incoming, clip, from_envelope
from llm import TurnDeps

_REVIEW_ASK = re.compile(r"\b(?:review|check|audit|look at|lint|go over)\b", re.IGNORECASE)
_ALERT_WORDS = re.compile(r"\b(?:alert(?:s|ing)?|rules?|monitors?|alarms?|alertmanager|prometheus|datadog|"
                          r"cloudwatch|grafana|paging|routing)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                     # reply | review | view | config | alert_question | question | pause
    text: str = ""
    command: followup.Command | None = None
    alert: str = ""
    pending: hitl.Pending | None = None


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: Route | None = None


def route_for(incoming: Incoming, state: dict, has_trace: bool) -> Route:
    text = incoming.text
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", canned)
    if not has_trace:
        return Route("reply", prompts.NO_TRACEPARENT)
    text = from_envelope(text) or text
    if detect.looks_like_config(text):
        return Route("review", text)
    names = [a["name"] for a in state.get("alerts") or ()]
    cmd = followup.command(text, names)
    if cmd is not None and cmd.kind == "explain":
        return Route("reply", catalog.explain(cmd.rule) or prompts.NO_REVIEW)
    if state.get("envelope"):
        if cmd is not None:
            return Route(cmd.kind, text, cmd)
        alert = followup.mentioned_alert(text, names)
        return Route("alert_question", text, alert=alert) if alert else Route("question", text)
    if _REVIEW_ASK.search(text) and _ALERT_WORDS.search(text) and len(text) < 300:
        return Route("pause", text)
    return Route("question", text)


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or route_for(scope.incoming, state, bool(scope.deps.traceparent))
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.text}
    if route.kind == "pause":
        pending = route.pending or hitl.Pending(instruction=route.text[:detect.INSTRUCTION_MAX_CHARS],
                                                wants_json=scope.incoming.wants_json)
        return {"route": "", "pause": pending.to_metadata()}
    return {"route": "review" if route.kind == "review" else "followup"}


def resume_route(answer: hitl.Answer) -> Route:
    if answer.action == "run":
        return Route("review", answer.text)
    if answer.action == "reask":
        return Route("pause", pending=answer.pending)
    return Route("reply", prompts.CANCELLED if answer.action == "cancel" else hitl.GAVE_UP)


async def run_review(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    text, clip_note = clip(scope.route.text)
    split = detect.split(text)
    settings = detect.settings(text, split.instruction)
    result = await review.run(scope.deps, split, settings, scope.incoming.policy)
    if result.outcome is None:
        lines = [*result.parsed.skipped, *result.parsed.notes] or ["no alert rules, monitors or alarms were found"]
        return {"reply": prompts.NOT_PARSED + "\n" + "\n".join(f"- {render.safe(line)}" for line in lines)}
    outcome = result.outcome
    if clip_note:
        outcome.not_checked.append(clip_note)
    env = envelope.build(outcome)
    alerts = [{"id": a.id, "name": a.name, "file": a.file, "line": a.line, "snippet": review.snippet(result.parsed, a)}
              for a in result.parsed.alerts]
    reply = render.as_json(env) if scope.incoming.wants_json else render.render(env)
    return {"reply": _with_notes(reply, scope.incoming.notes), "data": env, "envelope": env, "alerts": alerts,
            "files": result.parsed.files}


async def run_followup(state: dict, scope: Scope) -> dict:
    route, env = scope.route, state.get("envelope") or {}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if route.kind == "question" and not env:
        return await run_question(state, scope)
    if route.kind == "view":
        view = route.command.view
        if scope.incoming.wants_json:
            return {"reply": render.as_json(_filtered(env, view)), "data": env}
        return {"reply": render.render(env, view), "data": env}
    if route.kind == "config":
        return {"reply": _config_reply(state, route.command.alert)}
    if route.kind == "alert_question":
        alert = next(a for a in state.get("alerts") or () if a["name"] == route.alert)
        findings = [f for f in env.get("findings") or () if f["target"] == alert["name"]]
        data = f"{alert['file']}:{alert['line']}\n{alert['snippet']}\n\nFindings:\n" + "\n".join(
            f"{f['rule_id']} ({f['severity']}): {f['message']}" for f in findings)
        return {"reply": await _ask(scope, data)}
    return {"reply": await _ask(scope, _review_data(state))}


async def _ask(scope: Scope, data: str) -> str:
    status, reply = await followup.ask(scope.deps, scope.incoming.text, data, scope.incoming.policy)
    if status == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if status == "needs_config":
        return hitl.ASK
    return reply


async def run_question(state: dict, scope: Scope) -> dict:
    """No stored review: one call that tells a scope question from an off-topic one."""
    status, reply = await followup.ask(scope.deps, scope.incoming.text, "(no alert configuration pasted yet)",
                                       scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "failed":
        return {"reply": reply}
    pending = hitl.Pending(instruction=scope.incoming.text[:detect.INSTRUCTION_MAX_CHARS],
                           wants_json=scope.incoming.wants_json)
    return {"pause": pending.to_metadata()}


def _review_data(state: dict) -> str:
    env = state.get("envelope") or {}
    rows = [f"{f['target']} ({f['file']}:{f['line']}) {f['rule_id']} {f['severity']}: {f['message']} | "
            f"{f['evidence']}" for f in env.get("findings") or ()]
    return f"{env.get('summary', '')}\n" + "\n".join(rows)


def _config_reply(state: dict, name: str) -> str:
    alert = next((a for a in state.get("alerts") or () if a["name"].casefold() == name.casefold()), None)
    if alert is None:
        return prompts.UNKNOWN_ALERT.format(name=name.replace("`", ""))
    return f"{render.code(alert['name'])} ({alert['file']}:{alert['line']}):\n\n{render.block(alert['snippet'])}"


def _filtered(env: dict, view: render.View) -> dict:
    items = render.filtered(env, view)
    return {**env, "items": items, "findings": items}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
