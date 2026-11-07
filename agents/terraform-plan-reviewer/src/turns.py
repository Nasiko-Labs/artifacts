"""What each graph node does in one turn. Pure routing in `intake`; the model is called only by `review`
(intent check + narrate) and `ask` (one scoped call)."""

from __future__ import annotations

import envelope
import followup
import narrate
import prompts
import render
import review
import smalltalk
import verdict
from changes import Finding
from inputs import Incoming, split_request
from llm import TurnDeps

LONG_TEXT_CHARS = 500
LONG_TEXT_LINES = 8


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming


def intake(state: dict, scope: Scope) -> dict:
    incoming = scope.incoming
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return {"route": "", "reply": _with_notes(canned, incoming.notes)}
    request = split_request(incoming.text, incoming.files)
    stored = state.get("review")
    if request.plan_text.strip():
        return {"route": "review"}
    text = incoming.text.strip()
    if stored:
        if verdict.is_confirmation(text) and not text.endswith("?"):
            return {"route": "confirm"}
        if followup.parse_view(text, stored["addresses"]) is not None:
            return {"route": "rerender"}
        return {"route": "ask"}
    if len(text) >= LONG_TEXT_CHARS or len(text.splitlines()) >= LONG_TEXT_LINES:
        return {"route": "", "reply": _with_notes(f"{prompts.NOT_A_PLAN} {smalltalk.INTRO}", incoming.notes)}
    return {"route": "ask"}


async def run_review(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    request = split_request(scope.incoming.text, scope.incoming.files)
    result = await review.run(scope.deps, request, scope.incoming.policy)
    if isinstance(result, review.Failed):
        fixed = prompts.JSON_CUT_OFF if result.kind == "json_cut_off" else f"{prompts.NOT_A_PLAN} {smalltalk.INTRO}"
        return {"reply": _with_notes(fixed, scope.incoming.notes)}
    built = envelope.build(result)
    text = render.render_json(built) if scope.incoming.wants_json else render.render(built)
    return {"review": result, "reply": _with_notes(text, scope.incoming.notes), "data": built}


def rerender(state: dict, scope: Scope) -> dict:
    stored = state["review"]
    view = followup.parse_view(scope.incoming.text, stored["addresses"]) or followup.View()
    built = envelope.build(stored)
    if scope.incoming.wants_json:
        return {"reply": render.render_json(built), "data": built}
    return {"reply": render.render(built, view.view, view.evidence, view.address), "data": built}


def confirm(state: dict, scope: Scope) -> dict:
    stored = state["review"]
    findings = [Finding.from_dict(f) for f in stored["findings"]]
    chosen = verdict.confirm(scope.incoming.text, findings)
    if not chosen.addresses and not chosen.ask:
        return {"reply": "Nothing in this review is blocked, so there is nothing to confirm."}
    if chosen.ask:
        listed = "\n".join(f"- `{a}`" for a in chosen.ask)
        return {"reply": "More than one change is blocked. Name the ones you mean, for example "
                         f"\"I confirm destroying {chosen.ask[0]}\", or say \"I confirm all\":\n{listed}"}
    updated = verdict.apply_confirmation(findings, chosen.addresses)
    new_state = {**stored, "findings": [f.to_dict() for f in updated], "summary": ""}
    built = envelope.build(new_state)
    note = (f"Confirmed by you: {', '.join(f'`{a}`' for a in chosen.addresses)}. A confirmed destroy stays a "
            "risk, so the verdict can go to review, never safe; exposure findings are never cleared this way.")
    body = render.render_json(built) if scope.incoming.wants_json else render.render(built)
    return {"review": new_state, "reply": f"{note}\n\n{body}", "data": built}


async def ask(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    stored = state.get("review")
    digest = review.digest(stored) if stored else None
    status, answer = await narrate.ask(scope.deps, scope.incoming.text, digest, scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if status == "need_plan":
        return {"reply": f"{prompts.NEED_PLAN} {smalltalk.INTRO}"}
    return {"reply": answer}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
