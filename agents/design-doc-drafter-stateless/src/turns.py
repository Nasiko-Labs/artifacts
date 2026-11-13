"""What each route does with this turn's input and what `recall` found in the chat history. Nothing is stored:
a follow-up runs the drafting crew again on the recalled requirements (or, when they were trimmed, on the
requirements table of our last draft) with the user's request in it, then applies what is left in code."""

from __future__ import annotations

import followup as followup_mod
import intake as intake_mod
import pipeline
import prompts
import smalltalk
from finish import finish
from inputs import Incoming, split_request
from llm import LlmError, TurnDeps, UnusableAnswer
from recall import Prior, recall
from repair import Check, kickoff_checked

SCOPE_STATUSES = ("off_topic", "design_request")
SCOPE_MAX_TOKENS = 200


class Scope:
    """One turn's input, dependencies and recalled history (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.prior: Prior = recall(incoming.history)
        self.route: intake_mod.Route | None = None


def intake(scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, scope.prior)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "replies": [route.reply]}
    if not scope.deps.traceparent:
        return {"route": "", "replies": [prompts.NO_TRACEPARENT]}
    return {"route": route.kind}


def _done(drafted: pipeline.Drafted, scope: Scope, notes: tuple[str, ...] = ()) -> dict:
    if drafted.status == "off_topic":
        return {"replies": [smalltalk.OFF_TOPIC_REPLY]}
    if drafted.status == "empty":
        return {"replies": [prompts.NO_REQUIREMENTS]}
    if drafted.status == "unusable":
        return {"replies": [prompts.MODEL_UNUSABLE]}
    if drafted.status != "ok" or drafted.doc is None:
        return {"replies": [prompts.MODEL_FAILED]}
    done = finish(drafted.doc, scope.incoming.wants_json, notes)
    return {"data": done.envelope, "replies": list(done.replies)}


async def draft(scope: Scope) -> dict:
    drafted = await pipeline.draft(scope.deps, scope.route.request, scope.incoming.policy)
    return _done(drafted, scope)


def _instruction(command: followup_mod.Command) -> str:
    if command.kind == "answers":
        return followup_mod.revise_instruction(command.answers)
    return command.text if command.kind == "revise" else ""


async def followup(scope: Scope) -> dict:
    command = scope.route.command
    source = scope.prior.source or scope.prior.draft_reqs
    if command.kind == "add":
        source = f"{source}\n{command.text}"
    drafted = await pipeline.draft(scope.deps, split_request(source), scope.incoming.policy, _instruction(command))
    if drafted.status != "ok":
        return _done(drafted, scope)
    update = _apply(scope, drafted.doc)
    if scope.prior.source or not update.get("replies") or update["replies"][0].startswith("```"):
        return update
    return {**update, "replies": [*update["replies"][:-1], f"{update['replies'][-1]}\n\n{prompts.FROM_DRAFT_NOTE}"]}


def _apply(scope: Scope, doc: dict) -> dict:
    command = scope.route.command
    if command.kind == "json":
        done = finish(doc, True)
        return {"replies": list(done.replies), "data": done.envelope}
    if command.kind == "answers":
        recalled = scope.prior.questions
        seed = followup_mod.seed_numbers(finish(doc, False).doc.get("questions") or [], recalled)
        numbered = finish({**doc, "qnums": seed}, False).doc
        open_qs = numbered.get("questions") or []
        known = [*open_qs, *({"id": f"Q{n}"} for _, n in recalled)]
        unknown = followup_mod.unknown_ids(command.answers, known)
        if unknown:
            listed = ", ".join(dict.fromkeys(q["id"] for q in known)) or "none"
            return {"replies": [prompts.NO_SUCH_QUESTION.format(ids=", ".join(unknown), open=listed)]}
        answered = followup_mod.record_answers(numbered, command.answers, open_qs)
        closed = ", ".join(qid for qid, _ in command.answers)
        return _done(pipeline.Drafted("ok", answered), scope, (f"_Updated with your answers; closed {closed}._",))
    if command.kind == "add":
        return _done(pipeline.Drafted("ok", doc), scope, ("_Requirements added; coverage and questions recomputed._",))
    return _done(pipeline.Drafted("ok", doc), scope, ("_Revised as asked._",))


async def scope_check(scope: Scope) -> dict:
    try:
        (status,) = await kickoff_checked(scope.deps, [prompts.scope_step(scope.incoming.text, scope.incoming.policy)],
                                          [Check(_scope_status)], max_tokens=SCOPE_MAX_TOKENS)
    except UnusableAnswer:
        return {"replies": [prompts.MODEL_UNUSABLE]}
    except LlmError:
        return {"replies": [prompts.MODEL_FAILED]}
    if status == "off_topic":
        return {"replies": [smalltalk.OFF_TOPIC_REPLY]}
    return {"replies": [prompts.NEED_REPASTE if scope.incoming.had_history else prompts.ASK_REQUIREMENTS]}


def _scope_status(data) -> str | None:
    status = str(data.get("status") or "").casefold() if isinstance(data, dict) else ""
    return status if status in SCOPE_STATUSES else None
